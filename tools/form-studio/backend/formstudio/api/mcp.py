"""Form Studio as an MCP server, so ChatGPT and Claude can make printable things for you.

Mounted into the app's own server at /mcp/<secret>/ (streamable HTTP). ChatGPT's connectors can
only reach public HTTPS addresses, so to use it there the local server is shared through a tunnel
(see "Connect ChatGPT" in Settings). The secret in the path means only you can use it.
"""
from __future__ import annotations

import base64
import binascii
import logging
import tempfile
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MAX_IMAGE_MB = 24
WHAT = ("stand", "magnet", "keychain", "ornament")

try:
    from mcp.server.mcpserver import Image, MCPServer
    from mcp.server.transport_security import TransportSecuritySettings

    READY = True
except ImportError:  # the extra isn't installed: the app runs fine without MCP
    READY = False


def _api():
    from . import app as api  # late import: app.py imports this module first

    return api


def _snapshot(job_id: str) -> dict[str, Any]:
    """What a chat assistant needs to know about an object, in one small dict."""
    api = _api()
    from ..core.job import Job

    j = api._view(Job.load(job_id))
    task = j.get("task") or {}
    return {
        "object_id": j["id"],
        "name": j["name"],
        "kind": j.get("kind") or "object",
        "stage": j["stage"],
        "stage_meaning": {"cutout": "photo ready", "stylized": "style options made", "confirmed": "style picked",
                          "reconstructed": "3D shape built", "printified": "print files ready to export",
                          "exported": "files exported"}.get(j["stage"], j["stage"]),
        "working": task.get("status") in ("queued", "running"),
        "progress": task.get("progress"),
        "message": task.get("message"),
        "error": task.get("error"),
        "format": (j.get("print_params") or {}).get("format"),
        "parts": [{"part": p["id"], "colour": p["name"], "hex": p["hex"], "filament": p["filament"]}
                  for p in j.get("parts") or []],
        "size_mm": (j.get("report") or {}).get("size_mm"),
        "likeness_pct": ((j.get("report") or {}).get("fidelity") or {}).get("score"),
        "changes_for_printing": [c["text"] for c in (j.get("report") or {}).get("changes") or []],
        "options": [{"option": i + 1, **({"refined_from": c["parent"], "asked": c.get("message")} if c.get("parent") else {})}
                    for i, c in enumerate(j.get("candidates") or [])],
        "exports": j.get("exports") or [],
    }


def _wait(job_id: str, wait_s: float) -> dict[str, Any]:
    end = time.monotonic() + max(0.0, min(wait_s, 570))  # ChatGPT gives a tool about 10 minutes
    snap = _snapshot(job_id)
    while snap["working"] and time.monotonic() < end:
        time.sleep(1.0)
        snap = _snapshot(job_id)
    return snap


def _save_image(image_url: str, image_base64: str, image_path: str) -> Path:
    """The picture to build from, whichever way the assistant can pass it."""
    import httpx

    if sum(map(bool, (image_url, image_base64, image_path))) != 1:
        raise ValueError("Give exactly one of image_url, image_base64 or image_path.")
    suffix = ".png"
    if image_path:
        p = Path(image_path).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"No file at {image_path}")
        return p
    if image_url:
        with httpx.Client(timeout=60, follow_redirects=True) as c:
            r = c.get(image_url)
            r.raise_for_status()
            data = r.content
        ct = r.headers.get("content-type", "")
        for t, ext in (("jpeg", ".jpg"), ("jpg", ".jpg"), ("webp", ".webp"), ("png", ".png")):
            if t in ct:
                suffix = ext
                break
    else:
        try:
            data = base64.b64decode(image_base64.split(",")[-1], validate=True)  # allow a data: URI
        except binascii.Error as e:
            raise ValueError(f"image_base64 isn't valid base64: {e}")
    if len(data) > MAX_IMAGE_MB * 1024 * 1024:
        raise ValueError(f"The picture is over {MAX_IMAGE_MB} MB. Send a smaller copy.")
    if not data[:4] in (b"\x89PNG", b"RIFF") and not data[:2] == b"\xff\xd8":
        raise ValueError("That doesn't look like a PNG, JPEG or WebP picture.")
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    f.write(data)
    f.close()
    return Path(f.name)


