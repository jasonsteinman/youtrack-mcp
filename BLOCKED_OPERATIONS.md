# Blocked Operations — Build List

Operations that failed against the live DEMO instance (issue DEMO-4264, while
trying to move it to next sprint, set Stage = Backlog, and unassign everyone).
Each entry: what was attempted, the verbatim error, the root cause, and the fix.

| # | Operation | Tool(s) tried | Result |
|---|-----------|---------------|--------|
| 1 | Set **Stage = Backlog** | `update_issue_state`, `update_custom_fields` | ❌ → ✅ fixed |
| 2 | **Clear Assignee** (multi-user) | `update_custom_fields`, `update_issue_assignee` | ❌ → ✅ fixed |
| 3 | **Clear Reviewer** (multi-user) | `update_custom_fields` | ❌ → ✅ fixed |
| 4 | **Clear Squad** (single-enum) | `update_custom_fields` | ❌ → ✅ fixed |
| 5 | **Clear QA** (single-user) | `update_custom_fields` | ❌ → ✅ fixed |
| 6 | Move to **Sprint #92** | `move_issue_to_sprint` | ✅ already worked |

---

## Bug 1 — Stage (and any non-"State" state field) gets the wrong element type

**Verbatim errors**
- `update_custom_fields`: `The API expects StateBundleElement-type values but encountered EnumBundleElementMegaProxy-type instead.`
- `update_issue_state`: `State transition failed: Unknown workflow restriction`

**Root cause**
Field type was guessed from the field **name**, not its real schema type. The
builder in `IssuesClient._update_other_custom_fields` hardcodes:
`state → StateIssueCustomField`, `priority/type → enum`, `assignee/reporter → user`,
**everything else → enum**. `Stage` is a `StateIssueCustomField`, but since the name
isn't literally `state` it fell into the enum branch and built an `EnumBundleElement`,
which YouTrack rejects for a State field. Separately, `update_issue_state` only ever
writes the field literally named `State`, so it can't target `Stage` at all.

**Fix**
Detect each field's concrete `$type` from the issue itself
(`customFields(id,name,$type)`) and build the matching value element type
(`StateIssueCustomField → StateBundleElement`, etc.). Values resolve **by name**, so
the incomplete admin bundle endpoint (which doesn't even list `Backlog`/`OnHold`) is
no longer in the path.

## Bugs 2–5 — Clearing user / enum / state fields is impossible

**Verbatim errors**
- `update_custom_fields({"Assignee": null})`: `Incompatible field type: 112-1`
- `update_issue_assignee(...)`: same `112-1` type error
- `_set_user_field(login="")`: pydantic `Input should be None` crash

**Root cause**
1. No clear path existed. A `None` value was run through `_normalize_field_value`,
   which stringifies it to the literal `"None"`, then tried to look it up as an enum
   value — producing a wrong-typed element and the `112-1` mismatch.
2. Multi-value fields (Assignee/Reviewer here are `MultiUserIssueCustomField`) require
   `value: []` to clear; single-value fields require `value: null`. Neither was emitted.
3. With `validate=True`, clearing also tripped validation (`_validate_user_exists(None)`
   → `GET users/None` → false → hard error) before any request was sent.

**Fix**
- `update_custom_fields` / `update_issue_custom_fields` now treat `None` (and empty
  string / empty list) as an explicit **clear**, emitting `value: []` for multi-value
  fields and `value: null` for single-value fields, with the field's real `$type`.
- Validation is skipped for clear requests and, because the allowed-values source is
  incomplete on this instance, a failed validation now **warns instead of hard-failing**
  (YouTrack still rejects truly invalid values server-side, with a clear message).
- `update_issue_assignee` accepts empty / `"unassigned"` / `"none"` to clear the Assignee.

## Bug 6 — none (sprint move worked)

`move_issue_to_sprint` succeeded; no change needed.
