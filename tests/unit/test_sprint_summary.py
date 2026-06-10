"""Unit tests for the sprint-summary reconstruction (pure functions).

These exercise the status-at-start replay, unplanned detection, and aggregation
with hand-built activity fixtures — no network.
"""

from youtrack_mcp.reporting import sprint_summary as ss


# --- fixtures ------------------------------------------------------------- #
def stage_act(ts, old, new):
    """A Stage change activity, like the Activities API returns."""
    return {
        "$type": "CustomFieldCategory",
        "timestamp": ts,
        "field": {"name": "Stage"},
        "removed": [{"name": old}] if old else [],
        "added": [{"name": new}] if new else [],
    }


def sprint_act(ts, added=None, removed=None):
    return {
        "$type": "SprintCategory",
        "timestamp": ts,
        "field": {"name": "Sprint"},
        "added": [{"name": n} for n in (added or [])],
        "removed": [{"name": n} for n in (removed or [])],
    }


SPRINT_START = 1_000_000


# --- stage_at ------------------------------------------------------------- #
def test_stage_at_uses_last_change_before_start():
    acts = [
        stage_act(SPRINT_START - 500, "Backlog", "In Progress"),
        stage_act(SPRINT_START + 500, "In Progress", "Review"),  # after start
    ]
    # At start, the last change <= start moved it to "In Progress".
    assert ss.stage_at(acts, SPRINT_START, current_stage="Review") == "In Progress"


def test_stage_at_before_any_change_uses_first_old_value():
    acts = [stage_act(SPRINT_START + 500, "Backlog", "In Progress")]
    # The only change is AFTER start, so at start it was the pre-change value.
    assert ss.stage_at(acts, SPRINT_START, current_stage="In Progress") == "Backlog"


def test_stage_at_no_changes_falls_back_to_current():
    assert ss.stage_at([], SPRINT_START, current_stage="Published") == "Published"


def test_stage_at_change_exactly_at_start_is_included():
    acts = [stage_act(SPRINT_START, "Backlog", "Test")]
    assert ss.stage_at(acts, SPRINT_START, current_stage="Test") == "Test"


def test_stage_changes_detected_via_customfield_name():
    acts = [{
        "$type": "CustomFieldCategory",
        "timestamp": 5,
        "field": {"customField": {"name": "Stage"}},
        "removed": [{"name": "A"}],
        "added": [{"name": "B"}],
    }]
    assert ss.stage_changes(acts) == [(5, "A", "B")]


# --- sprint_entry_ts / is_unplanned --------------------------------------- #
def test_entry_ts_picks_latest_add_event():
    acts = [
        sprint_act(SPRINT_START - 1000, added=["Sprint #91"]),
        sprint_act(SPRINT_START + 2000, added=["Sprint #91"]),  # re-added later
    ]
    assert ss.sprint_entry_ts(acts, "Sprint #91") == SPRINT_START + 2000


def test_entry_ts_falls_back_to_created_when_no_event():
    assert ss.sprint_entry_ts([], "Sprint #91", fallback=42) == 42


def test_unplanned_true_when_added_after_start():
    entered = ss.sprint_entry_ts(
        [sprint_act(SPRINT_START + 5000, added=["Sprint #91"])], "Sprint #91"
    )
    assert ss.is_unplanned(entered, SPRINT_START) is True


def test_planned_when_added_before_start():
    entered = ss.sprint_entry_ts(
        [sprint_act(SPRINT_START - 5000, added=["Sprint #91"])], "Sprint #91"
    )
    assert ss.is_unplanned(entered, SPRINT_START) is False


def test_unplanned_ignores_other_sprint_names():
    acts = [sprint_act(SPRINT_START + 5000, added=["Sprint #90"])]
    # No add for #91 -> falls back, here no fallback -> None -> planned (not unplanned)
    assert ss.sprint_entry_ts(acts, "Sprint #91") is None
    assert ss.is_unplanned(None, SPRINT_START) is False


# --- ordered_stages ------------------------------------------------------- #
def test_ordered_stages_canonical_then_extras():
    order = ss.ordered_stages({"Published": 1, "Backlog": 2}, {"Mystery": 1})
    assert order.index("Backlog") < order.index("Published")
    assert order[-1] == "Mystery"  # unknown stage sorted to the end


# --- aggregate ------------------------------------------------------------ #
def _issue(id_, stage_now, stage_at_start, points, unplanned):
    return {
        "id": id_,
        "summary": id_,
        "squad": "Squad B",
        "stage_now": stage_now,
        "stage_at_start": None if unplanned else stage_at_start,
        "story_points": points,
        "unplanned": unplanned,
    }


def test_aggregate_counts_and_points():
    items = [
        _issue("A", "Published", "In Progress", 3, False),
        _issue("B", "Review", "Backlog", 2, False),
        _issue("C", "In Progress", None, 5, True),  # unplanned mid-sprint
    ]
    agg = ss.aggregate(items, "Squad B")

    assert agg["total_tasks"] == 3
    assert agg["planned_tasks"] == 2
    assert agg["unplanned_tasks"] == 1
    assert agg["unplanned_points"] == 5
    assert agg["completed_tasks"] == 1  # only "Published"
    assert agg["completed_points"] == 3
    assert agg["total_points"] == 10
    # start snapshot only counts planned tasks, by their start status
    assert agg["start_snapshot"] == {"In Progress": 1, "Backlog": 1}
    # end snapshot counts ALL current tasks
    assert agg["end_snapshot"] == {"Published": 1, "Review": 1, "In Progress": 1}
    assert agg["unplanned_list"][0]["id"] == "C"
    assert agg["completion_rate"] == round(100 / 3, 1)


def test_aggregate_empty():
    agg = ss.aggregate([], "Empty")
    assert agg["total_tasks"] == 0
    assert agg["completion_rate"] == 0.0
    assert agg["start_snapshot"] == {}
