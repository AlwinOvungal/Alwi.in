"""ChatGPT and Claude drive Form Studio over MCP (/mcp/<secret>/, streamable HTTP, JSON answers)."""
import base64
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from formstudio.api.app import app

HEADERS = {"content-type": "application/json", "accept": "application/json, text/event-stream"}


def _rpc(c, token, method, params=None, id_=1):
    r = c.post(f"/mcp/{token}", headers=HEADERS,
               json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}})
    assert r.status_code == 200, r.text
    out = r.json()
    assert "error" not in out, out
    return out["result"]


def _call(c, token, name, args):
    res = _rpc(c, token, "tools/call", {"name": name, "arguments": args})
    assert not res.get("isError"), res
    return res.get("structuredContent") or json.loads(res["content"][0]["text"])


@pytest.fixture()
def logo_b64():
    img = Image.new("RGB", (900, 300), "white")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((60, 60, 300, 240), 40, fill=(238, 46, 36))
    d.ellipse((400, 80, 700, 220), fill=(15, 15, 15))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def test_mcp_makes_a_magnet_from_a_picture(logo_b64):
    with TestClient(app) as c:
        token = c.get("/api/settings").json()["mcp"]["token"]
        assert token  # made at start-up and kept in settings.json

        # the address is secret: without it there's no MCP
        r = c.post("/mcp/wrong-token", headers=HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 404

        tools = {t["name"] for t in _rpc(c, token, "tools/list")["tools"]}
        assert {"make_object", "object_status", "list_objects", "list_styles",
                "set_part_colour", "export_print_files", "preview_image"} <= tools

        styles = _call(c, token, "list_styles", {})
        assert any(s["style_id"] == "soft-clay" for s in styles["result"]) if "result" in styles else True

        made = _call(c, token, "make_object",
                     {"image_base64": logo_b64, "what": "magnet", "subject": "marks", "wait_s": 240})
        oid = made["object_id"]
        assert made["stage"] == "printified" and not made["working"], made
        assert made["format"] == "magnet"
        assert made["kind"] == "logo" and [p["part"] for p in made["parts"]] == ["logo_1", "logo_2", "plaque"]

        snap = _call(c, token, "set_part_colour", {"object_id": oid, "part": "plaque", "colour_hex": "#FFFFFF"})
        assert any(p["part"] == "plaque" and p["hex"] == "#FFFFFF" for p in snap["parts"])

        out = _call(c, token, "export_print_files", {"object_id": oid, "formats": ["3mf"]})
        assert out["files"] and out["files"][0]["bytes"] > 0 and out["files"][0]["file"].endswith((".3mf", ".stl"))

        listed = _call(c, token, "list_objects", {})
        rows = listed["result"] if "result" in listed else listed
        assert any(row["object_id"] == oid for row in rows)

        r = c.post(f"/mcp/{token}", headers=HEADERS, json={
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": "preview_image", "arguments": {"object_id": oid}}})
        body = r.json()
        kinds = [x.get("type") for x in body["result"]["content"]]
        assert "image" in kinds

        c.delete(f"/api/jobs/{oid}")
