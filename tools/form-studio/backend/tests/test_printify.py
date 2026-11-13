import zipfile
from xml.etree import ElementTree as ET

import pytest
import trimesh

from formstudio.core import export, printify
from formstudio.core.style import get_style

NS = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}


@pytest.fixture(scope="module")
def style():
    return get_style("clay-minimal")


@pytest.fixture(scope="module")
def printed(style, mug_glb, tmp_path_factory):
    out = tmp_path_factory.mktemp("print")
    params = style.print_params()
    params["magnet"]["enabled"] = True
    report = printify.run(mug_glb, out, params)
    return out, report, params


def test_parts_are_watertight_and_sized(printed):
    out, report, params = printed
    body = trimesh.load(out / "body.stl")
    base = trimesh.load(out / "base.stl")
    assert body.is_watertight and base.is_watertight
    assert report["watertight"]
    total = max(body.bounds[1][2], base.bounds[1][2]) - min(body.bounds[0][2], base.bounds[0][2])
    assert total == pytest.approx(params["geometry"]["height_mm"], abs=0.1)


def test_flat_on_the_bed(printed):
    out, _, params = printed
    base = trimesh.load(out / "base.stl")
    body = trimesh.load(out / "body.stl")
    assert base.bounds[0][2] == pytest.approx(0, abs=1e-3)
    # body sits in its pocket, slightly below the top of the base
    t = params["base"]["thickness_mm"]
    assert body.bounds[0][2] == pytest.approx(t - printify.SINK_MM, abs=0.05)
    # the base is wider than the body
    assert base.extents[0] > body.extents[0]


def test_parts_do_not_overlap(printed):
    out, _, _ = printed
    body = printify.to_manifold(trimesh.load(out / "body.stl"))
    base = printify.to_manifold(trimesh.load(out / "base.stl"))
    assert (body ^ base).volume() < 1.0  # mm³


def test_magnet_pocket_cut(style, mug_glb, tmp_path):
    p = style.print_params()
    without = printify.run(mug_glb, tmp_path / "a", p)
    p["magnet"]["enabled"] = True
    with_m = printify.run(mug_glb, tmp_path / "b", p)
    assert with_m["base_volume_cm3"] < without["base_volume_cm3"]


def test_no_base_option(style, mug_glb, tmp_path):
    p = style.print_params()
    p["base"]["enabled"] = False
    rep = printify.run(mug_glb, tmp_path, p)
    body = trimesh.load(tmp_path / "body.stl")
    assert not (tmp_path / "base.stl").exists()
    assert body.bounds[0][2] == pytest.approx(0, abs=1e-3)
    assert body.extents[2] == pytest.approx(p["geometry"]["height_mm"], abs=0.1)
    assert rep["base_volume_cm3"] == 0


