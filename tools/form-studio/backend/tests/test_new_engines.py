"""The newer 3D engines: Hunyuan3D 3.1, Tripo H3.1, TRELLIS 2 and Meshy 6 on fal.ai (shape only, the cheapest
tier), and TripoSG on this Mac (with a stand-in for its CUDA-only mesh extractor)."""
import json
import sys
import textwrap

import httpx
import numpy as np
import pytest
from PIL import Image

from formstudio import models, pipeline
from formstudio.api import settings as settings_mod
from formstudio.engines import RECONSTRUCTORS, EngineContext, describe, local
from formstudio.engines.base import EngineError, ReconstructInput
from formstudio.engines.cloud import view_uris


class FakeFal:
    def __init__(self, result, file: bytes):
        self.result, self.file, self.posts = result, file, []

    def __call__(self, request: httpx.Request):
        url = str(request.url)
        if request.method == "POST":
            self.posts.append((url, json.loads(request.content)))
            return httpx.Response(200, json={"status_url": "https://queue.fal.run/s", "response_url": "https://queue.fal.run/r"})
        if url.endswith("/s?logs=1"):
            return httpx.Response(200, json={"status": "COMPLETED"})
        if url == "https://queue.fal.run/r":
            return httpx.Response(200, json=self.result)
        self.downloaded = url
        return httpx.Response(200, content=self.file)


def _ctx(fake, **fal):
    return EngineContext(settings={"_fal_transport": httpx.MockTransport(fake), "fal": fal}, secrets={"fal": "fal-test"})


def _inp(tmp_path, views=None):
    img, mask = tmp_path / "c.png", tmp_path / "c_mask.png"
    Image.new("RGB", (64, 64), (200, 40, 30)).save(img)
    Image.new("L", (64, 64), 255).save(mask)
    return ReconstructInput(img, mask, 7, tmp_path / "raw.glb", views=views)


def _views(tmp_path):
    out = {}
    for name, colour in (("front", (200, 40, 30)), ("left", (40, 160, 60)), ("back", (40, 70, 200))):
        img, mask = tmp_path / f"{name}.png", tmp_path / f"{name}_mask.png"
        Image.new("RGB", (64, 64), colour).save(img)
        Image.new("L", (64, 64), 255).save(mask)
        out[name] = (img, mask)
    return out


RESULTS = {  # how each model names its file
    "fal-hunyuan3": {"model_glb": {"url": "https://cdn.fal/h.glb"}, "model_urls": {"glb": {"url": "https://cdn.fal/h2.glb"}}},
    "fal-tripo": {"model_mesh": {"url": "https://cdn.fal/textured.glb"},
                  "model_urls": {"base_model": {"url": "https://cdn.fal/base.glb"}, "glb": {"url": "https://cdn.fal/t.glb"}}},
    "fal-trellis2": {"model_glb": {"url": "https://cdn.fal/t2.glb"}},
    "fal-meshy": {"model_glb": {"url": "https://cdn.fal/m.glb"}, "model_urls": {"stl": {"url": "https://cdn.fal/m.stl"}}},
}


@pytest.mark.parametrize("eid,endpoint,shape_only", [
    ("fal-hunyuan3", "fal-ai/hunyuan-3d/v3.1/rapid/image-to-3d", {"generate_type": "Geometry"}),
    ("fal-tripo", "tripo3d/h3.1/image-to-3d", {"texture": False, "pbr": False, "geometry_quality": "standard"}),
    ("fal-trellis2", "fal-ai/trellis-2", {"resolution": 1024}),
    ("fal-meshy", "fal-ai/meshy/v6/image-to-3d", {"should_texture": False}),
])
def test_each_engine_asks_for_the_shape_only(tmp_path, mug_glb, eid, endpoint, shape_only):
    fake = FakeFal(RESULTS[eid], mug_glb.read_bytes())
    info = RECONSTRUCTORS[eid].reconstruct(_inp(tmp_path), _ctx(fake), lambda p, m: None)
    url, body = fake.posts[0]
    assert url.endswith("/" + endpoint) and info["endpoint"] == endpoint
    assert shape_only.items() <= body.items()
    image = body.get("image_url") or body.get("input_image_url")
    assert image.startswith("data:image/png;base64,")
    assert (tmp_path / "raw.glb").read_bytes() == mug_glb.read_bytes()
    assert not (tmp_path / "04_upload.png").exists()  # the temporary upload is cleaned up
    if eid == "fal-tripo":
        assert fake.downloaded == "https://cdn.fal/base.glb"  # the untextured model, not the textured one


