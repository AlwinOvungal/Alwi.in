"""Stage 6: print-ready meshes -> slicer files.

- STL: body.stl and base.stl (already written by printify_to_files).
- 3MF: one object made of two parts, body on filament 1 and base on filament 2,
  using the Bambu Studio / OrcaSlicer `Metadata/model_settings.config` layout,
  with the palette colours as named base materials.
- STEP (optional, needs the [step] extra): a faceted B-rep. An organic mesh
  becomes triangles, not true CAD surfaces. STL and 3MF are the main formats.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import numpy as np
import trimesh

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="config" ContentType="application/vnd.bambulab.config+xml"/>
</Types>
"""

_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""


def _hex_colour(value: str) -> str:
    """'#5E6B2E' -> '#5E6B2EFF' (3MF displaycolor is RGBA)."""
    v = value.lstrip("#").upper()
    if len(v) == 6:
        v += "FF"
    return "#" + v


def _mesh_xml(mesh: trimesh.Trimesh, object_id: int, material_id: int | None = None) -> str:
    verts = "".join(
        f'<vertex x="{x:.5f}" y="{y:.5f}" z="{z:.5f}"/>' for x, y, z in mesh.vertices
    )
    tris = "".join(
        f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in np.asarray(mesh.faces)
    )
    pid = f' pid="{material_id}" pindex="0"' if material_id is not None else ""
    return (
        f'<object id="{object_id}" type="model"{pid}><mesh>'
        f"<vertices>{verts}</vertices><triangles>{tris}</triangles>"
        f"</mesh></object>"
    )


def write_3mf(
    body: trimesh.Trimesh,
    base: trimesh.Trimesh,
    path: Path,
    palette: dict[str, str],
    name: str = "Form Studio object",
) -> Path:
    """Write a two-part, two-filament 3MF.

    Layout:
      object 1 = body mesh, object 2 = base mesh (sub-objects, material-coloured)
      object 3 = parent that references 1 and 2 (what the build item places)
      model_settings.config: parts 1 and 2 of object 3 are on extruders 1 and 2.
    """
    body_col = _hex_colour(palette["body"])
    base_col = _hex_colour(palette["base"])
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
        "<resources>\n"
        f'<basematerials id="1">'
        f'<base name={quoteattr("Body " + palette["body"])} displaycolor="{body_col}"/>'
        f'<base name={quoteattr("Base " + palette["base"])} displaycolor="{base_col}"/>'
        "</basematerials>\n"
        f"{_mesh_xml(body, 1, material_id=1)}\n"
        f"{_mesh_xml(base, 2, material_id=1)}\n"
        '<object id="3" type="model"><components>'
        '<component objectid="1"/><component objectid="2"/>'
        "</components></object>\n"
        "</resources>\n"
        '<build><item objectid="3"/></build>\n'
        "</model>\n"
    )
    settings = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<config>\n"
        '  <object id="3">\n'
        f'    <metadata key="name" value={quoteattr(name)}/>\n'
        '    <part id="1" subtype="normal_part">\n'
        '      <metadata key="name" value="body"/>\n'
        '      <metadata key="extruder" value="1"/>\n'
        "    </part>\n"
        '    <part id="2" subtype="normal_part">\n'
        '      <metadata key="name" value="base"/>\n'
        '      <metadata key="extruder" value="2"/>\n'
        "    </part>\n"
        "  </object>\n"
        "</config>\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("3D/3dmodel.model", model)
        z.writestr("Metadata/model_settings.config", settings)
    return path


def write_stl(mesh: trimesh.Trimesh, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(path)
    return path


def write_step(body: trimesh.Trimesh, base: trimesh.Trimesh, path: Path) -> Path:
    """Faceted STEP via OCP (cadquery-ocp). Each triangle becomes a planar face,
    sewn into a shell per part. Large meshes produce large files."""
    try:
        from OCP.BRepBuilderAPI import (
            BRepBuilderAPI_MakeFace,
            BRepBuilderAPI_MakePolygon,
            BRepBuilderAPI_MakeSolid,
            BRepBuilderAPI_Sewing,
        )
        from OCP.gp import gp_Pnt
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
        from OCP.TopoDS import TopoDS
    except ImportError as exc:
        raise RuntimeError(
            "STEP export needs the optional extra: pip install -e '.[step]'"
        ) from exc

    def solid_from(mesh: trimesh.Trimesh):
        sewing = BRepBuilderAPI_Sewing(1e-3)
        verts = mesh.vertices
        for a, b, c in mesh.faces:
            pts = [gp_Pnt(*verts[i]) for i in (a, b, c)]
            poly = BRepBuilderAPI_MakePolygon(pts[0], pts[1], pts[2], True)
            sewing.Add(BRepBuilderAPI_MakeFace(poly.Wire()).Face())
        sewing.Perform()
        shell = TopoDS.Shell_s(sewing.SewedShape())
        return BRepBuilderAPI_MakeSolid(shell).Solid()

    writer = STEPControl_Writer()
    writer.Transfer(solid_from(body), STEPControl_AsIs)
    writer.Transfer(solid_from(base), STEPControl_AsIs)
    path.parent.mkdir(parents=True, exist_ok=True)
    if writer.Write(str(path)) != IFSelect_RetDone:
        raise RuntimeError(f"STEP write failed: {path}")
    return path


def export_all(
    body: trimesh.Trimesh,
    base: trimesh.Trimesh,
    out_dir: Path,
    palette: dict[str, str],
    step: bool = False,
    name: str = "Form Studio object",
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "body.stl": write_stl(body, out_dir / "body.stl"),
        "base.stl": write_stl(base, out_dir / "base.stl"),
        "model.3mf": write_3mf(body, base, out_dir / "model.3mf", palette, name=name),
    }
    if step:
        files["model.step"] = write_step(body, base, out_dir / "model.step")
    return files
