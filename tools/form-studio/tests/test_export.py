import dataclasses
import xml.etree.ElementTree as ET
import zipfile

import pytest
import trimesh

from formstudio.export import export_all, write_3mf
from formstudio.printify import printify
from formstudio.style import load_style

NS = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}


@pytest.fixture(scope="module")
def parts():
    spec = dataclasses.replace(load_style("clay-minimal"), smooth_iters=2, target_faces=20000)
    raw = trimesh.creation.cylinder(radius=0.5, height=1.0, sections=48)
    raw.apply_translation([0, 0.5, 0])
    res = printify(raw, spec)
    return res.body, res.base, spec.palette


def test_3mf_has_two_parts_on_two_filaments(tmp_path, parts):
    body, base, palette = parts
    path = write_3mf(body, base, tmp_path / "model.3mf", palette)
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        assert {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model",
                "Metadata/model_settings.config"} <= names
        model = ET.fromstring(z.read("3D/3dmodel.model"))
        settings = ET.fromstring(z.read("Metadata/model_settings.config"))

    # two mesh objects plus the parent, placed once by the build item
    objects = model.findall(".//m:resources/m:object", NS)
    meshes = [o for o in objects if o.find("m:mesh", NS) is not None]
    assert len(meshes) == 2
    assert model.find("m:build/m:item", NS).get("objectid") == "3"
    tri_counts = [len(o.findall("m:mesh/m:triangles/m:triangle", NS)) for o in meshes]
    assert tri_counts == [len(body.faces), len(base.faces)]

    # filament assignment: body on 1, base on 2
    parts_xml = settings.findall("object/part")
    extruders = {p.get("id"): p.find("metadata[@key='extruder']").get("value") for p in parts_xml}
    assert extruders == {"1": "1", "2": "2"}

    # colours from the palette, as named base materials
    colours = [b.get("displaycolor") for b in model.findall(".//m:basematerials/m:base", NS)]
    assert colours == ["#5E6B2EFF", "#F2F0EAFF"]


def test_export_all_writes_stl_and_3mf(tmp_path, parts):
    body, base, palette = parts
    files = export_all(body, base, tmp_path, palette)
    assert set(files) == {"body.stl", "base.stl", "model.3mf"}
    reloaded = trimesh.load(files["body.stl"], force="mesh")
    assert reloaded.is_watertight
    assert len(reloaded.faces) == len(body.faces)


def test_step_without_ocp_gives_install_hint(tmp_path, parts, monkeypatch):
    import builtins

    body, base, _ = parts
    real_import = builtins.__import__

    def no_ocp(name, *a, **k):
        if name.startswith("OCP"):
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_ocp)
    from formstudio.export import write_step

    with pytest.raises(RuntimeError, match=r"pip install -e '\.\[step\]'"):
        write_step(body, base, tmp_path / "model.step")
