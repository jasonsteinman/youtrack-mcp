# Fine-tuned YouTrack MCP — Added Features

This is a fork of [`tonyzorin/youtrack-mcp`](https://github.com/tonyzorin/youtrack-mcp)
with extra tools for **sprint operations**, **team actions**, and **team
reporting** that the upstream server doesn't provide.

Everything here is additive — all original tools still work. The new tools live in:

| Area | Module |
|------|--------|
| Sprint reporting | `youtrack_mcp/tools/reports.py` |
| Sprint operations | `youtrack_mcp/tools/sprints.py` + `youtrack_mcp/api/agiles.py` |
| Team actions (by name) | `youtrack_mcp/tools/team_actions.py` |
| Inbox & task summary | `youtrack_mcp/tools/inbox.py` |

---

## 📊 Reporting

Story points are aggregated per developer/squad, broken down by **Stage**
(Backlog, In Progress, Review, Test, Ready to Publish, Published, …). A task with
multiple assignees credits its **full** points to each; tasks with no estimate are
reported separately as `unestimated`.

| Tool | What it does | Example |
|------|--------------|---------|
| `sprint_developer_report` | Story points per developer, by stage | `sprint_developer_report(sprint="Sprint #91")` |
| `sprint_squad_report` | Story points per squad, by stage | `sprint_squad_report(squad="Squad A")` |

Both default to the board's **current sprint** if `sprint` is omitted, and to the
board named in the `YOUTRACK_DEFAULT_BOARD` environment variable if `board` is omitted.

## 🏃 Sprint operations

| Tool | What it does | Example |
|------|--------------|---------|
| `list_sprints` | List a board's sprints + current sprint | `list_sprints(board="Dev Board")` |
| `get_sprint_issues` | Issues on a sprint (Stage, Assignee, Story Point) | `get_sprint_issues(sprint="Sprint #91")` |
| `move_issue_to_sprint` | Move an issue between sprints | `move_issue_to_sprint(issue_id="PROJ-123", target_sprint="Sprint #92", from_sprint="Sprint #91")` |

> On single-sprint boards, adding an issue to a sprint automatically removes it
> from its previous one, so `move_issue_to_sprint` works even if `from_sprint` is omitted.

## 👥 Team actions (name-aware)

These resolve a **display name** ("Alex", "Sam") to the right login
automatically — you don't need to know logins. Ambiguous names return candidates.

| Tool | What it does | Example |
|------|--------------|---------|
| `whois` | Resolve a name to a login (read-only) | `whois(name="Alex")` |
| `assign_issue` | Set the Assignee by name | `assign_issue(issue_id="PROJ-123", person="Sam")` |
| `set_reviewer` | Set the Reviewer by name | `set_reviewer(issue_id="PROJ-123", person="Alex")` |
| `comment_with_mentions` | Comment and @-mention people by name | `comment_with_mentions(issue_id="PROJ-123", text="please review", mention="Alex, Sam")` |

> User-typed fields (Assignee, Reviewer) are written with the correct
> `MultiUserIssueCustomField` payload, which the upstream updater couldn't handle.

## 📥 Inbox & summaries

| Tool | What it does | Example |
|------|--------------|---------|
| `my_inbox` | Notification proxy: recent issues mentioning you, assigned to you, or you've commented on | `my_inbox(days=7)` |
| `task_summary` | Compact issue abstraction: status, priority, people, squad, points, recent comments | `task_summary(issue_id="PROJ-123")` |

> YouTrack has no clean "my notifications" REST endpoint, so `my_inbox` builds a
> practical proxy from recent activity that would have notified you.

---

## Setup

1. Create and fill a `.env` file in the project root:
   ```ini
   YOUTRACK_URL=https://yourworkspace.youtrack.cloud
   YOUTRACK_API_TOKEN=perm-...        # Profile → Account Security → New token
   YOUTRACK_CLOUD=true
   YOUTRACK_DEFAULT_BOARD=Your Board Name   # default board for reports/sprint tools
   ```
2. Install dependencies into a virtual environment:
   ```bash
   python -m venv .venv
   .venv/Scripts/python -m pip install -r requirements.txt   # Windows
   ```
3. Run the server (stdio transport):
   ```bash
   .venv/Scripts/python main.py
   ```

### Register it with Claude Code

```bash
claude mcp add youtrack-mine -s user \
  -e YOUTRACK_URL=https://yourworkspace.youtrack.cloud \
  -e YOUTRACK_API_TOKEN=perm-... \
  -e YOUTRACK_CLOUD=true \
  -- /path/to/.venv/Scripts/python /path/to/main.py
```

Then restart Claude and ask, e.g. *"show the squad story-point report for the current sprint"*.

## Defaults

The reporting and sprint tools read their default board from the
**`YOUTRACK_DEFAULT_BOARD`** environment variable. Pass a `board=` argument to
target a different board on any call.
