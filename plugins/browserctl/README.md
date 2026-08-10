# browserctl - Solaris Plugin <!-- omit in toc -->

- [What It Is](#what-it-is)
- [Profile Model](#profile-model)
- [Layout](#layout)
- [Install](#install)
- [Design Notes](#design-notes)

## What It Is

CLI-based browser automation for AI agents, replacing the Playwright MCP server. Playwright
stays the engine; `shared/browserctl.py` launches the Playwright-managed Chromium directly with
`--remote-debugging-port`, one persistent profile per purpose, each on a stable CDP port. The
CLI wraps the common MCP tools (`tabs`, `navigate`, `snapshot`, `screenshot`, `eval`) as cheap
one-shot commands, and `attach()` exposes the full Playwright Python API against the same live
browser for everything else.

Why CLI over MCP: one-shot commands cost a fraction of the context of MCP tool schemas and
round-trips, multiple agent sessions can drive the same browser concurrently over CDP, and the
browser outlives any single agent turn.

## Profile Model

- **Per-project namespaces.** Every profile belongs to a project id (auto-derived from the
  nearest `ai/manifest.json`, else the git-root/CWD name; `--project` / `$BROWSERCTL_PROJECT`
  override). Projects never share profiles, ports, or logins.
- **Clean on first use.** A missing profile is created as an empty user-data dir - at project
  init (`browserctl.py init`) or on first `launch`. No login-bearing master, no inherited state.
- **Ephemeral on demand.** `launch --ephemeral` profiles are deleted on `stop` and swept by
  `prune`; `launch --fresh` spins a clean ephemeral sibling when the requested profile is busy.
  `persist` marks a profile long-lived.
- **State root:** `~/.solaris/browserctl/` (`$BROWSERCTL_HOME` to override) -
  `profiles/<project>/<name>/`, `state.json` registry, `logs/`, `out/<project>/`. Machine-local,
  disposable, never in a repo. Private per-framework so `app_bundle` pointers from other
  frameworks cannot hijack launches.

## Layout

| File | Role |
|---|---|
| `shared/browserctl.py` | The tool (PEP 723 inline deps; run via `uv run`). |
| `shared/browserctl.skill.md` | Trigger-invoked procedure + command reference. |
| `shared/slack-web.skill.md` | Use case: operating the Slack web client (capture, threads, attachments, react/post) through browserctl. |
| `shared/browserctl.rule.md` | Always-on conventions (only launch path, profile discipline, hygiene). |
| `manifest.json` | Plugin manifest (`setup` drives install-time steps). |

No `mcps.json` - shipping zero MCP servers is the point.

## Install

Under a Solaris checkout: "add plugin browserctl to `<project>`" (copy mode) or "link plugin
browserctl to `<project>`" (development). Setup ensures Playwright's Chromium is installed and
creates the project's clean `default` profile. Standalone ai-packs carry the overlay copy and
need only `uv`.

## Design Notes

Chromium is the only supported engine. Derived from a production agent framework's browser
layer that fully replaced its Playwright MCP with this pattern, adapted for Solaris:
per-project namespaces with clean first-use profiles instead of a shared login-bearing master
profile as clone source, private per-framework state root, branded app bundle (`icon` command,
auto-built on first launch), tab hygiene on launch and stop, and JSON state (stdlib + playwright
+ pillow only). Public context: agent tooling is broadly moving browser automation from MCP
servers to CLI + skills for token efficiency (e.g. microsoft/playwright-cli).
