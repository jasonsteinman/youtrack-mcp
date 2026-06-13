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


def test_current_sprint_latest_add_wins():
    acts = [
        sprint_act(SPRINT_START - 1000, added=["Sprint #90"]),
        sprint_act(SPRINT_START + 1000, removed=["Sprint #90"], added=["Sprint #91"]),
    ]
    assert ss.current_sprint(acts) == "Sprint #91"


def test_current_sprint_removed_then_none():
    acts = [
        sprint_act(SPRINT_START - 1000, added=["Sprint #90"]),
        sprint_act(SPRINT_START + 1000, removed=["Sprint #90"]),
    ]
    assert ss.current_sprint(acts) is None


def test_current_sprint_no_events():
    assert ss.current_sprint([]) is None


def test_unplanned_ignores_other_sprint_names():
    acts = [sprint_act(SPRINT_START + 5000, added=["Sprint #90"])]
    # No add for #91 -> falls back, here no fallback -> None -> planned (not unplanned)
    assert ss.sprint_entry_ts(acts, "Sprint #91") is None
    assert ss.is_unplanned(None, SPRINT_START) is False


def test_sprint_events_detected_via_activity_item_type():
    # Real YouTrack labels sprint events "SprintActivityItem", not "SprintCategory".
    act = {
        "$type": "SprintActivityItem",
        "timestamp": SPRINT_START + 1,
        "added": [{"name": "Sprint #91"}],
        "removed": [],
    }
    assert ss.sprint_events([act]) == [(SPRINT_START + 1, ["Sprint #91"], [])]
    assert ss.sprint_entry_ts([act], "Sprint #91") == SPRINT_START + 1


# --- member_of_at --------------------------------------------------------- #
def test_member_of_at_replays_to_timestamp():
    acts = [
        sprint_act(SPRINT_START - 1000, added=["Sprint #91"]),       # joined before start
        sprint_act(SPRINT_START + 1000, removed=["Sprint #91"]),     # left after start
    ]
    assert ss.member_of_at(acts, "Sprint #91", SPRINT_START) is True   # member at start
    assert ss.member_of_at(acts, "Sprint #91", SPRINT_START + 2000) is False


def test_member_of_at_not_yet_added():
    acts = [sprint_act(SPRINT_START + 1000, added=["Sprint #91"])]
    assert ss.member_of_at(acts, "Sprint #91", SPRINT_START) is False  # added after start


def test_was_ever_in_sprint():
    acts = [sprint_act(SPRINT_START - 5000, added=["Sprint #90"], removed=["Sprint #89"])]
    assert ss.was_ever_in_sprint(acts, "Sprint #90") is True
    assert ss.was_ever_in_sprint(acts, "Sprint #91") is False


# --- classify_membership -------------------------------------------------- #
def test_classify_planned_member_at_start():
    acts = [sprint_act(SPRINT_START - 1000, added=["Sprint #91"])]
    assert ss.classify_membership(acts, "Sprint #91", SPRINT_START,
                                  previous_sprint="Sprint #90") == "planned"


def test_classify_carryover_added_after_start_from_prev_sprint():
    # Was in #90, rolled into #91 only after the sprint started -> carryover, NOT unplanned.
    acts = [
        sprint_act(SPRINT_START - 9000, added=["Sprint #90"]),
        sprint_act(SPRINT_START + 2000, added=["Sprint #91"], removed=["Sprint #90"]),
    ]
    assert ss.classify_membership(acts, "Sprint #91", SPRINT_START,
                                  previous_sprint="Sprint #90") == "carryover"


def test_classify_unplanned_genuine_new_scope():
    # Added after start, never in the previous sprint -> genuine mid-sprint addition.
    acts = [sprint_act(SPRINT_START + 2000, added=["Sprint #91"])]
    assert ss.classify_membership(acts, "Sprint #91", SPRINT_START,
                                  previous_sprint="Sprint #90") == "unplanned"


