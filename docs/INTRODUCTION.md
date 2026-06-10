# Jason-MCP — YouTrack, without the busywork

**A well-functioning YouTrack MCP for people who hate doing YouTrack busywork.**

Jason-MCP is a [Model Context Protocol](https://modelcontextprotocol.io) server that
plugs YouTrack into your AI assistant (Claude Code, Claude Desktop, Cursor, …). Once
it's connected, you ask in plain language — *"move this task to the next sprint and
unassign it"*, *"give me a sprint summary"*, *"what's stuck?"* — and it does it.

## Why this one

It's a fine-tuned fork of the community YouTrack MCP, with the rough edges sanded off:

- **Custom-field writes that actually work.** The stock server chokes on Stage,
  Assignee, Reviewer and other typed fields (`Incompatible field type` errors).
  Jason-MCP reads each field's real schema and sends the right type — and can
  **clear** people fields (unassign) instead of only setting them.
- **Sprints, finally.** Stock YouTrack MCPs can't see sprints at all. Jason-MCP can
  list them, move issues between them, **roll over** a sprint, **start** the next one
  (carry everything unfinished forward in one command), and generate a full
  **sprint summary**.
- **Reports you can hand to people.** One-command **sprint summary** and
  **stuck-task** reports as Markdown, self-contained **HTML**, or **PDF** — broken
  down by team, squad, and developer.

## What you can do with it

| Ask for… | And it… |
|---|---|
| "Move DEMO-1234 to next sprint, status Backlog, unassign everyone" | Sets the sprint, Stage, and clears Assignee/Reviewer/Squad in one go. |
| "Start the sprint" | Carries every unfinished task from the ending sprint into the next one. |
| "Sprint summary" | Builds the team/squad/developer breakdown (status at start vs now, unplanned additions) + optional HTML/PDF. |
| "What's stuck?" | Lists current-sprint tasks sitting >48h in the same status, with reviewers and last comments. |
| "What's up?" | Digests your mentions, replies, and tickets waiting on you. |

The last three come as one-word **skills** (`/sprint-summary`, `/stuck-tasks`,
`/whatsup`) — see [`../skills/README.md`](../skills/README.md).

## Get started

→ **[INSTALL.md](INSTALL.md)** — install the server, connect it to your AI client,
and add the skills. About 10 minutes.

Questions / bugs: **maintainer@example.com**
