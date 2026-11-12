"""Step 6: print files.

model.3mf is one object made of several parts (body + base, or each logo colour + the plaque). Each part gets a
filament slot in Metadata/model_settings.config, the file Bambu Studio and
OrcaSlicer read, so the object opens ready to print in its colours. Colours
are also written as 3MF base materials so other slicers show them.
"""
from __future__ import annotations

import json

import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import numpy as np
import trimesh

BED_CENTRE = (110.0, 110.0)  # on the bed of any printer ≥ 220 mm; slicers can re-centre

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="config" ContentType="text/xml"/>
</Types>
"""

RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""

IDENTITY = "1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"


@dataclass
class Part:
    name: str
    mesh: trimesh.Trimesh
    extruder: int  # 1-based filament slot
    colour_name: str
    colour_hex: str  # "#RRGGBB"


def _mesh_xml(m: trimesh.Trimesh) -> str:
    v = np.asarray(m.vertices, dtype=np.float64)
    f = np.asarray(m.faces, dtype=np.int64)
    verts = "\n".join(f'     <vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>' for x, y, z in v)
    tris = "\n".join(f'     <triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in f)
    return f"   <mesh>\n    <vertices>\n{verts}\n    </vertices>\n    <triangles>\n{tris}\n    </triangles>\n   </mesh>"


def write_3mf(parts: list[Part], path: Path, title: str) -> None:
    mat_id = len(parts) + 2
    obj_id = len(parts) + 1
    mats = "\n".join(
        f"   <base name={quoteattr(p.colour_name)} displaycolor=\"{p.colour_hex.upper()}FF\"/>" for p in parts
    )
    objects = "\n".join(
        f'  <object id="{i + 1}" type="model" pid="{mat_id}" pindex="{i}" name={quoteattr(p.name)}>\n'
        f"{_mesh_xml(p.mesh)}\n  </object>"
        for i, p in enumerate(parts)
    )
    comps = "\n".join(f'    <component objectid="{i + 1}"/>' for i in range(len(parts)))
    tx, ty = BED_CENTRE
    model = f"""<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
 <metadata name="Title">{escape(title)}</metadata>
 <metadata name="Application">Form Studio</metadata>
 <resources>
  <basematerials id="{mat_id}">
{mats}
  </basematerials>
{objects}
  <object id="{obj_id}" type="model" name={quoteattr(title)}>
   <components>
{comps}
   </components>
  </object>
 </resources>
 <build>
  <item objectid="{obj_id}" transform="1 0 0 0 1 0 0 0 1 {tx} {ty} 0"/>
 </build>
</model>
"""
    part_cfg = "\n".join(
        f'    <part id="{i + 1}" subtype="normal_part">\n'
        f'      <metadata key="name" value={quoteattr(p.name)}/>\n'
        f'      <metadata key="matrix" value="{IDENTITY}"/>\n'
        f'      <metadata key="extruder" value="{p.extruder}"/>\n'
        f"    </part>"
        for i, p in enumerate(parts)
    )
    settings = f"""<?xml version="1.0" encoding="UTF-8"?>
<config>
  <object id="{obj_id}">
    <metadata key="name" value={quoteattr(title)}/>
    <metadata key="extruder" value="{parts[0].extruder}"/>
{part_cfg}
  </object>
</config>
"""
    # PrusaSlicer reads volumes as triangle ranges of the merged object
    ranges, start = [], 0
    for p in parts:
        ranges.append((start, start + len(p.mesh.faces) - 1, p))
        start += len(p.mesh.faces)
    prusa = "\n".join(
        f'  <volume firstid="{a}" lastid="{b}">\n'
        f'   <metadata type="volume" key="name" value={quoteattr(p.name)}/>\n'
        f'   <metadata type="volume" key="extruder" value="{p.extruder}"/>\n'
        f"  </volume>"
        for a, b, p in ranges
    )
    prusa_cfg = f"""<?xml version="1.0" encoding="UTF-8"?>
<config>
 <object id="{obj_id}" instances_count="1">
  <metadata type="object" key="name" value={quoteattr(title)}/>
{prusa}
 </object>
</config>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("3D/3dmodel.model", model)
        z.writestr("Metadata/model_settings.config", settings)
        z.writestr("Metadata/Slic3r_PE_model.config", prusa_cfg)


def write_step(parts: list[Part], path: Path, max_faces: int = 12000) -> None:
    """Faceted STEP (triangles, not CAD surfaces). Needs the optional `cadquery-ocp` package."""
    try:
        from OCP.BRep import BRep_Builder
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace, BRepBuilderAPI_Sewing
        from OCP.gp import gp_Pnt
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
        from OCP.TopoDS import TopoDS_Compound
    except ImportError as e:
        raise RuntimeError("STEP export needs the optional 'step' extra: pip install -e 'backend[step]'") from e

    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for p in parts:
        m = p.mesh
        if len(m.faces) > max_faces:
            try:
                m = m.simplify_quadric_decimation(face_count=max_faces)
            except Exception:
                pass
        sew = BRepBuilderAPI_Sewing(1e-3)
        for tri in m.triangles:
            poly = BRepBuilderAPI_MakePolygon(*(gp_Pnt(*map(float, v)) for v in tri), True)
            face = BRepBuilderAPI_MakeFace(poly.Wire(), True)
            if face.IsDone():
                sew.Add(face.Face())
        sew.Perform()
        builder.Add(compound, sew.SewedShape())
    writer = STEPControl_Writer()
    writer.Transfer(compound, STEPControl_AsIs)
    if writer.Write(str(path)) != IFSelect_RetDone:
        raise RuntimeError("STEP writer failed")


def export(print_dir: Path, title: str, palette: dict, formats: list[str]) -> list[str]:
    """The 3MF / STEP of every part, each part on its own filament (1, 2, 3, …) with its colour.

    Parts come from parts.json (logos: each logo colour and the plaque), else body.stl and base.stl.
    `palette` maps part id → {name, hex}.
    """
    if (print_dir / "parts.json").exists():
        listed = json.loads((print_dir / "parts.json").read_text())
    else:
        listed = [{"id": "body", "file": "body.stl"}, {"id": "base", "file": "base.stl"}]
    parts: list[Part] = []
    written: list[str] = []
    for p in listed:
        f = print_dir / p["file"]
        if not f.exists():
            continue
        c = palette.get(p["id"]) or {"name": "Grey", "hex": "#8A8A85"}
        parts.append(Part(p["id"], trimesh.load(f, force="mesh"), len(parts) + 1, c["name"], c["hex"]))
        written.append(p["file"])
    if not parts:
        raise FileNotFoundError("The print files are missing. Prepare the print again.")
    if "3mf" in formats:
        write_3mf(parts, print_dir / "model.3mf", title)
        written.append("model.3mf")
    if "step" in formats:
        write_step(parts, print_dir / "model.step")
        written.append("model.step")
    return written
