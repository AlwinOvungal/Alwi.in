import numpy as np
import pytest
import trimesh

from formstudio.core import primitives as pr


def part(kind, size, pos, rot=(0, 0, 0), op="add", r=4.0, taper=1.0, label="p"):
    return pr.Part(label=label, kind=kind, op=op, size=list(size), position=list(pos),
                   rotation_deg=list(rot), roundness=r, taper=taper)


MUG = pr.ShapeRecipe(subject="mug", notes="body, opening, handle", symmetry="none", parts=[
    part("cylinder", (60, 80, 60), (0, 40, 0), r=8, label="body"),
    part("cylinder", (48, 80, 48), (0, 52, 0), op="subtract", r=6, label="opening"),
    part("torus", (40, 10, 40), (32, 42, 0), rot=(90, 0, 0), r=0, label="handle"),
])


@pytest.mark.parametrize("kind", ["rounded_box", "capsule", "sphere", "cylinder", "cone", "torus"])
def test_each_primitive_is_a_valid_solid_of_the_right_size(kind):
    m = pr.build_part(part(kind, (40, 30, 20), (5, 15, 0)))
    lo, hi = np.array(m.bounding_box()[:3]), np.array(m.bounding_box()[3:])
    assert m.volume() > 0
    assert hi[0] - lo[0] == pytest.approx(40, abs=0.5)
    assert (lo + hi)[0] / 2 == pytest.approx(5, abs=0.5)


def test_mug_recipe_builds_a_watertight_mug_with_a_handle_hole(tmp_path):
    info = pr.build_to_glb(MUG, tmp_path / "raw.glb")
    mesh = trimesh.load(tmp_path / "raw.glb", force="mesh")
    assert mesh.is_watertight
    assert info["parts"] == 3
    assert mesh.extents[1] == pytest.approx(80, abs=1)  # height along y (glTF up)
    assert pr.build(MUG).genus() == 1  # the handle's hole


def test_same_recipe_rebuilds_identically():
    a = pr.build(MUG).to_mesh()
    b = pr.build(pr.ShapeRecipe.model_validate_json(MUG.model_dump_json())).to_mesh()
    assert np.array_equal(np.asarray(a.vert_properties), np.asarray(b.vert_properties))


def test_mirror_and_bad_recipes():
    half = pr.ShapeRecipe(subject="x", notes="", symmetry="mirror_x", parts=[
        part("sphere", (30, 30, 30), (0, 15, 0), r=0), part("capsule", (10, 30, 10), (20, 20, 0))])
    bb = pr.build(half).bounding_box()
    assert bb[0] == pytest.approx(-bb[3], abs=0.5)  # symmetric
    with pytest.raises(ValueError):
        pr.build(pr.ShapeRecipe(subject="x", notes="", symmetry="none",
                                parts=[part("sphere", (10, 10, 10), (0, 0, 0), op="subtract")]))
    # silly numbers are clamped rather than crashing
    pr.build(pr.ShapeRecipe(subject="x", notes="", symmetry="none",
                            parts=[part("rounded_box", (0, -5, 9999), (0, 0, 0), r=500)]))