def test_classify_no_events_falls_back_to_created():
    assert ss.classify_membership([], "Sprint #91", SPRINT_START,
                                  previous_sprint="Sprint #90",
                                  created=SPRINT_START - 100) == "planned"
    assert ss.classify_membership([], "Sprint #91", SPRINT_START,
                                  previous_sprint="Sprint #90",
                                  created=SPRINT_START + 100) == "unplanned"


# --- ordered_stages ------------------------------------------------------- #
def test_ordered_stages_canonical_then_extras():
    order = ss.ordered_stages({"Published": 1, "Backlog": 2}, {"Mystery": 1})
    assert order.index("Backlog") < order.index("Published")
    assert order[-1] == "Mystery"  # unknown stage sorted to the end


# --- aggregate ------------------------------------------------------------ #
def _issue(id_, stage_now, stage_at_start, points, unplanned,
           assignee=None, reviewer=None, squad="Squad B"):
    return {
        "id": id_,
        "summary": id_,
        "squad": squad,
        "assignee": assignee,
        "reviewer": reviewer,
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


def test_aggregate_three_way_planned_carryover_unplanned():
    items = [
        _issue("A", "Published", "In Progress", 3, False),                 # planned
        _issue("C", "In Progress", None, 5, True),                         # unplanned
        {**_issue("D", "Review", "Backlog", 2, False), "carryover": True},  # carryover
    ]
    agg = ss.aggregate(items, "Squad B")
    assert agg["total_tasks"] == 3
    assert agg["planned_tasks"] == 1
    assert agg["carryover_tasks"] == 1
    assert agg["unplanned_tasks"] == 1
    assert agg["carryover_points"] == 2
    assert agg["unplanned_points"] == 5
    # start snapshot = planned + carryover (both pre-existed the sprint)
    assert agg["start_snapshot"] == {"In Progress": 1, "Backlog": 1}
    assert agg["carryover_list"][0]["id"] == "D"
    assert agg["unplanned_list"][0]["id"] == "C"


# --- dev_summary ---------------------------------------------------------- #
def test_dev_summary_splits_assigned_and_reviewing():
    items = [
        _issue("A", "Review", "Backlog", 3, False, assignee="Alex", reviewer="Ali"),
        _issue("B", "Test", "Review", 2, False, assignee="Alex", reviewer="Ali"),
        _issue("C", "Review", "Backlog", 5, False, assignee="Ali", reviewer="Alex"),
    ]
    devs = ss.dev_summary(items)

    # Alex: assigned A+B (5 pts), reviewing C (5 pts)
    assert devs["Alex"]["assigned"]["count"] == 2
    assert devs["Alex"]["assigned"]["points"] == 5
    assert devs["Alex"]["reviewing"]["count"] == 1
    assert devs["Alex"]["reviewing"]["points"] == 5
    # "where they are": Alex's assigned tasks sit in Review and Test
    assert devs["Alex"]["assigned"]["by_stage"]["Review"]["count"] == 1
    assert devs["Alex"]["assigned"]["by_stage"]["Test"]["count"] == 1
    # Ali: assigned C (5 pts), reviewing A+B (5 pts)
    assert devs["Ali"]["assigned"]["count"] == 1
    assert devs["Ali"]["reviewing"]["count"] == 2


def test_dev_summary_ignores_missing_people():
    items = [_issue("A", "Review", "Backlog", 3, False, assignee=None, reviewer=None)]
    assert ss.dev_summary(items) == {}


# --- squad_rollup --------------------------------------------------------- #
def test_squad_rollup_one_row_per_squad():
    items = [
        _issue("A", "Published", "In Progress", 3, False, squad="Squad B"),
        _issue("B", "Review", "Backlog", 2, False, squad="Squad C"),
        _issue("C", "Test", "Backlog", 1, False, squad="Squad C"),
    ]
    rows = ss.squad_rollup(items)
    by_squad = {r["squad"]: r for r in rows}
    assert by_squad["Squad B"]["tasks"] == 1
    assert by_squad["Squad B"]["completed"] == 1
    assert by_squad["Squad C"]["tasks"] == 2
    assert by_squad["Squad C"]["points"] == 3