def test_3mf_has_two_parts_on_two_filaments(printed, style):
    out, _, _ = printed
    pal = {"body": {"name": "Olive", "hex": "#5E6B2E"}, "base": {"name": "Bone", "hex": "#F2F0EA"}}  # the Print step's
    written = export.export(out, "Coffee mug", pal, ["3mf"])
    assert "model.3mf" in written
    with zipfile.ZipFile(out / "model.3mf") as z:
        names = set(z.namelist())
        assert {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model", "Metadata/model_settings.config"} <= names
        model = ET.fromstring(z.read("3D/3dmodel.model"))
        cfg = ET.fromstring(z.read("Metadata/model_settings.config"))
    meshes = model.findall(".//m:object/m:mesh", NS)
    assert len(meshes) == 2
    comps = model.findall(".//m:components/m:component", NS)
    assert [c.get("objectid") for c in comps] == ["1", "2"]
    colours = [b.get("displaycolor") for b in model.findall(".//m:basematerials/m:base", NS)]
    assert colours == [pal["body"]["hex"].upper() + "FF", pal["base"]["hex"].upper() + "FF"]
    parts = cfg.findall("./object/part")
    extruders = [p.find("./metadata[@key='extruder']").get("value") for p in parts]
    assert extruders == ["1", "2"]
    # the 3MF reloads as geometry
    loaded = trimesh.load(out / "model.3mf", force="mesh")
    assert len(loaded.faces) > 1000


def test_keychain_lies_flat_with_a_ring_hole(style, mug_glb, tmp_path):
    p = style.print_params("keychain")
    rep = printify.run(mug_glb, tmp_path, p)
    body = trimesh.load(tmp_path / "body.stl")
    assert not (tmp_path / "base.stl").exists()
    assert body.is_watertight and rep["format"] == "keychain"
    assert body.bounds[0][2] == pytest.approx(0, abs=1e-3)  # flat back on the bed
    # the object's height runs along y; the loop adds to it
    assert body.extents[1] > p["geometry"]["height_mm"]
    # the mug has two handle holes; the ring adds a third
    plain = style.print_params("keychain")
    plain["ring"]["enabled"] = False
    printify.run(mug_glb, tmp_path / "plain", plain)
    assert body.euler_number < trimesh.load(tmp_path / "plain" / "body.stl").euler_number
    # a thin pin through the hole centre doesn't touch the part
    import manifold3d as mf

    ys = body.vertices[:, 1]
    hole_x = float(body.vertices[ys > ys.max() - 0.5, 0].mean())
    hole_y = float(ys.max()) - p["ring"]["outer_radius_mm"]
    pin = mf.Manifold.cylinder(40, 1.0, 1.0, 24).translate((hole_x, hole_y, -10))
    assert (printify.to_manifold(body) ^ pin).volume() < 0.01


def test_magnet_format_has_a_pocket_on_the_back(style, mug_glb, tmp_path):
    p = style.print_params("magnet")
    with_pocket = printify.run(mug_glb, tmp_path / "a", p)
    p["magnet"]["enabled"] = False
    without = printify.run(mug_glb, tmp_path / "b", p)
    body = trimesh.load(tmp_path / "a" / "body.stl")
    assert body.is_watertight and not (tmp_path / "a" / "base.stl").exists()
    assert body.bounds[0][2] == pytest.approx(0, abs=1e-3)
    assert with_pocket["body_volume_cm3"] <= without["body_volume_cm3"]


def test_switching_format_starts_from_its_defaults(style):
    from formstudio import pipeline

    stand = style.print_params("stand")
    stand["geometry"]["height_mm"] = 80
    switched = pipeline._deep_merge(style.print_params("keychain"), {})
    assert switched["format"] == "keychain" and switched["geometry"]["height_mm"] == 40
    assert switched["ring"]["enabled"] and not switched["base"]["enabled"]
    with pytest.raises(KeyError):
        style.print_params("bookend")


def test_voxel_finish_is_blocky_and_printable(style, mug_glb, tmp_path):
    import numpy as np

    p = style.print_params()
    p["geometry"].update(finish="voxel", voxel_size_mm=2.5)
    rep = printify.run(mug_glb, tmp_path, p)
    body = trimesh.load(tmp_path / "body.stl")
    assert body.is_watertight and rep["watertight"]
    axis_aligned = (np.abs(body.face_normals).max(axis=1) > 0.99).mean()
    assert axis_aligned > 0.7  # mostly flat cube faces
    assert rep["size_mm"][2] == pytest.approx(p["geometry"]["height_mm"], abs=0.1)


def test_faceted_finish_keeps_few_faces(style, mug_glb, tmp_path):
    p = style.print_params()
    p["geometry"].update(finish="faceted", facets=400)
    printify.run(mug_glb, tmp_path, p)
    body = trimesh.load(tmp_path / "body.stl")
    assert body.is_watertight and len(body.faces) <= 3200  # a few hundred, doubled at most if needed


def test_preset_styles_load_with_their_finish():
    from formstudio.core.style import get_style

    expect = {"clay-minimal": "soft", "soft-clay": "soft", "pixel-voxel": "voxel", "low-poly": "faceted"}
    for sid, finish in expect.items():
        s = get_style(sid)
        assert s.geometry["finish"] == finish and "{subject}" in s.edit_prompt
        assert "Completely redraw" in s.edit_prompt  # a real style conversion, not a cut-out
        assert {"stand", "magnet", "keychain"} <= set(s.formats)
