<div align="center">

<img src="assets/header.jpg" alt="Jason-MCP" width="340">

# Jason-MCP

**A well-functioning YouTrack MCP for people who hate doing YouTrack busywork.**

*Sprints, squad reports, name-aware actions, and the notifications inbox the official one forgot.*

</div>

---

Jason-MCP is a [Model Context Protocol](https://modelcontextprotocol.io) server that
lets Claude (and any MCP client) drive **JetBrains YouTrack** — read and write issues,
move sprints, report story points per developer/squad, assign people by name, and check
what's waiting on you. It speaks plain strings, not fragile API objects, so the common
operations just work.

> **New here / setting it up for a teammate?**
> → [docs/INTRODUCTION.md](docs/INTRODUCTION.md) — the one-page pitch ·
> [docs/INSTALL.md](docs/INSTALL.md) — install & connect in ~10 min ·
> [skills/README.md](skills/README.md) — the one-word skills pack.

## ✨ What's fine-tuned here

Beyond the standard YouTrack tools, Jason-MCP adds:

- **🗂️ Schema-aware custom fields** — field types are read from the issue itself, so
  any State/enum/user field updates correctly (no more `StateBundleElement` type errors),
  and fields can actually be **cleared** (unassign everyone in one call).
- **🏃 Sprint operations** — move issues between sprints, roll sprints over, fetch sprint
  contents.
- **📊 Team reporting** — story points per developer and per squad.
- **🙋 Name-aware actions** — assign, set reviewer, and @-mention people by **display name**,
  not just login. Jason-MCP resolves the name for you.
- **🔔 Notifications inbox** — surface mentions and comment activity the official MCP
  doesn't expose.

See **[FINE_TUNED_FEATURES.md](FINE_TUNED_FEATURES.md)** for the full list, and
**[BLOCKED_OPERATIONS.md](BLOCKED_OPERATIONS.md)** for the custom-field fixes and why
they were needed.

## 🚀 Quick reference — common operations

These use the **proven simple-string format**. Pass plain strings, not nested objects.

### State, priority, assignee, type, estimation
```python
update_issue_state("DEMO-123", "In Progress")
update_issue_priority("DEMO-123", "Critical")
update_issue_assignee("DEMO-123", "john.doe")     # or a display name: "John Doe"
update_issue_type("DEMO-123", "Bug")
update_issue_estimation("DEMO-123", "4h")          # 30m · 4h · 2d · 1w · "3d 5h"
```

### Custom fields (and clearing them)
```python
# Set several at once
update_custom_fields("DEMO-123", {"Priority": "Critical", "Type": "Bug"})

# Clear fields — None empties them (unassign everyone, blank the squad, etc.)
update_custom_fields("DEMO-123", {
    "Assignee": None,
    "Reviewer": None,
    "Squad": None,
})
update_issue_assignee("DEMO-123", "unassigned")    # also clears the assignee
```

### Finding, creating, linking, commenting
```python
search_issues("bug in login")
get_project_issues("DEMO")
get_issue("DEMO-123")

create_issue(project_id="DEMO", summary="Bug in login system",
             description="Users cannot log in with special characters")

add_dependency("DEMO-123", "DEMO-124")
add_relates_link("DEMO-123", "DEMO-125")

add_comment("DEMO-123", "Fixed the login bug")
get_issue_comments("DEMO-123")
```

### Attachments
```python
get_issue_raw("DEMO-123")                          # raw data incl. attachments
get_attachment_content("DEMO-123", "1-456")        # download as base64
delete_attachment("DEMO-123", "1-456")             # needs permission
```

## 🛠️ Install & run (from source)

Jason-MCP runs straight from this repo over stdio — no Docker or npm package required.

```bash
git clone https://github.com/jasonsteinman/fine-tuned-youtrack-mcp.git
cd fine-tuned-youtrack-mcp

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Set your credentials (or put them in a `.env` file — it's auto-loaded):

```bash
export YOUTRACK_URL="https://your-instance.youtrack.cloud"
export YOUTRACK_API_TOKEN="perm-XXXXXXXX..."
```

Then run it:

```bash
python main.py                     # stdio (default, for MCP clients)
python main.py --transport sse --port 8000   # or SSE over HTTP
python main.py --version
```

### Wire it into Claude

Add Jason-MCP to your MCP client config (e.g. Claude Desktop / Claude Code), pointing
at this repo's `main.py`:

```json
{
  "mcpServers": {
    "youtrack": {
      "command": "python",
      "args": ["/absolute/path/to/fine-tuned-youtrack-mcp/main.py"],
      "env": {
        "YOUTRACK_URL": "https://your-instance.youtrack.cloud",
        "YOUTRACK_API_TOKEN": "perm-XXXXXXXX..."
      }
    }
  }
}
```

## ⚙️ Configuration

| Variable | Purpose |
|---|---|
| `YOUTRACK_URL` | Your YouTrack instance URL |
| `YOUTRACK_API_TOKEN` | Your YouTrack permanent API token |
| `YOUTRACK_VERIFY_SSL` | SSL verification (default: `true`) |
| `ENABLED_TOOLS` | Allowlist — enable only these tools (disables all others) |
| `DISABLED_TOOLS` | Denylist — disable specific tools |

**Tool filtering** keeps context lean. Tool names are case-insensitive and treat
`-`/`_` the same; `ENABLED_TOOLS` wins over `DISABLED_TOOLS`. Filtering happens at startup.

```bash
export ENABLED_TOOLS="get_issue,search_issues,update_issue_state"   # allowlist
export DISABLED_TOOLS="delete_issue,delete_attachment"              # denylist
```

## 🧪 Development

```bash
pip install -r requirements.txt
python -m pytest tests/unit -q          # run the unit suite
```

The custom-field fixes are covered by `tests/unit/test_custom_field_schema_aware.py`.

## 🐱 About the name

Jason-MCP. The kittens are just here for morale.

## 💬 Support

Questions, bugs, or ideas? Open an [issue](https://github.com/jasonsteinman/fine-tuned-youtrack-mcp/issues)
or reach me at **maintainer@example.com**.

---

<sub>Forked from [`tonyzorin/youtrack-mcp`](https://github.com/tonyzorin/youtrack-mcp) and fine-tuned. Thanks to the original author for the foundation.</sub>
