_Rev. 2_

# Rule: browserctl conventions <!-- omit in toc -->

- [The Browser Layer](#the-browser-layer)
- [Profile Discipline](#profile-discipline)
- [Outputs and Hygiene](#outputs-and-hygiene)

Always-on while this plugin is attached. House rules for browser automation - the procedure and
full command reference live in [`browserctl.skill.md`](browserctl.skill.md).

## The Browser Layer

- **All browser automation in this project runs through browserctl** (`browserctl.py` in this
  plugin's overlay), not through a Playwright MCP server, ad-hoc `playwright` launches, or bare
  Chromium. If a Playwright MCP is configured in this project, prefer browserctl and flag the
  MCP entry as removable.
- Never launch Chromium on a browserctl profile by any other means: browserctl launches with
  `--use-mock-keychain`, and a foreign launch silently purges every cookie in the profile.
- One-liner health check: `uv run <overlay>/browserctl.py status`.

## Profile Discipline

- This project's browser state is namespaced under its own project id (auto-derived from the
  ai-pack manifest) - never reuse another project's profiles or pass a foreign `--project`.
- The standing profile is `default`, created **clean** on project init or first use. Use
  purpose-named profiles (`<purpose>`, kebab-case) for parallel flows, and `--ephemeral` for
  one-off jobs that should leave no state behind; `--fresh` when the requested profile is busy.
- Logins live in profiles, not in files: to authenticate, launch headed, let the user sign in
  once, and rely on profile persistence. Never write session cookies or tokens into the repo or
  the ai-pack; profile names and hosts worth remembering go in `ai/.memory/resources.md`.

## Outputs and Hygiene

- Snapshots/screenshots default to `~/.browserctl/out/<project>/` (machine-local). Copy anything
  worth keeping into the project deliberately; **never auto-commit captures**.
- Stop ephemeral profiles when their job ends (stop deletes them) and run `prune` at natural
  hygiene points; leave `default` (and other persistent profiles) running only when a standing
  task needs the session held open.
- **Sandboxed harnesses (home dir not writable):** if a command fails with a permission error
  on `~/.browserctl/` (e.g. `.state.lock`), retry once with the state root moved into writable
  scratch - `BROWSERCTL_HOME=<harness scratch dir or workspace>/__browserctl/` - and say you
  did. If `uv` itself fails on a home-cache permission error first, also set
  `UV_CACHE_DIR=<same scratch>/uv-cache`. Profiles created this way are machine-and-session
  local; treat them as ephemeral.
