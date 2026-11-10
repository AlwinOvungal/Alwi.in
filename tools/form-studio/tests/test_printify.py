import dataclasses
import math

import numpy as np
import pytest
import trimesh

from formstudio.printify import (
    PrintabilityError,
    overhang_fraction,
    parse_magnet,
    printify,
    rounded_plinth,
    wall_thickness_ok,
)
from formstudio.style import load_style


@pytest.fixture(scope="module")
def spec():
    return load_style("clay-minimal")


@pytest.fixture(scope="module")
def fast_spec(spec):
    # Same geometry rules, fewer smoothing passes and faces, for speed.
    return dataclasses.replace(spec, smooth_iters=2, target_faces=20000)


@pytest.fixture(scope="module")
def printed(spec, cylinder_y_up):
    return printify(cylinder_y_up, spec)


def test_body_and_base_are_watertight(printed):
    assert printed.body.is_watertight
    assert printed.base.is_watertight
    assert printed.report["watertight"] is True


def test_base_sits_flat_on_z0(printed, spec):
    assert printed.base.bounds[0, 2] == pytest.approx(0.0, abs=1e-6)
    assert printed.base.extents[2] == pytest.approx(spec.base_height_mm, abs=1e-3)


def test_overall_height_matches_spec(printed, spec):
    assert printed.body.bounds[1, 2] == pytest.approx(spec.height_mm, abs=1e-2)
    assert printed.body.bounds[0, 2] == pytest.approx(spec.base_height_mm, abs=1e-3)


def test_body_is_centred_and_base_wider(printed):
    lo, hi = printed.body.bounds
    assert (lo[0] + hi[0]) / 2 == pytest.approx(0, abs=0.05)
    assert printed.base.extents[0] > printed.body.extents[0]


def test_face_budget_respected(printed, spec):
    assert len(printed.body.faces) <= spec.target_faces * 1.05


def test_thin_plate_fails_wall_check():
    plate = trimesh.creation.box(extents=[40, 40, 0.5])
    with pytest.raises(PrintabilityError, match="thinner than"):
        wall_thickness_ok(plate, min_wall=1.2)


def test_solid_block_passes_wall_check():
    block = trimesh.creation.box(extents=[20, 20, 20])
    assert wall_thickness_ok(block, min_wall=1.2) < 0.01


def test_overhang_of_a_box_is_only_its_side_faces_and_bed():
    box = trimesh.creation.box(extents=[10, 10, 10])
    box.apply_translation([0, 0, 5])
    assert overhang_fraction(box) == pytest.approx(0.0, abs=1e-6)
    # an upside-down overhang ledge is reported
    ledge = trimesh.creation.box(extents=[10, 10, 2])
    ledge.apply_translation([0, 0, 20])
    tower = trimesh.util.concatenate([trimesh.creation.box(extents=[4, 4, 20]), ledge])
    tower.apply_translation([0, 0, 10])
    assert overhang_fraction(tower) > 0


def test_rounded_plinth_is_watertight_and_sized():
    p = rounded_plinth(radius=30, height=6, fillet=1.5)
    assert p.is_watertight
    assert p.extents[0] == pytest.approx(60, abs=0.2)
    assert p.extents[2] == pytest.approx(6, abs=1e-3)
    assert p.bounds[0, 2] == pytest.approx(0)


def test_parse_magnet():
    assert parse_magnet("6x2") == (6.0, 2.0)
    assert parse_magnet(None) is None


def test_magnet_pocket_reduces_base_volume(fast_spec, cylinder_y_up):
    plain = printify(cylinder_y_up, fast_spec)
    with_magnet = printify(cylinder_y_up, dataclasses.replace(fast_spec, magnet="6x2"))
    assert with_magnet.base.is_watertight
    assert with_magnet.base.volume < plain.base.volume
    expected = math.pi * 3.1**2 * 2
    assert plain.base.volume - with_magnet.base.volume == pytest.approx(expected, rel=0.1)