def build() -> "MCPServer":
    srv = MCPServer(
        "Form Studio",
        instructions=(
            "Form Studio turns a picture into a 3D-printable figurine, fridge magnet, keychain or ornament on this "
            "person's Mac. make_object starts the whole journey and waits; the result says what was made, "
            "its parts and their filament colours. Flat artwork (logos, text) is built exactly as drawn. "
            "Files stay on the Mac; export_print_files tells the person where they are."
        ),
        version="0.1.0",
    )

    @srv.tool(description="Turn a picture into a printable object: a stand (figurine), fridge magnet, keychain or "
                          "ornament. "
                          "Pass the picture one way: image_url (preferred), image_base64, or image_path (a file "
                          "already on this Mac). Starts the whole journey and waits up to wait_s seconds; if it's "
                          "still working, check again with object_status. Returns the object's parts and colours.")
    def make_object(image_url: str = "", image_base64: str = "", image_path: str = "",
                    what: str = "magnet", subject: str = "", style: str = "", wait_s: float = 240) -> dict[str, Any]:
        api = _api()
        from .. import pipeline
        from ..core import style as style_mod

        if what not in WHAT:
            raise ValueError(f"what must be one of {', '.join(WHAT)}")
        photo = _save_image(image_url, image_base64, image_path)
        try:
            job = pipeline.create(photo, subject or photo.stem, subject or photo.stem,
                                  style or style_mod.default_style_id())
        finally:
            if not image_path:
                photo.unlink(missing_ok=True)
        if not subject:
            suggested = pipeline.suggest_name(api.settings_mod.context(), job)
            if suggested:
                job.update(subject=suggested, name=suggested)
        api.start_auto(job, what)
        return _wait(job.id, wait_s)

    @srv.tool(description="How an object is doing. wait_s > 0 waits for it to finish (up to that many seconds).")
    def object_status(object_id: str, wait_s: float = 0) -> dict[str, Any]:
        return _wait(object_id, wait_s)

    @srv.tool(description="The objects already made, newest first.")
    def list_objects() -> list[dict[str, Any]]:
        from ..core.job import Job

        return [{"object_id": j.id, "name": j.manifest["name"], "stage": j.manifest["stage"],
                 "kind": j.manifest.get("kind") or "object",
                 "format": (j.manifest.get("print_params") or {}).get("format")}
                for j in Job.all() if not j.manifest.get("bench")]

    @srv.tool(description="The house styles an object can be made in. Pass a style id to make_object.")
    def list_styles() -> list[dict[str, Any]]:
        from ..core.style import list_styles

        return [{"style_id": s.id, "name": s.name, "look": s.raw.get("description") or s.name} for s in list_styles()]

    @srv.tool(description="Change one part's filament colour, e.g. part 'body', 'base', 'logo_1' or 'plaque' "
                          "(object_status lists them) to a hex colour like #D94F3C. Exports are redone after this.")
    def set_part_colour(object_id: str, part: str, colour_hex: str) -> dict[str, Any]:
        api = _api()
        from .. import pipeline
        from ..core.job import Job

        with api._job_locks[object_id]:
            pipeline.set_colours(Job.load(object_id), {part: {"hex": colour_hex}})
        return _snapshot(object_id)

    @srv.tool(description="Reduce the picture to fewer colours (cheaper, fewer filament changes), before restyling. "
                          "enabled=false keeps every colour (the default). max_colours 2–8. colours: optional new hex "
                          "per colour, in order (null keeps one); restore=true puts back the picture's own. Returns "
                          "the palette; the reduced preview is the object's picture until a look is chosen.")
    def set_palette(object_id: str, enabled: bool, max_colours: int | None = None,
                    colours: list[str | None] | None = None, restore: bool = False) -> dict[str, Any]:
        api = _api()
        from .. import pipeline
        from ..core.job import Job

        with api._job_locks[object_id]:
            pal = pipeline.set_palette(Job.load(object_id), enabled, max_colours, colours, restore)
        return {**_snapshot(object_id), "palette": pal}

    @srv.tool(description="Design advice for one restyled option (option = its number in object_status, from 1): "
                          "what would print badly at the chosen size and type, each with a ready-made change to ask for.")
    def design_advice(object_id: str, option: int) -> list[dict[str, Any]]:
        from .. import pipeline
        from ..core.job import Job

        return pipeline.advice(Job.load(object_id), option - 1)

    @srv.tool(description="Change one restyled option by asking in words, e.g. 'make the trunk thicker'. The result is "
                          "a new option linked to it (needs an OpenAI or fal.ai key in Form Studio). Waits up to wait_s.")
    def refine_look(object_id: str, option: int, message: str, wait_s: float = 180) -> dict[str, Any]:
        api = _api()
        from .. import pipeline
        from ..core.job import Job

        job = Job.load(object_id)
        ctx = api.settings_mod.context()
        if not pipeline.refine_engine(job, ctx, option - 1):
            raise ValueError("Refining in words needs an OpenAI or fal.ai key in Form Studio's Settings.")
        api._queue(job, "stylize", lambda p: pipeline.refine(job, ctx, option - 1, message, p,
                                                             on_candidate=lambda: api._publish_state(job)))
        return _wait(object_id, wait_s)

    @srv.tool(description="Write the print files (3mf for multi-colour printing, stl, step) and say where they "
                          "are on this Mac, ready for the slicer.")
    def export_print_files(object_id: str, formats: list[str] | None = None) -> dict[str, Any]:
        api = _api()
        from .. import pipeline
        from ..core.job import Job

        with api._job_locks[object_id]:
            job = Job.load(object_id)
            written = pipeline.export(job, formats or ["3mf"])
        files = [{"file": str(job.path(w)), "bytes": job.path(w).stat().st_size} for w in written]
        return {**_snapshot(object_id), "files": files}

    @srv.tool(description="A picture of the object as it stands (the chosen style, or the photo).")
    def preview_image(object_id: str) -> Image:
        api = _api()
        from ..core.job import Job

        j = api._view(Job.load(object_id))
        for name in (j.get("thumbnail"), "03_confirmed.png", "01_cutout.png"):
            if name and Job.load(object_id).path(name).exists():
                return Image(path=Job.load(object_id).path(name))
        raise FileNotFoundError("This object has no picture yet.")

    return srv


