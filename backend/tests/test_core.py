from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hitlcad.core.events import BuildStarted, DesignOpened, DesignState, DesignUpdated, ErrorRaised, PinsChanged
from hitlcad.core.types import ParamSpec, Pin, apply_values
from hitlcad.store.fs import FileDesignStore

from .conftest import brief


class TestParamSpec:
    def test_coerces_and_bounds(self) -> None:
        p = ParamSpec(name="wall", value=2.0, min=0.5, max=5)
        assert p.coerce(3) == 3.0
        with pytest.raises(ValueError, match="below minimum"):
            p.coerce(0.1)
        with pytest.raises(ValueError, match="expected a number"):
            p.coerce(True)

    def test_int_choice_bool(self) -> None:
        assert ParamSpec(name="n", type="int", value=3).coerce(4.0) == 4
        c = ParamSpec(name="style", type="choice", value="a", choices=["a", "b"])
        assert c.coerce("b") == "b"
        with pytest.raises(ValueError):
            c.coerce("z")
        assert ParamSpec(name="flag", type="bool", value=False).coerce(True) is True

    def test_inconsistent_specs(self) -> None:
        with pytest.raises(ValueError):
            ParamSpec(name="x", value=1.0, min=5, max=1)
        with pytest.raises(ValueError):
            ParamSpec(name="x", type="choice", value="c", choices=["a"])

    def test_apply_values(self) -> None:
        specs = [ParamSpec(name="a", value=1.0), ParamSpec(name="b", value=2.0)]
        out = apply_values(specs, {"b": 5})
        assert [p.value for p in out] == [1.0, 5.0]
        assert specs[1].value == 2.0  # originals untouched
        with pytest.raises(ValueError, match="unknown"):
            apply_values(specs, {"c": 1})


class TestStoreAndProjection:
    def test_append_assigns_sequence_and_replays(self, tmp_path: Path) -> None:
        store = FileDesignStore(tmp_path)
        store.create("dsg_a")
        e1 = store.append("dsg_a", DesignOpened(path="/d/box", name="Box", backend="fake"))
        store.append("dsg_a", DesignUpdated(name="Box", backend="fake", brief=brief()))
        store.append("dsg_a", BuildStarted(backend="fake", origin="file"))
        e4 = store.append(
            "dsg_a", PinsChanged(pins=[Pin(point=(1, 2, 3), note="thicker"), Pin(point=(0, 0, 0), resolved=True)])
        )
        assert (e1.seq, e4.seq) == (1, 4)

        # a fresh store instance continues the sequence from disk
        again = FileDesignStore(tmp_path)
        assert again.append("dsg_a", ErrorRaised(message="bad json")).seq == 5

        state = DesignState.replay("dsg_a", again.events("dsg_a"))
        assert state.path == "/d/box" and state.brief and state.brief.title == "Test box"
        assert [p.note for p in state.open_pins] == ["thicker"]
        assert not state.building and state.error == "bad json"
        assert [e.seq for e in again.events("dsg_a", after=3)] == [4, 5]

    def test_rejects_path_like_ids(self, tmp_path: Path) -> None:
        store = FileDesignStore(tmp_path)
        with pytest.raises(ValueError):
            store.create("../evil")
        assert not store.exists("../evil")


async def test_bus_fans_out_to_design_and_global_subscribers() -> None:
    from hitlcad.service.bus import EventBus

    bus = EventBus()
    one, every = bus.subscribe("dsg_a"), bus.subscribe_all()
    event = ErrorRaised(message="x")
    bus.publish("dsg_a", event)
    bus.publish("dsg_b", event)
    assert one.qsize() == 1 and [every.get_nowait()[0], every.get_nowait()[0]] == ["dsg_a", "dsg_b"]
    bus.unsubscribe_all(every)
    bus.publish("dsg_a", event)
    assert every.empty()


