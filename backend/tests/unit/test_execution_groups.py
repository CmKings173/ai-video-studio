from types import SimpleNamespace

import pytest

from apps.api.app.core.errors import AppError
from apps.api.app.schemas.api import GenerationRequest
from apps.api.app.services.continuity_groups import expand_continuity_selection
from apps.api.app.services.execution_groups import plan_execution_groups


def scene(index, continuity="CUT"):
    return SimpleNamespace(
        id=str(index), scene_order=index, enabled=True, spec={"continuity": continuity}
    )


@pytest.mark.parametrize("stale", [0, 1, 2])
def test_stale_member_expands_entire_native_chain_but_not_separate_cut(stale):
    scenes = [scene(0), scene(1, "CONTINUOUS"), scene(2, "CONTINUOUS"), scene(3)]
    assert [row.id for row in expand_continuity_selection([scenes[stale]], scenes)] == [
        "0",
        "1",
        "2",
    ]


def test_cut_splits_and_continuous_chain_stays_ordered():
    scenes = [scene(0), scene(1, "CONTINUOUS"), scene(2), scene(3)]
    groups = plan_execution_groups(scenes[::-1], scenes, [GenerationRequest()] * 4)
    assert [[s.id for s in group.scenes] for group in groups] == [["0", "1"], ["2"], ["3"]]
    assert [group.execution_scope for group in groups] == [
        "aggregate",
        "single_scene",
        "single_scene",
    ]


def test_motion_context_single_member_uses_aggregate():
    scenes = [scene(0)]
    groups = plan_execution_groups(
        scenes, scenes, [GenerationRequest(motion_context={"enabled": True})]
    )
    assert groups[0].execution_scope == "aggregate"


def test_missing_predecessor_fails_before_planning():
    scenes = [scene(0), scene(1, "CONTINUOUS")]
    with pytest.raises(AppError) as error:
        plan_execution_groups([scenes[1]], scenes, [GenerationRequest()])
    assert error.value.code == "DIRECTOR_CONTINUITY_PREDECESSOR_REQUIRED"


def test_invalid_continuity_is_rejected():
    scenes = [scene(0, "INVALID")]
    with pytest.raises(AppError) as error:
        plan_execution_groups(scenes, scenes, [GenerationRequest()])
    assert error.value.code == "DIRECTOR_GROUP_INVALID"


@pytest.mark.parametrize(
    "boundaries,enabled,orders,expected",
    [
        (["CUT", "CONTINUOUS"], [False, True], [0, 1], [["1"]]),
        (["CONTINUOUS", "CONTINUOUS"], [False, True], [0, 1], [["1"]]),
        (
            ["CUT", "CONTINUOUS", "CONTINUOUS", "CONTINUOUS"],
            [True, False, True, True],
            [0, 1, 2, 3],
            [["0"], ["2", "3"]],
        ),
        (["CONTINUOUS"], [True], [5], [["5"]]),
        (["CUT", "CONTINUOUS"], [True, True], [0, 2], [["0"], ["2"]]),
        (["CUT", "CONTINUOUS"], [False, False], [0, 1], []),
    ],
)
def test_execution_groups_use_canonical_enabled_boundaries(boundaries, enabled, orders, expected):
    from apps.api.app.services.continuity_groups import continuity_chains

    rows = [scene(order, boundary) for order, boundary in zip(orders, boundaries, strict=True)]
    for row, active in zip(rows, enabled, strict=True):
        row.enabled = active
    selected = [row for row in rows if row.enabled]
    assert [[row.id for row in chain] for chain in continuity_chains(rows)] == expected
    groups = plan_execution_groups(selected, rows, [GenerationRequest()] * len(selected))
    assert [[row.id for row in group.scenes] for group in groups] == expected
    assert [group.execution_scope for group in groups] == [
        "aggregate" if len(chain) > 1 else "single_scene" for chain in expected
    ]


def test_disabled_scene_cannot_be_planned_explicitly():
    row = scene(0)
    row.enabled = False
    with pytest.raises(AppError) as caught:
        plan_execution_groups([row], [row], [GenerationRequest()])
    assert caught.value.code == "SCENE_SELECTION_INVALID"


def test_partial_chain_leader_is_not_silently_downgraded():
    rows = [scene(0), scene(1, "CONTINUOUS")]
    with pytest.raises(AppError) as caught:
        plan_execution_groups([rows[0]], rows, [GenerationRequest()])
    assert caught.value.code == "DIRECTOR_CONTINUITY_CHAIN_REQUIRED"
    assert caught.value.details["required_scene_ids"] == ["0", "1"]
