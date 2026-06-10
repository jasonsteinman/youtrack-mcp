# Install & use Jason-MCP

Get the YouTrack MCP server running and connected to your AI client (Claude Code),
then add the one-word skills. ~10 minutes. Commands are given for **Windows
(PowerShell)** first, then **macOS / Linux**.

## 1. Prerequisites

- **Python 3.10+** — check with `python --version`.
- **Git**.
- **Claude Code** (the `claude` CLI) installed and signed in.
- A **YouTrack account** on your team's workspace.

## 2. Get the code

```powershell
git clone https://github.com/jasonsteinman/fine-tuned-youtrack-mcp.git
cd fine-tuned-youtrack-mcp
```

## 3. Create a virtual environment & install

**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 4. Get your YouTrack API token

1. In YouTrack, click your avatar → **Profile** → **Account Security**.
2. Under **Tokens**, click **New token…**, name it (e.g. "Jason-MCP"), give it the
   **YouTrack** scope, and **Create**.
3. Copy the `perm:...` value — you won't see it again.

## 5. Configure

Copy the template and fill in your values:

**Windows (PowerShell):**
```powershell
Copy-Item .env.example .env
notepad .env
```
**macOS / Linux:**
```bash
cp .env.example .env && nano .env
```

Set `YOUTRACK_URL`, `YOUTRACK_API_TOKEN`, and `YOUTRACK_DEFAULT_BOARD`. (`.env` is
gitignored — your token stays local.)

**Smoke test** the server starts (Ctrl-C to stop — it waits silently for a client):
```powershell
.\.venv\Scripts\python.exe main.py --version
```
Should print the version. If it errors about the token/URL, recheck `.env`.

## 6. Connect it to Claude Code

Register the server with the `claude` CLI. Use **absolute paths** to the venv's
Python and `main.py`. Add it at **user** scope so it's available everywhere.

**Windows (PowerShell)** — run from the repo folder so `$PWD` is correct:
```powershell
claude mcp add youtrack -s user `
  -e YOUTRACK_URL=https://your-instance.youtrack.cloud `
  -e "YOUTRACK_API_TOKEN=perm:your-token-here" `
  -e "YOUTRACK_DEFAULT_BOARD=Dev Board" `
  -- "$PWD\.venv\Scripts\python.exe" "$PWD\main.py"
```
**macOS / Linux:**
```bash
claude mcp add youtrack -s user \
  -e YOUTRACK_URL=https://your-instance.youtrack.cloud \
  -e "YOUTRACK_API_TOKEN=perm:your-token-here" \
  -e "YOUTRACK_DEFAULT_BOARD=Dev Board" \
  -- "$PWD/.venv/bin/python" "$PWD/main.py"
```

> The name **must be `youtrack`** — the skills call `mcp__youtrack__*`.
> Passing the token via `-e` keeps it in your local Claude config (it also reads
> `.env`, but `-e` is the reliable path since the client may launch the server from
> a different folder).

Verify:
```powershell
claude mcp list
```
You should see `youtrack` with a ✓. In a Claude session, ask *"list the sprints on
the Dev Board board"* — if you get sprints back, you're connected.

## 7. Add the skills (optional but recommended)

The one-word commands (`/sprint-summary`, `/stuck-tasks`, `/whatsup`, …) live in
[`../skills/`](../skills). Follow [`../skills/README.md`](../skills/README.md) — it's a
one-minute copy step plus setting your login in `~/.claude/youtrack.md`.

## 8. Use it

Just talk to Claude:
- *"Move DEMO-1234 to the next sprint, set it to Backlog, and unassign everyone."*
- *"Start the sprint"* (dry-run first; add *"for real"* to apply).
- *"Give me a sprint summary as HTML."*
- `/stuck-tasks`, `/whatsup`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `claude mcp list` shows ✗ for youtrack | Re-check the absolute paths to `python` and `main.py`; run `python main.py --version` manually to see the error. |
| "API token is required" | Token missing/wrong in `-e` or `.env`. Regenerate it (step 4). |
| Tools work but skills don't appear | Did you copy `skills/*.md` into `~/.claude/commands/` and restart Claude? |
| Sprint/board tools return nothing | Check `YOUTRACK_DEFAULT_BOARD` matches the board's exact name (e.g. `Dev Board`). |
| PDF/HTML report script errors | Run it with the repo's venv Python; `pip install -r requirements.txt` for `reportlab`. |

Stuck? **maintainer@example.com**
