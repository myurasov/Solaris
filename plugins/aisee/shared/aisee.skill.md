_Rev. 1_

---
name: aisee
triggers:
  - "visually verify <scope>" / "visually check <scope>"
  - "check the UI" / "does the UI look right"
  - "look at <screenshot|video>" / "what's on screen"
  - "watch this recording" / "watch the flow"
  - "visual regression" / "compare against baseline"
  - "OCR this" / "read the text on <screen>"
  - "use aisee" / "aisee look" / "aisee assert" / "aisee watch"
  - "set up aisee"
antitriggers:
  - "develop "
  - "work on tasks/"
summary: Capture the UI (or take provided media), then have AISee's VLM eyes answer a question,
  assert an expectation, or watch a recording - MCP first, REST/CLI fallback.
---

# Skill: aisee - visual verification with AISee <!-- omit in toc -->

- [0. Reach the server](#0-reach-the-server)
- [1. Capture](#1-capture)
- [2. Query](#2-query)
  - [2a. MCP (preferred)](#2a-mcp-preferred)
  - [2b. REST fallback](#2b-rest-fallback)
  - [2c. CLI fallback](#2c-cli-fallback)
- [3. Report](#3-report)
- [Setting up a server](#setting-up-a-server)
- [Troubleshooting](#troubleshooting)

AISee is a tool that gives AI agents eyes: `look` (free-form/OCR), `assert_visual`
(pass/fail verdicts), `watch` (chunked whole-video analysis). Conventions (query-kind
choice, evidence, media rules) are in [`aisee.rule.md`](aisee.rule.md) - they always apply.

## 0. Reach the server

1. Read `aisee_server` from `ai/memory/resources.md` and the consumer token (if any) from
   `ai/memory/credentials.md`.
2. Confirm liveness: the MCP `health` tool if the `aisee` MCP server is connected, else
   `GET <server>/v1/health` (open endpoint).
3. On the **first contact** with a server, read `GET <server>/v1/describe?flavor=mcp` - the
   server's own guide: tools, installed models with strengths/weaknesses/pitfalls, live
   serving config, and the exact media-upload recipe.
4. If the MCP tools are absent from your session, check the project MCP config for the
   `aisee` entry (type `http`, URL `<server>/mcp`, bearer header when auth is on) and reload
   the session; until then use the REST fallback. If the host itself is unreachable, see
   [Setting up a server](#setting-up-a-server).

## 1. Capture

Skip if the user already provided media.

- **Web:** Playwright MCP (`browser_navigate`, `browser_take_screenshot`; record interactions
  as video if the check is temporal).
- **Native / TUI:** `screencapture` (macOS) / `import`/`grim` (Linux) for stills; ffmpeg or OS
  screen recording for video.
- **Mobile:** simulator/device screen recording.

Save per the evidence rule: `ai/memory/visual/<area>-<state>-<YYYYMMDD>.png` (or the active
task folder).

## 2. Query

### 2a. MCP (preferred)

Tools: `look(media, question, ...)`, `assert_visual(media, expectation, ...)`,
`watch(video, question|expectation, fps, wait, ...)`, plus `list_models`, `list_tasks`,
`get_task`, `cancel_task`, `describe`, `health`.

Media entries are AISee-host paths or `sha256:<hex>` blob refs. For a local file:

```bash
sha=$(shasum -a 256 shot.png | cut -d' ' -f1)     # sha256sum on Linux
curl -s <server>/v1/blobs/$sha                     # {"exists": ...} - probe first
curl -s -X POST <server>/v1/blobs -F files=@shot.png \
     ${TOKEN:+-H "Authorization: Bearer $TOKEN"}   # only if exists=false
# then: assert_visual(media=["sha256:$sha"], expectation="...")
```

Query tools block until done (a cold model can take minutes - be patient, never resubmit).
For long videos: `watch(..., wait=false)` returns a task id; poll `get_task` every few
seconds until `status` is terminal. Useful parameters: `model` (omit for default), `frames`
/ `fps` (video sampling), `native` (send the video itself - temporal reasoning,
video-capable models only), `context` (background the model cannot see in the pixels).

### 2b. REST fallback

```bash
curl -s -X POST <server>/v1/tasks -F files=@shot.png \
  -F 'params={"kind":"assert","expectation":"the Start button is visible and enabled"}' \
  ${TOKEN:+-H "Authorization: Bearer $TOKEN"}      # -> {"id": "..."}
curl -s <server>/v1/tasks/<id> ${TOKEN:+-H "Authorization: Bearer $TOKEN"}   # poll 2-5 s
```

Statuses: `queued -> preparing_media -> model_loading (cold only) -> running -> done`
(`failed`/`canceled` terminal). JSON submission takes `media_paths` (host paths or
`sha256:` refs) instead of multipart. `timings.total_s` reports wall-clock on finish.

### 2c. CLI fallback

From a checkout of github.com/myurasov/AISee (`./aisee` bootstraps its own venv):

```bash
aisee assert shot.png -e "the Start button is visible" --server <server>   # exit code = verdict
aisee look shot.png -q "What error is shown?" --server <server>
aisee watch run.mp4 -e "the counter increases monotonically" --fps 8 --server <server>
```

The CLI uploads media itself (with the same dedup) and reads `AISEE_SERVER` /
`AISEE_API_TOKEN` from the environment.

## 3. Report

- Quote the verdict fields: `pass`, `reason`, `evidence` (or the `answer` for look; the
  synthesized answer / `failing_ranges` for watch).
- Link the capture paths used as evidence.
- One claim per assert; if a compound check was needed, list each sub-assert and its verdict.

## Setting up a server

If no AISee server is reachable and the user wants one: follow `aisee.admin.agent.md` (or
README.md) in github.com/myurasov/AISee on a Linux GPU host - in short:

```bash
git clone https://github.com/myurasov/AISee ~/aisee && cd ~/aisee
uv sync && ./aisee install            # checks docker/NVIDIA toolkit/ffmpeg, creates ~/.aisee
./aisee creds set HF_TOKEN            # gated model downloads
./aisee model install qwen3-vl-30b-a3b-instruct   # recommended default
./aisee api start                     # REST + MCP on 0.0.0.0:8484
```

Then record the URL in `ai/memory/resources.md` (`aisee_server`), the consumer token (if
enabled) in `ai/memory/credentials.md`, update the `aisee` MCP entry, and re-run
`uv run -m solaris.tools.mcp_sync --check`.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| 401 | consumer token missing/wrong - set the bearer header / `AISEE_API_TOKEN` |
| 403 | admin-only action (model install/start/stop) - not available over MCP; ask the server operator or use the CLI on the host |
| `unknown blob sha256:...` | blob expired (~24 h TTL) or never uploaded - re-upload via `POST /v1/blobs` |
| task stuck in `model_loading` | cold model load (first-ever use downloads tens of GB); keep polling `progress`, never resubmit |
| task `failed` with memory error | another model holds the GPU - `list_models`, ask operator to stop it, retry |
| MCP tools missing from session | project MCP config lacks the `aisee` entry or session predates it - fix entry, `mcp_sync --check`, reload; use REST meanwhile |
| verdict looks wrong | read `reason`/`evidence`, tighten the expectation, add `context`, or retry with a stronger model from `describe` |