def test_agents_hear_when_a_pin_was_not_found_in_the_latest_build() -> None:
    from hitlcad.describe import pin_lines

    pin = Pin(point=(1, 2, 3), note="thicker", label="Planar face (face 2 of model) at (1.00, 2.00, 3.00)", revision=1)
    assert pin_lines([pin], latest=1) == [
        f"- {pin.id} Planar face (face 2 of model) at (1.00, 2.00, 3.00), revision 1: thicker"
    ]
    assert "revision 1; not found in revision 3, the latest build" in pin_lines([pin], latest=3)[0]


def test_an_open_mesh_is_measured_as_open() -> None:
    import numpy as np
    import trimesh

    from hitlcad.analysis.mesh import measure

    box = trimesh.creation.box((10, 10, 10))
    box.update_faces(np.arange(len(box.faces)) != 0)  # one triangle missing
    m = measure(box)
    assert not m.watertight and m.volume is None and m.bodies == 1 and m.size == (10, 10, 10)


def test_fdm_checks_find_thin_walls_overhangs_and_small_bed_contact() -> None:
    import trimesh

    from hitlcad.analysis.assembly import Part
    from hitlcad.analysis.mesh import measure
    from hitlcad.core.types import FdmProcess

    def checks(mesh: trimesh.Trimesh) -> dict[str, Any]:
        return {c.name: c for c in FdmProcess(process="fdm").checks(Part("model", mesh, measure(mesh), None))}

    def resting(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
        mesh.apply_translation((0, 0, -mesh.bounds[0][2]))
        return mesh

    block = checks(resting(trimesh.creation.box((20, 20, 10))))
    assert all(c.passed and c.severity == "warning" for c in block.values()) and len(block) == 3

    plate = checks(resting(trimesh.creation.box((20, 20, 0.5))))["fdm_wall_thickness"]
    assert not plate.passed and "thinnest wall 0.50 mm" in plate.detail and "nozzle" not in plate.detail
    fin = checks(resting(trimesh.creation.box((20, 20, 0.3))))["fdm_wall_thickness"]
    assert "thinner than the 0.4 mm nozzle" in fin.detail

    post = trimesh.creation.box((4, 4, 10))
    post.apply_translation((0, 0, 5))
    top = trimesh.creation.box((40, 40, 2))
    top.apply_translation((0, 0, 11))
    table = checks(trimesh.util.concatenate([post, top]))
    assert not table["fdm_overhangs"].passed and table["fdm_overhangs"].detail.startswith("1600 mm² overhangs")
    assert not table["fdm_bed_contact"].passed and "16 mm² on the bed" in table["fdm_bed_contact"].detail
    gentle = checks(resting(trimesh.creation.cone(10, 20)))  # its sides lean 63° from horizontal: printable
    assert gentle["fdm_overhangs"].passed


def test_requirement_checks_are_validated() -> None:
    from pydantic import ValidationError

    from hitlcad.core.types import Requirement

    assert Requirement(text="looks friendly").check is None
    for bad, reason in [
        ({"kind": "size", "axis": "z"}, "give min, max or both"),
        ({"kind": "volume", "min": 10, "max": 5}, "min is greater than max"),
        ({"kind": "hole", "diameter": 0}, "greater than 0"),
        ({"kind": "colour", "value": "red"}, "does not match any of the expected tags"),
    ]:
        with pytest.raises(ValidationError, match=reason):
            Requirement.model_validate({"text": "x", "check": bad})


def test_every_check_and_process_example_is_valid_and_in_the_skill() -> None:
    from pydantic import TypeAdapter

    from hitlcad import skills
    from hitlcad.core.types import MANUFACTURING_PROCESSES, REQUIREMENT_CHECKS, Manufacturing, Requirement

    guide = skills.render([])["hitlcad"]["SKILL.md"]
    for check in REQUIREMENT_CHECKS:
        assert isinstance(Requirement.model_validate({"text": "x", "check": check.example}).check, check)
        assert json.dumps(check.example) in guide
    for process in MANUFACTURING_PROCESSES:
        assert isinstance(TypeAdapter(Manufacturing).validate_python(process.example), process)
        assert json.dumps(process.example) in guide