def test_settings_pick_quality_detail_and_resolution(tmp_path, mug_glb):
    fake = FakeFal(RESULTS["fal-hunyuan3"], mug_glb.read_bytes())
    RECONSTRUCTORS["fal-hunyuan3"].reconstruct(_inp(tmp_path), _ctx(fake, hunyuan3_quality="pro"), lambda p, m: None)
    assert fake.posts[0][0].endswith("/fal-ai/hunyuan-3d/v3.1/pro/image-to-3d")

    fake = FakeFal(RESULTS["fal-tripo"], mug_glb.read_bytes())
    RECONSTRUCTORS["fal-tripo"].reconstruct(_inp(tmp_path), _ctx(fake, tripo_detail="detailed"), lambda p, m: None)
    assert fake.posts[0][1]["geometry_quality"] == "detailed"

    for res, want in ((512, 512), (1536, 1536), (777, 1024)):
        fake = FakeFal(RESULTS["fal-trellis2"], mug_glb.read_bytes())
        RECONSTRUCTORS["fal-trellis2"].reconstruct(_inp(tmp_path), _ctx(fake, trellis2_resolution=res), lambda p, m: None)
        assert fake.posts[0][1]["resolution"] == want

    fake = FakeFal(RESULTS["fal-trellis2"], mug_glb.read_bytes())  # Advanced: a custom endpoint still wins
    ctx = _ctx(fake, endpoints={"fal-trellis2": "fal-ai/trellis-2/beta"})
    RECONSTRUCTORS["fal-trellis2"].reconstruct(_inp(tmp_path), ctx, lambda p, m: None)
    assert fake.posts[0][0].endswith("/fal-ai/trellis-2/beta")


def test_views_go_to_hunyuan_pro_and_tripo_multiview(tmp_path, mug_glb):
    views = _views(tmp_path)
    fake = FakeFal(RESULTS["fal-hunyuan3"], mug_glb.read_bytes())
    info = RECONSTRUCTORS["fal-hunyuan3"].reconstruct(_inp(tmp_path, views), _ctx(fake), lambda p, m: None)
    url, body = fake.posts[0]
    assert url.endswith("/v3.1/pro/image-to-3d") and info["views"] == 3
    assert {"input_image_url", "back_image_url", "left_image_url"} <= body.keys() and body["generate_type"] == "Geometry"

    fake = FakeFal(RESULTS["fal-tripo"], mug_glb.read_bytes())
    inp = _inp(tmp_path, views)
    info = RECONSTRUCTORS["fal-tripo"].reconstruct(inp, _ctx(fake), lambda p, m: None)
    url, body = fake.posts[0]
    assert url.endswith("/tripo3d/h3.1/multiview-to-3d") and info["views"] == 3
    want = view_uris(inp)
    assert body["image_urls"] == [want["front"], want["left"], want["back"]] and body["texture"] is False


def test_no_model_in_the_answer_is_a_clear_error(tmp_path, mug_glb):
    fake = FakeFal({"seed": 3}, mug_glb.read_bytes())
    with pytest.raises(EngineError, match="no 3D model"):
        RECONSTRUCTORS["fal-trellis2"].reconstruct(_inp(tmp_path), _ctx(fake), lambda p, m: None)


def test_new_engines_listed_and_older_ones_marked():
    ctx = EngineContext(settings={}, secrets={"fal": "k"})
    for eid in ("fal-hunyuan3", "fal-tripo", "fal-trellis2", "fal-meshy"):
        d = describe(RECONSTRUCTORS[eid], ctx)
        assert d["available"] and d["kind"] == "cloud" and not d["legacy"] and "$" in d["note"]
    for eid in ("fal-trellis", "fal-hunyuan"):
        assert describe(RECONSTRUCTORS[eid], ctx)["legacy"]
    assert not describe(RECONSTRUCTORS["fal-hunyuan3"], EngineContext(settings={}, secrets={}))["available"]
    order = pipeline.AUTO_ORDER["reconstruct"]
    assert order.index("fal-hunyuan3") < order.index("fal-trellis") and "triposg" in order


def test_saved_old_fal_default_moves_to_hunyuan3():
    raw = settings_mod._migrate({"engines": {"reconstruct": "fal-trellis"}})
    assert raw["engines"]["reconstruct"] == "fal-hunyuan3"
    assert settings_mod._migrate({"engines": {"reconstruct": "triposr"}})["engines"]["reconstruct"] == "triposr"
    s = settings_mod.DEFAULT_SETTINGS["fal"]
    assert s["hunyuan3_quality"] == "rapid" and s["tripo_detail"] == "standard" and s["trellis2_resolution"] == 1024


