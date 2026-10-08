# kaggle - Solaris Plugin <!-- omit in toc -->

- [What It Is](#what-it-is)
- [Files](#files)
- [Install](#install)
- [Upstream and Upgrades](#upstream-and-upgrades)
- [Changelog](#changelog)

## What It Is

Kaggle for Solaris agents through the official Kaggle CLI (Kaggle's command-line client,
`github.com/Kaggle/kaggle-cli`) and three fixed, read-only reads through Kaggle's Python SDK for
what the CLI drops: a forum topic in full, the public notebooks with their scores, and the
account's quota and runs in one read. A small gateway script pins the CLI and installs it
separately into each project or ad-hoc task that uses it, never globally, and runs the SDK reads
in that same environment. A gateway skill (`kaggle-cli.skill.md`) routes agents to that script
and to Kaggle's own agent skill, vendored at the same version (the `kaggle-cli/` folder beside it;
upstream also names that skill `kaggle-cli`). An always-on rule keeps commands and every write on
the CLI and the SDK to those reads, every write to Kaggle owner-confirmed, and credentials out of
sight. Tools build on the gateway: a leaderboard history that saves every leaderboard read,
so competitors' progress can be followed over time, and the public notebooks' scores, so a
notebook jump shows; a discussions watch that lists a competition's forum, reads the new and
changed topics in full and prints them with the new comments marked; account sharing, which
splits one Kaggle account's sessions and weekly GPU hours between the projects using it (the
gateway refuses a kernel push that no sharing lease covers); one status page per project, the page
the owner reads (state, leaderboard progress against the top teams, plan, spending, resources, open
questions and suggestions, with live figures); a pre-submit check of what is new since
the last review, a gated submit that needs that check (the gateway refuses any other submit), and
an hourly pass that runs every read-only check in one call, flags what needs a decision (board jumps,
unpushed commits, agents off their allowed models, a master effort other than the one the pack sets, failed hooks,
spend limits) and ends with a status block to paste into the status message; and a
fetch of named files from a kernel output too large for `kernels output`. A playbook carries
the know-how for competing with an autonomous agent team, from the first hour to the final picks: a core read every
session, a skill per kind of contest, and a kernel-engineering skill read before building a submission.

## Files

| File | Role |
|---|---|
| `shared/tools/kaggle.py` | Gateway (`shared/tools/` holds the plugin's scripts): finds the project root (first; the nearest folder with exactly one child folder holding an ai-pack `manifest.json`, whatever that folder's name) or task folder, keeps `kaggle==2.2.4` + `kagglesdk==0.1.37` in `<context>/.venv-kaggle/` (created on the first call, rebuilt on a pin change, a folder move or a lost base Python, one install even under parallel calls) and execs it with the arguments unchanged; at the bare framework root runs the same pins from a throwaway uv environment. With `--sdk` first it runs `kaggle_sdk.py` in that same environment instead of the CLI, its output and exit code passed through. Inside a project or task two hooks run first and never block or change a command: an activity stamp for `kaggle_share.py` (`KAGGLE_SHARE_QUIET=1` skips it; an SDK read stamps as `sdk <read>`) and a tee that saves `competitions leaderboard` reads (what `--show` printed, the zip `--download` wrote) through `kaggle_lb.py` (`KAGGLE_LB_RECORD=0` turns it off; never for an `--sdk` read); at the bare framework root the tee runs only when `KAGGLE_LB_DIR` names a store. One gate blocks: `kernels push` (or `kernels update`; `k` for `kernels`) runs only while an open account-sharing lease covers the pushed kernel (`kaggle_share.py` `push_lease`: a lease of the project around the kernel folder naming that kernel or none; outside any project, one naming that kernel); otherwise it exits 3 before any install, stamp or Kaggle call with a one-line `acquire --path` hint, and it fails closed when the lease cannot be checked. `KAGGLE_SHARE_QUIET` does not bypass it; `KAGGLE_PUSH_WITHOUT_LEASE=1`, the owner's explicit override, does. A second gate refuses a bare `competitions submit` (`c submit`) the same way, with a one-line pointer to `kaggle_submit.py`: only that tool's own call (it sets `KAGGLE_SUBMIT_GATED=1`) or `KAGGLE_SUBMIT_WITHOUT_GATE=1`, the owner's explicit override, gets through; `KAGGLE_SHARE_QUIET` does not bypass it either. Stdlib only. |
| `shared/tools/kaggle_sdk.py` | The gateway's SDK reads, run only as `kaggle.py --sdk <read>` (it imports the pinned CLI's library and signs in as the CLI does): `topic <id>` (one forum topic in full: the opening post and every comment as Kaggle's HTML, links included, each reply nested under its comment; no profile paths, usernames or tiers), `notebooks <slug> [--max N]` (up to N public notebooks, default 100, in Kaggle's score order, each with its best public score, null when unknown; `complete` false, with a note, when the list stopped short or a score could not be read) and `account [--hours H] [--page-size N] [--done REF=TIME]` (the signed-in account's quota, one page of its own kernels, and the run state of up to 25 kernels run in the last H hours, default 12, skipping finished runs that `--done` names; in one process). Read-only; each read prints one JSON document within a fixed call budget. |
| `shared/tools/kaggle_lb.py` | Leaderboard history: `snapshot`/`show` save a gzip JSON snapshot per read under `<context>/__data/kaggle/<slug>/leaderboard/`, never overwritten, partial reads flagged, `--dir`/`KAGGLE_LB_DIR`; `history`, `movers`, `new-teams`, `summary`; `record-raw` and `tee_leaderboard` (the gateway hook); `import`; `notebooks` (the public notebooks with their best public scores, through the gateway's `--sdk notebooks`: one gzip JSON list per read in `notebooks/` beside `leaderboard/`, incomplete reads flagged, printed with new notebooks and score changes marked). Public leaderboard and notebook fields only. Stdlib only. |
| `shared/tools/kaggle_forum.py` | Discussions watch: `list` (the CLI's `competitions topics list`, or pages a browser saved, `--from`, complete only with every page of `--pages N`), `diff` (new, changed and missing topics against `state.json`), `fetch` (each topic read once, in full, through the gateway's `--sdk topic`; a topic an earlier version saved is read again by the next `check` while pending), `show` (the opening post and the reply tree, HTML stripped and links kept, new comments marked; records what it printed), `commit` (records exactly that as read), and `check` (list, diff and fetch in one), under `<context>/__data/kaggle/<slug>/forum/`, `--dir`/`KAGGLE_FORUM_DIR`. Public forum content only. Stdlib only. |
| `shared/tools/kaggle_forum_list.js` | The optional browser fallback's extractor: run on a competition's discussion page (for example through browserctl's `eval`), it returns the page's topics for `kaggle_forum.py list --from`. |
| `shared/tools/kaggle_share.py` | Account sharing: active projects from account-wide runs and quota (one `--sdk account` read through the gateway) plus local gateway stamps; an equal or configured split of concurrent sessions and weekly GPU hours; leases with borrowing, kept per project folder; flock-guarded state in `~/.solaris/kaggle/` (never `~/.kaggle/`); `push_lease`, the gateway's read-only question before each `kernels push`. Stdlib only. |
| `shared/tools/kaggle_presubmit.py` | The pre-submit check: `<slug> [--vs SCORE] [--hours H]` runs `kaggle_forum.py check`, `competitions pages <slug> list <slug> --content`, `kaggle_lb.py notebooks` and `kaggle_lb.py snapshot` (read-only, unstamped) and prints what is new since the last review: host posts first and in full (hosts named in `presubmit/config.json`: `hosts`, `host_topics`), new topics and comments (with no review yet, the last 24 h), pages added, changed or removed, notebooks new or re-scored (`--vs` marks those at or better), a new #1 and top-20 moves, then a TRIGGERS block (host post, rules or scorer, reported results, failure report, forum activity, page change, public notebook, notebook at or above `--vs`, board move, read failed, no review yet); exit 10 when any matched, else 0. `<slug> --ack` records exactly what the last check showed (no Kaggle call; a source whose read failed keeps its earlier view). State in `<context>/__data/kaggle/<slug>/presubmit/` (`seen.json`, `acked.json`, `pages/<name>-<sha>.txt`); `review_status()` is the gate `kaggle_submit.py` applies. It reuses the last hourly pass's board snapshot and notebook list while that pass and each read are at most `--fresh` minutes old (default 20; `--fresh 0` reads them again) and says so on a REUSED line; a read answered with HTTP 429, in explicit words (Too Many Requests, a rate limit, or 429 beside HTTP, Client Error or status; never a bare 429), stops the check's reads (a RATE LIMITED line; the reads not made count as failed). Stdlib only. |
| `shared/tools/kaggle_submit.py` | The gated submit, a Kaggle write (`--go` only with the owner's approval of the printed command or a standing grant): `<record.json>` checks a submission record (`competition`, `file`, `message`; `kernel` and `version` in a code competition), its earlier attempts, the pre-submit check (at most `--fresh` minutes old, default 30, its findings acked) and the kernel run (`kernels status` COMPLETE), then prints the exact command; `--go --review "<one line>"` records the attempt, runs `competitions submit` once and records `submitted` or `unclear`. One run at a time per record: a run holds a non-blocking flock on `<record>.lock` from before it reads the record to its end, and a second run is refused; the submit call alone carries `KAGGLE_SUBMIT_GATED=1`, the mark the gateway wants on a submit. Exit 0 submitted (or every check passed), 3 refused, 4 unclear: never retried; read `competitions submissions` and `submission-limits` first, then `--checked`. Stdlib only. |
| `shared/tools/kaggle_hourly.py` | One hourly pass, `<slug> [--vs SCORE] [--status FILE] [--jump N%] [--json]`: the pre-submit reads and triggers (not kept as a check), the board, notebooks, unread forum topics, `competitions submissions`, `kaggle_share.py status --json` (kernels, GPU week), the status page, and in a project `git` (in the project's own repos, its root and a code repo in `source/`, the commits each upstream lacks, from git's own record; nothing fetched), `agents` (`solaris.tools.ai_spend --detail` since the last pass: the master's models and effort mix, the workers' models, the hook runs) and `spend` (Claude today against `ai.daily_budget_usd`, each category of the cost ledger, Brev from the nvidia-brev plugin's `brev-costs.md`, closed instances and the running or stopped ones its TOTAL row notes, against `brev.daily_limit_usd`; never a total), and the browsers left running, one line each, FLAG where a decision is needed (a host post or page change, a top-10 team that improved by `--jump` (default 2%) or came from below place 50, a notebook in the top 15 or at or better than `--vs`, unread topics, a score that landed or a submission that errored, a run of this project's that ended, a status JSON missing or unreadable, a status page missing, built before the JSON's last change or the newest score, or over two hours old, a project still on the retired live plan, commits unpushed for over an hour (push per the project's rules), a worker or the master on a model outside the allowed list (`claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-*`, or the pack's `kaggle.allowed_models`), a failed hook run, the master at another effort than the pack's `kaggle.master_effort` (only when the pack sets one: the effort is the owner's choice per session, so no level is assumed), a daily limit reached, a running Brev instance it cannot count while a Brev limit is set, a browser of this project's up over two hours or an orphaned report-render Chrome, a failed read); a Kaggle read answered with HTTP 429, in explicit words, stops the pass's Kaggle reads with one `rate-limited` line. It ends with a STATUS block (pick, score, rank, compute, stopped, spend, for you; n/a where it cannot tell), and `--json` prints the pass as one object; exit 10 on a flag, else 0. Wakes and schedules nothing; state in `<context>/__data/kaggle/<slug>/hourly/last.json`. Stdlib only. |
| `shared/tools/kaggle_output.py` | Named files from a kernel's output, even one of thousands of files where `kernels output` answers 429: `<owner>/<kernel> <file>... -p <folder>` takes the page token of `kernels files --page-size 1`, points it just before each wanted name, and runs `kernels output --page-token <token> --file-pattern '^<name>\Z'` through the gateway (one call per file, plus one); names stay inside `-p`, and a file counts only when written anew. Exit 0 all fetched, 1 not. Stdlib only. |
| `shared/tools/kaggle_status.py` | The project's single status page, built from a project root: the hand-edited `reports/status.json` (state, phase, plan, schedule, resources, questions, suggestions, an optional daily budget) beside live figures (the newest saved board snapshot, read fresh when over 30 minutes old; the team's submissions; the GPU week and sessions from `kaggle_share.py status --json`; `hosts.json` and `lease-ends.json`; the cost ledger `<pack>/.memory/spend.jsonl`; Claude Code spend per day from `solaris.tools.ai_spend` when a Solaris checkout is above; the newest `improvements.md` titles; n/a when a source fails). Sections: Current State, Leaderboard Progress (our score and the top teams' over time with the medal lines, and our rank), Plan and Timeline, Spending, Resources, Open Questions, Suggestions. Writes `reports/html/status.html`, then renders `reports/status.pdf` through the reporting plugin when attached (render plus layout check; a half-empty page is rendered once more with shorter charts); the PDF stays out of git. Each build raises the JSON's rev, except the hourly rebuild (`--keep-rev`) and a preview (`--out`, HTML only, no fresh read); `--offline` makes no Kaggle call, `--no-render` skips the PDF. Text is escaped apart from `<b>`, `<i>` and `<code>`, and a JSON that does not build writes nothing. Its `ai_spend()` runner (uv when on PATH, else this Python, in the checkout above) is the one `kaggle_hourly.py` uses. Stdlib only. |
| `shared/tools/kaggle_live_plan.py` | Retired in 0.7.0: a stub that points to `kaggle_status.py` and exits 2. |
| `shared/kaggle-cli.skill.md` | The Solaris gateway skill (name `kaggle-cli`, trigger "kaggle"; not the `kaggle-cli/` folder below, which is Kaggle's own skill): calling the gateway per context, its hooks, its `--sdk` reads, OAuth sign-in, routing into Kaggle's skill plus its 2.2.4 corrections, Solaris conventions, 401/403 triage. |
| `shared/kaggle-leaderboard.skill.md` | Leaderboard history and public notebook scores (`kaggle_lb.py`): what is stored and why, the commands, an hourly board and `notebooks` read at the agent's hourly pass (inside the session, never a daemon, cron job or watcher), privacy, reading progress and notebook jumps over time. |
| `shared/kaggle-discussions.skill.md` | Discussions watch (`kaggle_forum.py`): the hourly routine at the agent's hourly pass (inside the session, never a daemon, cron job or watcher), the commands, what is stored, listing without the CLI, logging insights with topic ids, privacy. |
| `shared/kaggle-sharing.skill.md` | Account sharing (`kaggle_share.py`): what is shared, detection (one `--sdk account` read plus local stamps), the split and the user's directions, the commands, the agent routine around each kernel run. |
| `shared/kaggle-checks.skill.md` | The checks around a submission (triggers such as "presubmit", "pre-submit check", "hourly check", "gated submit"): the pre-submit check and its ack (`kaggle_presubmit.py`, with the reuse of a young hourly read and the HTTP 429 stop), the gated submit and its record (`kaggle_submit.py`; no read names which kernel version ran, so check it by hand), the hourly pass, its flags, the spend line and the status block (`kaggle_hourly.py`), and the status page's rebuild (`kaggle_status.py`). |
| `shared/how-to-kaggle.skill.md` | The playbook core, read at every session start and after every compaction (triggers such as "kaggle competition", "kaggle playbook"): quick start, setup and the competition facts sheet (with the contest's kind), the status page (`kaggle_status.py`), Kaggle access, the kinds of contest and their skills, compute principles, phases, honest validation and public-first mode, daily submission discipline, agent organization, research, a pointer to the kernel skill, a pitfalls log, the settled decisions; each rule with its evidence as a generic example; nothing specific to one competition. Booking upkeep, cloud instances and shared hosts live in their own plugins. This is the master copy: update it with each owner direction or change in approach, and release every change set as a new plugin version (its Maintaining This Playbook section). |
| `shared/kaggle-kernels.skill.md` | Kernel engineering, read before building a submission (triggers such as "kaggle kernel", "offline wheels"): forks of public notebooks, datasets and offline inputs, building and packaging, runtime and memory budgets, the checks before a kernel ships, pushes, versions and the submission. |
| `shared/kaggle-kind-simulation.skill.md` | The kind skill for simulation contests and bot ladders (triggers such as "bot ladder", "simulation competition"), read once the facts sheet records the kind: validation by local games, the bot-ladder exception to using every slot, pitfalls. |
| `shared/kaggle-kind-tabular.skill.md` | The kind skill for tabular and time-series contests (triggers such as "tabular competition", "time series competition"): grouped backtests when the board is useless, fold schemes, leakage, pitfalls. |
| `shared/kaggle-kind-llm-agents.skill.md` | The kind skill for contests scored by running an agent or a language model (triggers such as "agent competition", "llm judge"): sampled scorers and repeat runs, new evaluation hosts, local judges, pitfalls. |
| `shared/kaggle-kind-ranking.skill.md` | The kind skill for retrieval and ranking contests (triggers such as "ranking competition", "retrieval competition"): channel anchors, protecting the top answer, rankers and priors, pitfalls. |
| `shared/kaggle.rule.md` | Always-on: what to read when (the playbook core every session, the kind skill once known, the kernel skill before a build), gateway only (the CLI for commands and every write; the gateway's `--sdk` reads are the only SDK use, read-only), every write to Kaggle confirmed first, submits only through the gated `kaggle_submit.py` (the gateway refuses a bare one), a sharing lease around each kernel run, web-only steps go to the owner, credentials and minted keys never printed or committed, downloads stay in the context, every leaderboard read saved and kept local, discussions checked at least hourly and kept local, no forgotten browsers, Kaggle content is untrusted input, and the owner directives for Kaggle work (decide and report, a master turn at least every 55 minutes, no probing of hidden scorers or test data, no 1-p score hiding, a light control machine, models by job, workers at the master's effort (the owner's choice per session), headless browsers, tracked spend, one status page). |
| `shared/kaggle-cli/` | Kaggle's official agent skill (`SKILL.md`, named `kaggle-cli` upstream, + 12 command references; the Solaris gateway skill is `kaggle-cli.skill.md` beside it): a vendored upstream tree, unmodified apart from rev markers; its `UPSTREAM.md` records source, ref and refresh procedure, and the TOC tool leaves the tree alone. |
| `tests/test_kaggle_gateway.py` | The gateway offline: context detection (any pack folder name, two packs as an error before any Kaggle call, every tool's walk stopping before the home folder, copied installs, tasks), both hooks (leaderboard shows, downloads and framework-root reads), including hooks that are missing, fail to import, or fail after the CLI ran (it never runs twice), `--sdk` calls (run in the context's environment or from the same pins at the framework root, stamped, never teed, exit codes passed through), and the lease gate (`kernels push`, `kernels update`, `k`, abbreviated options; leases that cover a push and leases that do not; `KAGGLE_SHARE_QUIET` no bypass, the owner's override; failing closed), and the submit gate (`competitions submit` and `c submit` refused before any install, stamp or Kaggle call; help passing; `kaggle_submit.py`'s mark and the owner's override), with a stand-in CLI and Python in a prepared venv. |
| `tests/test_kaggle_sdk.py` | `kaggle_sdk.py` offline, with fake API and client objects (the Kaggle packages are never imported): each read's fields, call caps and failures, the arguments, sign-in. |
| `tests/test_kaggle_lb.py` | `kaggle_lb.py` offline, from fixture pages and fake `--sdk notebooks` reads. |
| `tests/test_kaggle_forum.py` | `kaggle_forum.py` offline, with a fake gateway and fake listings. |
| `tests/test_kaggle_share.py` | `kaggle_share.py` offline, with a fake account and project tree. |
| `tests/test_kaggle_status.py` | `kaggle_status.py` offline, with a stand-in gateway, `kaggle_share.py` and `ai_spend` (no network, no Chrome): the root and pack from any pack name or a copied install, every section in order with the charts, escaping, sources that fail as n/a, a preview that leaves the project untouched, the rev, a JSON that does not build, the fresh board read, Claude Code spend from a checkout above, the render and its layout check with the shorter-chart retry, the HTML only without the reporting plugin; the ai_spend runner the hourly pass shares. |
| `tests/test_kaggle_live_plan.py` | The retired `kaggle_live_plan.py` stub: it points to the status page and exits 2. |
| `tests/test_kaggle_presubmit.py` | `kaggle_presubmit.py` offline, running `kaggle_forum.py` and `kaggle_lb.py` for real against a stand-in gateway: a first check, the ack, changes on every source with hosts first, an ack that records only what the check showed, failed reads keeping their earlier view, old-format and unfetched topics, the gate `kaggle_submit.py` applies, bad input; a 429 that stops the reads after it (the ack keeps their earlier views), and what reads as a rate limit. |
| `tests/test_kaggle_submit.py` | `kaggle_submit.py` offline: refusals (record, pre-submit check, kernel run, review line), the check-only run, one recorded submit carrying the gateway's mark, unclear results and interrupted attempts that block a blind retry, a file competition, one run at a time (a held record, two concurrent `--go` runs that submit once). |
| `tests/test_kaggle_hourly.py` | `kaggle_hourly.py` offline, with `kaggle_share.py` and the pre-submit reads running for real: a quiet pass, flags on the next pass (scores, errors, ended runs), failed reads that keep the last pass's view, notebooks, unread topics, host posts and page changes, the status page (stale, missing, unreadable, the retired live plan, a task folder without one); the status block and `--json`, the HTTP 429 stop as one line (and numbers or paths holding 429 that are not one), board jumps, unpushed commits against a local bare remote, the agents and spend lines from a stand-in ai_spend in a checkout above (allowed models from the pack, the master's effort judged only against the pack's `kaggle.master_effort`, hook failures, the cost ledger and the Brev ledger against their limits), the pre-submit check's reuse of a young hourly read, and the helpers (model families, `--jump`, the Brev ledger). |
| `tests/test_kaggle_output.py` | `kaggle_output.py` offline, with a stand-in that pages an output of thousands of files from a page token: the crafted tokens, names not on their page, one-file outputs, failed calls, names that would leave `-p`. |
| `tests/kaggle_standin.py` | Shared fixtures of the pre-submit, submit and hourly tests: a project with the tools copied in (a copied install) and a stand-in gateway answering from a table (after an optional delay) and logging each call with the submit mark. |
| `tests/acceptance.md` | The live acceptance runbook: before each release of the plugin, a fresh agent follows it once against live Kaggle, read-only (nothing written to Kaggle; everything made locally stays in one scratch folder, removed at the end), and ends with a short report. |
| `migrations/` | Steps for copied installs when the plugin version advances (`0.2.0.md`: the moved gateway and the renamed skill; `0.7.0.md`: from the live plan to the status page). |

`manifest.json` and `revisions.json` (rev ledger, managed by `solaris.tools.revs`) complete
the plugin. No MCP servers: Kaggle's official MCP server only searches and downloads, which the
CLI already covers. Run the tests from the Solaris root (stdlib, offline):
`python3 -m unittest discover -s plugins/kaggle/tests`.

## Install

"install plugin kaggle to `<project>`" (copy) or "link plugin kaggle to `<project>`" (link
mode); for an ad-hoc task, add `kaggle` to its `Plugins:` line. Then sign in once per machine
(the `kaggle-cli` skill's Signing In section).

Agents call the gateway from the project root or task folder (the `kaggle-cli` skill's Calling
the Gateway table has every context):

- copied install: `python3 <pack>/plugins/kaggle/tools/kaggle.py <args>`, where `<pack>` is
  the project's ai-pack folder (default `aipack/`, `ai/` in older projects, any name);
- linked install or ad-hoc task: `python3 <solaris>/plugins/kaggle/shared/tools/kaggle.py <args>`
  (from a grouped project root: `python3 ../../../plugins/kaggle/shared/tools/kaggle.py <args>`).

The other tools sit beside the gateway (`tools/kaggle_lb.py`, `tools/kaggle_forum.py`,
`tools/kaggle_share.py`, `tools/kaggle_presubmit.py`, `tools/kaggle_submit.py`,
`tools/kaggle_hourly.py`, `tools/kaggle_output.py`, and `tools/kaggle_status.py`, which runs
from a project root) and are called the same way. `tools/kaggle_sdk.py` runs only through the
gateway, as `<gateway> --sdk <read>`, since it needs the pinned CLI's environment.

## Upstream and Upgrades

Pinned to the latest release of the newest minor line: CLI `kaggle==2.2.4` with the SDK it
was tested with (`kagglesdk==0.1.37`, which holds the auth/HTTP/API code), and Kaggle's
skill from the same tag (`shared/kaggle-cli/UPSTREAM.md` records the source and ref). The pins
are exact rather than `~=2.2.4`, so every project resolves the same tested CLI and the
vendored skill always matches it; patch releases arrive by moving the pin. The vendored files
are Kaggle's, under Apache-2.0. Move the pin as one change:

1. Set `PIN` in `shared/tools/kaggle.py` to the new release and `SDK_PIN` to the kagglesdk version a
   fresh install of it resolves to.
2. Refresh `shared/kaggle-cli/` from the matching tag (procedure in its `UPSTREAM.md`).
3. Update the version mentions in this README, the skills (`kaggle-cli.skill.md`,
   `how-to-kaggle.skill.md`), the rule and the manifest;
   `revs bump` the edited files and run `revs ledger`.
4. Test through the gateway, the `--sdk` reads included: `kaggle_sdk.py` calls the CLI's library
   and kagglesdk directly. Consumers reinstall on their next call (copied installs after a plugin
   update).

Upgrading a copied install from 0.1.0: the gateway moved to `tools/kaggle.py` and the gateway
skill became `kaggle-cli.skill.md`, so an update must also remove the old files and repoint
every call, script and allow rule that names the old gateway path - `migrations/0.2.0.md`.
Updating a copied install from 0.6.x: the live plan and the phase progress reports give way to
the status page - `migrations/0.7.0.md`.

## Changelog

- 0.8.0: the playbook splits into a core read every session (77 KB down to 64 KB), four kind skills
  (`kaggle-kind-simulation`, `-tabular`, `-llm-agents`, `-ranking`) read once the facts sheet records the contest's
  kind, and `kaggle-kernels` read before building a submission; lease upkeep moves to the booking plugin, cloud
  instances to the cloud-GPU plugin, shared-host practice to `resource-sharing`; every tagged rule keeps its line.
  The rule says what to read when, and that workers inherit the master's effort, the owner's choice per session (no
  file fixes a level). The hourly pass ends with a STATUS block to paste into the status message (pick: today's slots
  of the status JSON's `daily_slots`, the pending submissions and its optional `pick` text; score and the medal
  lines; rank; compute: `hosts.json`, the nearest booking end, the Kaggle GPU week; stopped kernel runs; spend; for
  you: open questions, owner actions and the FLAG count; `--json` prints the pass as one object). New flags: a top
  team's jump on the board (`--jump`, default 2%, or an entry into the top 10 from below place 50), a status page
  built before the newest score, commits unpushed for over an hour in the project's root or `source/` repo, a worker or the master on a model outside the
  allowed list (`kaggle.allowed_models` overrides it), failed hook runs and, only where the pack sets
  `kaggle.master_effort`, the master at another effort (the mix is always shown), all from
  `solaris.tools.ai_spend --detail`, and a daily spend limit reached (Claude against `ai.daily_budget_usd`, each
  cost-ledger category, Brev from `brev-costs.md` with its running and stopped instances against
  `brev.daily_limit_usd`; no total). An HTTP 429, in explicit words, stops the pass's Kaggle reads with one `rate-limited` line instead of a flag per read. The pre-submit check reuses a young
  hourly board snapshot and notebook list (`--fresh`, default 20 minutes) and stops at a 429 too. The checks skill
  says no read names which kernel version a run or submission used, and how to check by hand. Project steps:
  `migrations/0.8.0.md`.
- 0.7.0: one status page per project replaces every live PDF (the live plan and the phase progress
  reports). `tools/kaggle_status.py` builds `reports/status.pdf` (HTML at `reports/html/status.html`,
  rendered through the reporting plugin with its layout check) from the hand-edited
  `reports/status.json` plus live figures: Current State, Leaderboard Progress (our score and the top
  teams' over time from the saved snapshots, with the medal lines and our rank), Plan and Timeline,
  Spending (Claude Code per day from `solaris.tools.ai_spend` when a Solaris checkout is above, plus
  the cost ledger `<pack>/.memory/spend.jsonl`, against an optional daily budget), Resources (each
  machine, service or account with its cost, status and use, merged with `hosts.json` and
  `lease-ends.json`), Open Questions and Suggestions. The PDF holds private operations data and stays
  out of git. `kaggle_live_plan.py` is retired (a stub that points to the status page and exits 2);
  the hourly pass checks the status page instead (`--status FILE` replaces `--plan FILE`). The rule
  gains the owner directives for Kaggle work: decide and report; a master turn at least every 55
  minutes; no probing of hidden scorers or test data; no 1-p score hiding; a light control machine;
  models by job (Opus 5.5 for the master and every judgment job, Sonnet 5.5 for technical-only
  jobs, Haiku for read-only sweeps), replacing the frontier tier for every worker; workers at the
  master's max effort; headless browsers; tracked spend; the status page. The playbook gains a
  Settled Decisions list, the owner's newer directions for every Kaggle project, four corrections
  from a review of past winners' writeups, and a pointer to `solaris.tools.housekeeping` for disk
  hygiene. The checks skill sets the pre-submit review baseline at adoption, keeps Kaggle reads away
  from the pre-submit check (HTTP 429) and git-ignores the submit lock files (`*.json.lock`); the CLI
  skill pages `kernels list` for name checks and keeps `docker_image` pinned. Hourly reads run inside
  the session only: the leaderboard and discussions skills drop the host scheduler. Updating a copy
  from 0.6.x moves the project to the status page: `migrations/0.7.0.md`.
- 0.6.0: the hourly pass gains a `browsers` line (one `ps` read: this project's browserctl browsers
  and their age, orphaned report-render Chromes, and a count of the other browsers, never flagged),
  and the rule asks agents to close every browser they start and to check again before a pause or
  handover. The playbook sends a project's suggestions for the framework or a plugin to its private
  `<pack>/.memory/improvements.md` instead of editing those files, keeps the owner-directions log
  private in `<pack>/.memory/directions.md` (long-standing directions folded into
  `instructions.md`), and gains lessons on ranking agent families, queue depth, capped clocks,
  notebook forks, tracing uploaded notebook copies and retrained-checkpoint swaps. A plain copy
  update; state from 0.5.0 carries over.
- 0.5.0: practices two autonomous competition projects built for themselves, made generic. A
  pre-submit check, `tools/kaggle_presubmit.py <slug>`: it runs the forum check, the competition
  pages, the public notebooks and a board snapshot, read-only, and prints what is new since the last
  review (host posts first and in full, hosts named in `presubmit/config.json`; new topics and
  comments; page changes with both texts saved to diff; new or re-scored notebooks, `--vs` marking
  those at or better than a score; a new #1 and top-20 moves) with a TRIGGERS block, exiting 10 when
  something needs review and 0 when nothing does; `--ack` records exactly what the check showed, so
  nothing that landed after it counts as read. A gated submit, `tools/kaggle_submit.py <record.json>`,
  a Kaggle write run only with the owner's approval or standing grant: it refuses (exit 3) unless the
  submission record is complete, no earlier attempt landed or may have landed, the pre-submit check is
  at most 30 minutes old with its findings acked, the kernel run is COMPLETE and a one-line review is
  given; it prints the exact command, and with `--go` submits once and records `submitted` or
  `unclear` (exit 4), never retrying: after an unclear result, read `competitions submissions` and
  `submission-limits` first; one run at a time per record (a lock beside it). An hourly pass, `tools/kaggle_hourly.py <slug>`: every read-only check
  in one call (the pre-submit reads and triggers, board, notebooks, unread topics, submissions,
  kernels, GPU week, live plan age, browsers left running), one line each, FLAG where a decision is needed, exit 10 on a
  flag; it wakes and schedules nothing. A fetch of named files from a kernel output of thousands of
  files, where `kernels output` answers 429: `tools/kaggle_output.py <owner>/<kernel> <file>... -p
  <folder>` points the page token of `kernels files` just before each name. The gateway now refuses
  `kernels push` (and `kernels update`) with exit 3, before any Kaggle call, when no open
  account-sharing lease covers the pushed kernel (`kaggle_share.py` gains `push_lease`);
  `KAGGLE_SHARE_QUIET` does not bypass the gate and `KAGGLE_PUSH_WITHOUT_LEASE=1` is the owner's
  override. It refuses a bare `competitions submit` the same way: only `kaggle_submit.py`'s own call
  gets through, or `KAGGLE_SUBMIT_WITHOUT_GATE=1`, the owner's override. The rule runs Kaggle workers
  on the frontier tier. A new skill, `kaggle-checks.skill.md`, and offline tests for each tool. A
  plain copy update; state from 0.4.0 carries over, pushes now need the lease the rule already asked
  for (`kaggle_share.py acquire --path <kernel dir>`), and submits go through `kaggle_submit.py`.
- 0.4.0: three read-only SDK reads behind the gateway: `kaggle.py --sdk <read>` runs the new
  `tools/kaggle_sdk.py` in the same pinned environment, for `topic <id>` (one topic in full: the
  opening post as HTML with its links, and the nested reply tree), `notebooks <slug>` (the public
  notebooks with their best public scores, null when unknown) and `account` (the account's own
  quota, kernels and run states). The activity stamp records these calls (`sdk topic` and so on);
  the leaderboard tee does not apply. The rule now reads: the CLI for commands and every write, the
  gateway's `--sdk` reads as the only SDK use, read-only. The discussions watch reads each topic
  once through `--sdk topic`, with no second CLI view to stitch (a topic an earlier version saved is
  kept until a fetch reads it again, as `check` does while it is pending). The leaderboard history's
  `notebooks <slug>` saves the public notebooks' scores under `__data/kaggle/<slug>/notebooks/` and
  marks new notebooks and score changes, and account sharing scans the account in one
  `--sdk account` read (about 5 s, instead of one CLI process per call). A live plan tool,
  `tools/kaggle_live_plan.py`, builds the owner's plan page (HTML, then a PDF through the reporting
  plugin when attached) from a hand-edited plan JSON plus live reads, with plan text escaped, atomic
  writes and `--keep-rev` for the hourly rebuild. The playbook gains past winners' writeups, a
  kernel replay recipe, pulling one notebook version, a score ladder with each score marked measured
  or claimed, a pre-push metadata check, no blind resubmit, dataset upload gotchas
  (`datasets metadata --update` makes a dataset public unless the file says private) and a merged
  team's one daily limit, plus lessons on the live plan, medal lines and ties, slot order, a daily
  ladder built before the reset, a forum check right before each submission, two predictions per
  candidate, stacking on the strongest base and extending host leases well before they end. The
  skills run the hourly checks at the agent's hourly pass, and a host scheduler (cron, launchd) only
  when the owner approved one. New offline tests for the SDK reads and the live plan, and
  `tests/acceptance.md`, a live, read-only runbook a fresh agent follows before each release. A
  plain copy update; state from 0.3.0 carries over.
- 0.3.0: a discussions watch, `tools/kaggle_forum.py` with the `kaggle-discussions` skill and a rule
  line: it lists a competition's forum through the CLI's `competitions topics list` (or pages a
  browser saved, with the extractor `tools/kaggle_forum_list.js`), diffs against what was read,
  fetches new and changed topics through the gateway and prints them with new comments marked, and
  records as read only what it printed, so a check between reading and committing loses nothing; state
  in `__data/kaggle/<slug>/forum/`, reading an earlier `{comments, last_seen, title}` state file as it
  is. The playbook gains the hourly forum check, watching public notebooks for jumps, taking a fork's
  image from a version that ran, answer classes as retrieval gaps, public single-channel probes as
  board anchors, and visible-test rates that do not transfer. Account sharing records a failed or old
  account read at `acquire`, so no run from before such a lease closes it. A plain copy update; state
  from 0.2.x carries over.
- 0.2.1: account sharing keeps leases per project folder (two projects of one folder name no longer
  share a count), finds embedded grouped projects, uses `scan_hours` from `sharing.json`, closes a
  lease on its kernel's new run despite clock differences, accepts kernel ids containing "insert",
  and its routine releases the lease after a failed or declined push; the gateway also saves
  leaderboard downloads, reads of the CLI's default competition, and framework-root reads when
  `KAGGLE_LB_DIR` is set. A plain copy update; state from 0.2.0 carries over.