class TokenGate:
    """Only requests that carry the secret path reach the MCP app: /mcp/<token>/…

    The server also answers from behind a tunnel, where everything arrives as localhost,
    so the path is the one thing that proves it's you.
    """

    def __init__(self, inner: Any = None) -> None:
        self.inner = inner  # set while the app is running (the MCP app is rebuilt on every start)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return  # the MCP app's lifespan is driven by the main app, not by the mount
        if self.inner is None:
            await send({"type": "http.response.start", "status": 503, "headers": [(b"content-type", b"text/plain")]})
            return await send({"type": "http.response.body", "body": b"Starting up; try again in a moment."})
        from . import settings as settings_mod

        token = (settings_mod.load().get("mcp") or {}).get("token") or ""
        root = scope.get("root_path") or ""  # the mount prefix (/mcp); the path still carries it
        full = scope.get("path") or "/"
        rel = full[len(root):] if root and full.startswith(root) else full
        want = "/" + token
        if token and (rel == want or rel.startswith(want + "/")):
            scope = dict(scope)
            scope["path"] = root + (rel[len(want):] or "/")
            scope["raw_path"] = scope["path"].encode()
            return await self.inner(scope, receive, send)
        await send({"type": "http.response.start", "status": 404, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"Not found. The MCP address includes a secret: see Settings > Connect ChatGPT."})


def asgi_app():
    """The ASGI app to mount at /mcp, and a lifespan factory to run inside the main app's.

    The streamable-HTTP session manager can only be started once, so a fresh MCP app is built on
    every start of the main app (tests start it many times); the gate always points at the live one.
    """
    from contextlib import asynccontextmanager

    gate = TokenGate()

    @asynccontextmanager
    async def lifespan():
        srv = build()
        inner = srv.streamable_http_app(
            streamable_http_path="/",
            stateless_http=True,
            json_response=True,  # plain JSON answers: what ChatGPT's connectors expect, and easy to test
            max_request_body_size=MAX_IMAGE_MB * 1024 * 1024 * 2,  # base64 grows a picture by a third
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),  # tunnel hosts vary
        )
        async with inner.router.lifespan_context(inner):
            gate.inner = inner
            try:
                yield
            finally:
                gate.inner = None

    return gate, lifespan
