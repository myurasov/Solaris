---
name: browserctl
triggers: ["launch a browser", "open the browser", "browser profile", "new browser profile", "ephemeral browser", "browserctl", "drive the web", "browser automation", "take a page snapshot", "screenshot the page"]
summary: Drive Chromium through the browserctl CLI (this plugin's browserctl.py) - per-project persistent profiles on stable CDP ports, clean on first use, ephemeral on demand; replaces the Playwright MCP.
---
_Rev. 5_

# Skill: browserctl - Browser Lifecycle and Driving Pages <!-- omit in toc -->

- [Why This Exists](#why-this-exists)
- [Profiles: Per-Project, Clean, Ephemeral on Demand](#profiles-per-project-clean-ephemeral-on-demand)
- [Command Reference](#command-reference)
- [Typical Flows](#typical-flows)
- [Scripting Anything Else: attach()](#scripting-anything-else-attach)
- [Hard Rules and Known Constraints](#hard-rules-and-known-constraints)

## Why This Exists

`browserctl.py` (in this plugin's overlay - `ai/browserctl/` in the project, or the plugin's
`shared/` when linked) **replaces the Playwright MCP** as the project's browser layer. Playwright
remains the engine: browserctl launches the Playwright-managed Chromium directly, one persistent
profile per purpose, each on a stable CDP port. Any client attaches over CDP, so several flows
(and several agent sessions) can hold browsers open concurrently with zero profile-lock
conflicts - and every command is an ordinary CLI call, far cheaper in context than MCP tool
round-trips.

Run it with [uv](https://docs.astral.sh/uv/) (dependencies are inline PEP 723 metadata - no
project dep changes needed):

```bash
uv run <path-to>/browserctl.py <cmd> ...
```

One-time on a machine: `uv run --with playwright playwright install chromium` (browserctl tells
you when it is missing).

## Profiles: Per-Project, Clean, Ephemeral on Demand

A profile is a named persistent Chromium user-data dir under
`~/.browserctl/profiles/<project>/<name>/` with a registry entry in `~/.browserctl/state.json`
(port, color, ephemeral flag, pid when running). Everything under `~/.browserctl/` is
machine-local and disposable - never inside the repo (repos are often cloud-synced).

- **Every profile belongs to a project.** The project id is auto-derived (nearest
  `ai/manifest.json` walking up from the CWD, else the git-root/CWD name) or forced with
  `--project` / `$BROWSERCTL_PROJECT`. Two projects never share profiles, ports, or logins.
- **Profiles start clean.** A missing profile is created as an empty user-data dir on first
  `launch` - no logins inherited from anywhere. The project's standing profile is `default`;
  create it at project init with `init` (idempotent, no launch) or just launch it.
- **Ephemeral on demand:** `launch --ephemeral` marks the profile disposable - `stop` deletes it
  immediately, `prune` sweeps any left stopped. `launch --fresh` spins up a clean ephemeral
  sibling (`<name>-2`, ...) when the requested profile is already busy. `persist` /
  `persist --forget` flips the flag later. The `default` profile is never auto-deleted
  (explicit `remove` only).
- **Logins persist per profile.** Sign in once in a headed window; the session survives
  relaunches. `clone --from <src> --to <dst>` slim-copies just the login-bearing state when a
  second profile should share it (source must be closed).
- **Each profile gets a distinct theme color** (auto-assigned, or `--color '#RRGGBB'`) so headed
  windows are tellable apart.

## Command Reference

All commands take `--project P` (omitted = auto-derived). Lifecycle:

| Command | Use |
|---|---|
| `init [--profile default]` | Ensure the project's clean standing profile (registry + empty dir, no launch). Run at project init. |
| `launch --profile P [--headless\|--headed] [--minimized] [--url U] [--color C] [--ephemeral] [--fresh]` | Start (idempotent - reports if already running; missing profile created clean). Unattended runs: `--headless`; interactive: `--headed --minimized` then `show` on demand. |
| `stop --profile P` | Graceful close (CDP `Browser.close`, SIGTERM fallback). Deletes the profile if ephemeral. |
| `status [--json] [--all]` | This project's profiles, ports, liveness (`--all`: every project). |
| `show / hide --profile P` | Restore / minimize the window (CDP). |
| `persist --profile P [--forget]` | Mark long-lived / revert to ephemeral. |
| `prune [--all]` | Delete stopped ephemeral profiles (dir + registry + log). |
| `remove --profile P` | Delete any profile explicitly, `default` included (must be stopped). |
| `clone --from <name\|project/name\|dir> --to <name> [--color C]` | Login-preserving slim copy (source closed). |
| `theme --profile P --color '#RRGGBB'` | Recolor (browser must be closed). |
| `cdp-url --profile P` | Print `http://localhost:<port>` for attaching. |

Drive commands (attach over CDP, leave the browser open; `--tab` defaults to the last tab):

| Command | Use |
|---|---|
| `tabs --profile P` | List open tabs (index, title, url). |
| `navigate --profile P --url U [--tab N\|--new-tab]` | Go to a URL (waits for `domcontentloaded`, best-effort `networkidle`). |
| `snapshot --profile P --out F [--tab N]` | Aria snapshot YAML - same content the MCP's `browser_snapshot` produced. |
| `screenshot --profile P --out F [--tab N] [--full-page]` | PNG for visual verification. |
| `eval --profile P --js EXPR [--tab N]` | `page.evaluate`; prints JSON. |

Bare `--out` filenames land in `~/.browserctl/out/<project>/` (never the CWD); absolute paths
are honored.

## Typical Flows

- **Unattended check / scrape:** `launch --profile default --headless --url <target>` ->
  `snapshot` / `eval` -> leave running for the session or `stop`.
- **One-off job that must not touch standing state:** `launch --profile job-x --ephemeral
  --headless --url <target>` -> work -> `stop` (auto-deletes).
- **User-visible work:** `launch --profile <purpose> --headed --url <target>` (own window, own
  color); `--minimized` + `show` for the quiet-until-needed pattern.
- **Login needed:** `launch --profile default --headed --url <login page>`, ask the user to sign
  in once, then continue - the session persists in the profile.
- **Parallel flows on one purpose:** `launch --fresh` gives the second flow a clean ephemeral
  sibling instead of a lock conflict.

## Scripting Anything Else: attach()

For interactions the CLI does not wrap (hover, click, form filling, downloads, waiting on
selectors), write a short Python script against the same live browser - full Playwright API:

```python
import importlib.util
spec = importlib.util.spec_from_file_location("browserctl", "<path-to>/browserctl.py")
bctl = importlib.util.module_from_spec(spec); spec.loader.exec_module(bctl)

with bctl.attach("default") as (pw, browser):
    page = browser.contexts[0].pages[-1]
    page.click("text=Sign in")
    # downloads: with page.expect_download() as dl: ...; dl.value.save_as(...)
```

Run such scripts with `uv run --with playwright python <script>` from a scratch location - never
leave them in the repo root. `attach()` disconnects on exit; the browser keeps running.

For SPA-heavy sites (Slack, SharePoint, ...) always `page.goto(url,
wait_until="domcontentloaded")` - such apps often never fire `load` and the default wait times
out.

## Hard Rules and Known Constraints

1. **browserctl is the only launch path for its profiles.** It launches with
   `--use-mock-keychain`; launching the same profile any other way (bare binary, ad-hoc
   `launch_persistent_context`) makes Chromium **silently purge every cookie**.
2. **No live headless<->headed flip.** `stop`, then `launch` in the other mode - the profile
   persists and relaunch takes seconds.
3. **Clone/theme only against a closed browser.** `clone` refuses a running source; `Singleton*`
   lock files are never copied (browserctl strips them, and clears stale ones on launch).
4. **Keep repos clean.** Snapshots/screenshots default to `~/.browserctl/out/<project>/`; move
   anything worth keeping into the project explicitly - never auto-commit captures.
5. Run `prune` at natural hygiene points (end of a sweep, project health checks) so ephemeral
   profiles do not accumulate.
6. `~/.browserctl/` is reconstructible (re-create + re-auth); nothing under it is a source of
   truth. A wedged registry entry (port shown busy, nothing running) can be fixed by deleting
   the entry from `state.json`.
7. **Sandbox fallback.** In harnesses that deny home-dir writes, two known failure modes and
   their one-retry fixes (disclose when used):
   - `PermissionError` on `~/.browserctl/.state.lock` (or any `~/.browserctl/` path) → rerun
     with `BROWSERCTL_HOME=<writable scratch>/__browserctl/`.
   - `uv` dies on `~/.cache/uv` permission errors before browserctl even starts → rerun with
     `UV_CACHE_DIR=<writable scratch>/uv-cache`.
   Both variables compose: `UV_CACHE_DIR=... BROWSERCTL_HOME=... uv run <path>/browserctl.py ...`.
   - No shell network AND a pre-warmed cache available → also set `UV_OFFLINE=1`: uv's
     resolver otherwise contacts the package index even on a full cache hit, and the DNS
     failure looks like a missing package.
   A sandbox may still block the Chromium process itself (e.g. Codex Seatbelt SIGABRTs
   Chromium). Two remaining tiers, in order: **request per-command escalation** where the
   harness supports it (Codex `approval_policy = "on-request"`: ask with a one-line
   justification; the user approves each command and it runs outside the sandbox - validated
   for the full launch/tabs/stop chain), or **split launch from drive**: have an unsandboxed
   session launch the profile, then drive it from the sandboxed one over the CDP port
   (`tabs`/`navigate`/`snapshot`/`eval` only need localhost).