# ---- TripoSG ---------------------------------------------------------------------------------

def test_diso_stand_in_makes_a_closed_surface():
    import torch
    import trimesh

    n = 48
    ax = np.linspace(-1, 1, n)
    x, y, z = np.meshgrid(ax, ax, ax, indexing="ij")
    sdf = np.sqrt(x**2 + y**2 + z**2) - 0.6  # < 0 inside, as TripoSG passes it
    sdf[:4] = np.nan  # cells TripoSG never evaluated
    v, f = local._DiffDMC(dtype=torch.float32).to("mps")(torch.from_numpy(sdf).float(), deform=None,
                                                          return_quads=False, normalize=False)
    f = f.numpy()[:, ::-1]  # what TripoSG does next
    mesh = trimesh.Trimesh(v.numpy(), f)
    assert mesh.is_watertight and mesh.volume > 0  # outward-facing after TripoSG's flip
    assert abs(mesh.extents[0] - 0.6 * 2 * (n - 1) / 2) < 2  # grid units, sphere of radius 0.6


FAKE_PIPELINE = textwrap.dedent('''
    import numpy as np
    import torch
    from diso import DiffDMC  # the stand-in, as the real file imports it


    class _Out:
        def __init__(self, samples):
            self.samples = samples


    class TripoSGPipeline:
        calls = []

        @classmethod
        def from_pretrained(cls, repo):
            assert repo == "VAST-AI/TripoSG"
            return cls()

        def to(self, device, dtype):
            self.device, self.dtype = device, dtype
            return self

        def __call__(self, image, generator, num_inference_steps, guidance_scale):
            TripoSGPipeline.calls.append(image.size)
            n = 40
            ax = np.linspace(-1, 1, n)
            x, y, z = np.meshgrid(ax, ax, ax, indexing="ij")
            sdf = np.maximum(np.maximum(abs(x), abs(y)), abs(z)) - 0.5
            sdf = np.minimum(sdf, np.sqrt((x - 0.85) ** 2 + y**2 + z**2) - 0.08)  # a floater to drop
            v, f = DiffDMC(dtype=torch.float32).to(self.device)(torch.from_numpy(sdf).float(), deform=None,
                                                                return_quads=False, normalize=False)
            v = v.numpy() / n * 2 - 1
            return _Out([(v.astype(np.float32), np.ascontiguousarray(f.numpy()[:, ::-1]))])
''')


def test_triposg_builds_a_clean_mesh_with_the_stand_ins(tmp_path, monkeypatch):
    import trimesh

    vendor = tmp_path / "vendor" / "TripoSG"
    pkg = vendor / "triposg" / "pipelines"
    pkg.mkdir(parents=True)
    (vendor / "triposg" / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text("")
    (pkg / "pipeline_triposg.py").write_text(FAKE_PIPELINE)
    monkeypatch.setattr(local, "triposg_dir", lambda: vendor)
    monkeypatch.setattr(local, "_ensure_models", lambda *a, **k: None)
    monkeypatch.delitem(sys.modules, "diso", raising=False)
    for m in [m for m in sys.modules if m == "triposg" or m.startswith("triposg.")]:
        monkeypatch.delitem(sys.modules, m)
    try:
        eng = RECONSTRUCTORS["triposg"]
        inp = _inp(tmp_path)
        info = eng._reconstruct(inp, EngineContext(settings={}, device="cpu"), lambda p, m: None)
        mesh = trimesh.load(inp.out, force="mesh")
        assert mesh.is_watertight and mesh.volume > 0 and info["model"] == "VAST-AI/TripoSG"
        assert abs(max(mesh.extents) - 1.0) < 0.15  # the cube, without the floater
        assert sys.modules["triposg.pipelines.pipeline_triposg"].TripoSGPipeline.calls == [(512, 512)]
    finally:
        local.unload_all()
        sys.path[:] = [p for p in sys.path if p != str(vendor)]
        for m in [m for m in sys.modules if m == "triposg" or m.startswith("triposg.")]:
            sys.modules.pop(m, None)


def test_triposg_packs_and_setup():
    assert models.packs_needed({"engines": {"reconstruct": "triposg"}}) == ["matting", "triposg-code", "triposg"]
    assert pipeline.LOCAL_PACKS["triposg"] == ["triposg-code", "triposg"]
    assert models.PACKS["triposg"]["repos"]["VAST-AI/TripoSG"]
    assert models._vendor_dir("triposg-code").name == "TripoSG"
    assert models.status("triposg-code")["status"] in ("missing", "partial", "ready")
