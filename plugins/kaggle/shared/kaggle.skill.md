---
name: kaggle
triggers: ["kaggle"]
summary: Gateway to Kaggle for Solaris agents - runs the pinned Kaggle CLI (installed per project or task, never globally) and routes to Kaggle's own agent skill (vendored kaggle-cli/SKILL.md + command references) for commands, flags and metadata files. Covers the first-run install, OAuth sign-in, Solaris conventions, and 401/403 triage.
---
_Rev. 1_

# Skill: kaggle - Kaggle Through the Pinned CLI <!-- omit in toc -->

- [When to Use](#when-to-use)
- [Calling the Gateway](#calling-the-gateway)
- [Signing In](#signing-in)
- [Kaggle's Own Skill](#kaggles-own-skill)
- [Solaris Conventions](#solaris-conventions)
- [Troubleshooting](#troubleshooting)

## When to Use

Anything that touches Kaggle: finding competitions and reading their pages, downloading data,
submitting and reading scores, running notebooks (Kaggle calls them kernels) on Kaggle GPUs,
datasets, models, discussions, benchmarks, GPU quota. The Kaggle CLI (Kaggle's official
command-line client) is the only channel - this plugin does no browser automation.

## Calling the Gateway

Run every Kaggle command through `kaggle.py` from this plugin, arguments unchanged:

| Context | Command | Run from |
|---|---|---|
| Project, plugin copied | `python3 ai/plugins/kaggle/kaggle.py <args>` | project root |
| Project, plugin linked; ad-hoc task | `python3 <solaris>/plugins/kaggle/shared/kaggle.py <args>` | project root / task folder |
| Framework root | the same live path | Solaris root |

- **Where the CLI lives.** The first call installs `kaggle==2.2.4` (with `kagglesdk==0.1.37`,
  the SDK it was tested with) into `<context>/.venv-kaggle/` (about a second on a warm uv
  cache; the venv git-ignores itself); later calls run it directly. The context is the nearest
  project root above the working directory (a folder holding `ai/manifest.json`), else the
  nearest task folder (its `notes.md` header names the ad-hoc-task skill) - so `cd` there
  first. A copied overlay also finds its project from its own location. With neither (the
  framework root, or an old task without that header) the gateway runs the same pinned CLI
  from a throwaway uv environment and says so on stderr.
- **Self-healing.** A changed pin (plugin update), a moved folder, or a removed base Python
  triggers a clean reinstall on the next call; parallel first calls wait for a single install;
  deleting `.venv-kaggle/` is a safe reset. Never `pip install kaggle`, `uv tool install
  kaggle`, or call a bare `kaggle` from PATH.
- **Output.** Gateway notes go to stderr; stdout is the CLI's own output, and it can carry
  notices before the data: `Next Page Token = ...` on paginated commands, `Using competition:`,
  and an out-of-date warning once Kaggle ships past the pin. To parse, use `--format json` and
  read from the first line that starts with `[` or `{`.

## Signing In

Once per machine: credentials live in `~/.kaggle/` and serve every project and task.

1. **Check:** `<gateway> config view` shows the signed-in username and auth method (no secret
   values).
2. **Sign in:** run `PYTHONUNBUFFERED=1 <gateway> auth login` as a background task (without
   the variable, Python buffers the output and the URL only appears after the login ends). It
   opens the owner's default browser on Kaggle's consent page, prints the same URL, and waits
   with no timeout on a localhost callback (127.0.0.1, a random port 8000-9000). Give the owner
   the URL in case no tab opened. After they approve it prints
   `You are now logged in as [<user>]` and writes `~/.kaggle/credentials.json` (scope
   `resources.admin:*` - full account access).
3. **No browser on this machine** (SSH, headless): `auth login --no-launch-browser` prints the
   URL and reads a verification code from stdin, so the owner runs that one in their own
   terminal. Or the owner creates an API token at `https://www.kaggle.com/settings/api` and
   saves it as `~/.kaggle/access_token` (or exports `KAGGLE_API_TOKEN`); a token takes
   precedence over the OAuth login. Agents never handle token values.
4. **Wrong account:** `auth login --force` replaces the login; `auth revoke` ends it.

## Kaggle's Own Skill

`kaggle-cli/` next to this file is Kaggle's official agent skill, copied unmodified (apart from
rev markers) from `github.com/Kaggle/kaggle-cli` at tag `v2.2.4` - the version the gateway
installs. `kaggle-cli/SKILL.md` holds the command tree and a reference map; read only the
reference the task needs: `kaggle-cli/references/competitions.md`, `kernels.md`,
`datasets.md`, `models.md`, `model_variations.md`, `model_variations_versions.md`, `files.md`,
`forums.md`, `benchmarks.md`, `configuration.md`, `auth.md`, `quota.md`. Apply it with two
substitutions: every `kaggle <args>` becomes the gateway command above, and its install step
(`pip install kaggle`) is the gateway's job. Where a flag is unclear,
`<gateway> <group> <command> --help` is authoritative for the pinned version.

Corrections to it, verified against 2.2.4:

- **Competition pages:** the documented `competitions pages <slug>` fails ("invalid choice");
  2.2.4's parser needs the slug twice:
  `competitions pages <slug> list <slug> [--page-name rules] [--content] [--format json]`.
  This is how agents read a competition's description, rules and data description.
- **Incomplete command tree:** it omits commands 2.2.4 has, e.g. `competitions
  submission-limits`, `benchmarks leaderboard`, and the competition-host commands. Read its
  "do not invent commands" line as "the live `--help` wins".
- **`kernels output` trusts server file names:** 2.2.4 writes each file to `<-p dir>/<name the
  server sent>` without sanitizing it, so a crafted name can land outside `-p` (fixed upstream
  after 2.2.4). For someone else's kernel, list the files with `kernels files` first and fetch
  only listed names without `/` or `..`, anchored: `--file-pattern '^<name>$'` (the pattern is
  an unanchored regex search).

## Solaris Conventions

- **Name the competition** on every command. `config set competition` writes a machine-wide
  default that every other project would silently inherit.
- **Downloads** always take an explicit output path inside the context:
  `-p <absolute path>` (e.g. `<project>/data/kaggle/<slug>`), or `-o` for
  `benchmarks tasks download` - never the Solaris root. Data and outputs stay out of git.
- **Submissions:** check `competitions submission-limits <slug>` first; afterwards read the
  score with `competitions submissions <slug>` (2.2.4 has no `submit --wait`; poll sparingly).

## Troubleshooting

- **403 on download / submissions / submit:** usually this account has not accepted the
  competition's rules (`competitions list` shows `userHasEntered` False) - there is no CLI
  path; the owner accepts at `https://www.kaggle.com/competitions/<slug>/rules`, then retry.
  Before that, `files`, `leaderboard`, `submission-limits` and `pages` already work. The same
  bare 403 comes back for a gated model whose license the owner has not accepted, and for GPU
  or internet notebooks on an account without phone verification.
- **"Authentication required" / 401:** not signed in, or the login expired - redo Signing In.
- **429:** rate limited. The CLI retries on its own only for uploads, dataset/model creation
  and benchmarks calls; after a 429 from anything else (lists, downloads, submit, leaderboard)
  back off yourself - never loop.
- **Versioned kernel refs** (`<user>/<kernel>/<N>`): in 2.2.4, `kernels output`, `status`,
  `files` and `logs` may silently use the latest version instead of N - confirm the version
  in what comes back.