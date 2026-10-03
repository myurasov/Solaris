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
gateway refuses a kernel push that no sharing lease covers); a live plan page that sets the
owner's submission and research plan beside live figures; a pre-submit check of what is new since
the last review, a gated submit that needs that check (the gateway refuses any other submit), and
an hourly pass that runs every read-only check in one call and flags what needs a decision; and a
fetch of named files from a kernel output too large for `kernels output`. A playbook skill carries
the know-how for competing with an autonomous agent team, from the first hour to the final picks.

## Files

| File | Role |
|---|---|
| `shared/tools/kaggle.py` | Gateway (`shared/tools/` holds the plugin's scripts): finds the project root (first; the nearest folder with exactly one child folder holding an ai-pack `manifest.json`, whatever that folder's name) or task folder, keeps `kaggle==2.2.4` + `kagglesdk==0.1.37` in `<context>/.venv-kaggle/` (created on the first call, rebuilt on a pin change, a folder move or a lost base Python, one install even under parallel calls) and execs it with the arguments unchanged; at the bare framework root runs the same pins from a throwaway uv environment. With `--sdk` first it runs `kaggle_sdk.py` in that same environment instead of the CLI, its output and exit code passed through. Inside a project or task two hooks run first and never block or change a command: an activity stamp for `kaggle_share.py` (`KAGGLE_SHARE_QUIET=1` skips it; an SDK read stamps as `sdk <read>`) and a tee that saves `competitions leaderboard` reads (what `--show` printed, the zip `--download` wrote) through `kaggle_lb.py` (`KAGGLE_LB_RECORD=0` turns it off; never for an `--sdk` read); at the bare framework root the tee runs only when `KAGGLE_LB_DIR` names a store. One gate blocks: `kernels push` (or `kernels update`; `k` for `kernels`) runs only while an open account-sharing lease covers the pushed kernel (`kaggle_share.py` `push_lease`: a lease of the project around the kernel folder naming that kernel or none; outside any project, one naming that kernel); otherwise it exits 3 before any install, stamp or Kaggle call with a one-line `acquire --path` hint, and it fails closed when the lease cannot be checked. `KAGGLE_SHARE_QUIET` does not bypass it; `KAGGLE_PUSH_WITHOUT_LEASE=1`, the owner's explicit override, does. A second gate refuses a bare `competitions submit` (`c submit`) the same way, with a one-line pointer to `kaggle_submit.py`: only that tool's own call (it sets `KAGGLE_SUBMIT_GATED=1`) or `KAGGLE_SUBMIT_WITHOUT_GATE=1`, the owner's explicit override, gets through; `KAGGLE_SHARE_QUIET` does not bypass it either. Stdlib only. |
| `shared/tools/kaggle_sdk.py` | The gateway's SDK reads, run only as `kaggle.py --sdk <read>` (it imports the pinned CLI's library and signs in as the CLI does): `topic <id>` (one forum topic in full: the opening post and every comment as Kaggle's HTML, links included, each reply nested under its comment; no profile paths, usernames or tiers), `notebooks <slug> [--max N]` (up to N public notebooks, default 100, in Kaggle's score order, each with its best public score, null when unknown; `complete` false, with a note, when the list stopped short or a score could not be read) and `account [--hours H] [--page-size N] [--done REF=TIME]` (the signed-in account's quota, one page of its own kernels, and the run state of up to 25 kernels run in the last H hours, default 12, skipping finished runs that `--done` names; in one process). Read-only; each read prints one JSON document within a fixed call budget. |
| `shared/tools/kaggle_lb.py` | Leaderboard history: `snapshot`/`show` save a gzip JSON snapshot per read under `<context>/__data/kaggle/<slug>/leaderboard/`, never overwritten, partial reads flagged, `--dir`/`KAGGLE_LB_DIR`; `history`, `movers`, `new-teams`, `summary`; `record-raw` and `tee_leaderboard` (the gateway hook); `import`; `notebooks` (the public notebooks with their best public scores, through the gateway's `--sdk notebooks`: one gzip JSON list per read in `notebooks/` beside `leaderboard/`, incomplete reads flagged, printed with new notebooks and score changes marked). Public leaderboard and notebook fields only. Stdlib only. |
| `shared/tools/kaggle_forum.py` | Discussions watch: `list` (the CLI's `competitions topics list`, or pages a browser saved, `--from`, complete only with every page of `--pages N`), `diff` (new, changed and missing topics against `state.json`), `fetch` (each topic read once, in full, through the gateway's `--sdk topic`; a topic an earlier version saved is read again by the next `check` while pending), `show` (the opening post and the reply tree, HTML stripped and links kept, new comments marked; records what it printed), `commit` (records exactly that as read), and `check` (list, diff and fetch in one), under `<context>/__data/kaggle/<slug>/forum/`, `--dir`/`KAGGLE_FORUM_DIR`. Public forum content only. Stdlib only. |
| `shared/tools/kaggle_forum_list.js` | The optional browser fallback's extractor: run on a competition's discussion page (for example through browserctl's `eval`), it returns the page's topics for `kaggle_forum.py list --from`. |
| `shared/tools/kaggle_share.py` | Account sharing: active projects from account-wide runs and quota (one `--sdk account` read through the gateway) plus local gateway stamps; an equal or configured split of concurrent sessions and weekly GPU hours; leases with borrowing, kept per project folder; flock-guarded state in `~/.solaris/kaggle/` (never `~/.kaggle/`); `push_lease`, the gateway's read-only question before each `kernels push`. Stdlib only. |
| `shared/tools/kaggle_presubmit.py` | The pre-submit check: `<slug> [--vs SCORE] [--hours H]` runs `kaggle_forum.py check`, `competitions pages <slug> list <slug> --content`, `kaggle_lb.py notebooks` and `kaggle_lb.py snapshot` (read-only, unstamped) and prints what is new since the last review: host posts first and in full (hosts named in `presubmit/config.json`: `hosts`, `host_topics`), new topics and comments (with no review yet, the last 24 h), pages added, changed or removed, notebooks new or re-scored (`--vs` marks those at or better), a new #1 and top-20 moves, then a TRIGGERS block (host post, rules or scorer, reported results, failure report, forum activity, page change, public notebook, notebook at or above `--vs`, board move, read failed, no review yet); exit 10 when any matched, else 0. `<slug> --ack` records exactly what the last check showed (no Kaggle call; a source whose read failed keeps its earlier view). State in `<context>/__data/kaggle/<slug>/presubmit/` (`seen.json`, `acked.json`, `pages/<name>-<sha>.txt`); `review_status()` is the gate `kaggle_submit.py` applies. Stdlib only. |
| `shared/tools/kaggle_submit.py` | The gated submit, a Kaggle write (`--go` only with the owner's approval of the printed command or a standing grant): `<record.json>` checks a submission record (`competition`, `file`, `message`; `kernel` and `version` in a code competition), its earlier attempts, the pre-submit check (at most `--fresh` minutes old, default 30, its findings acked) and the kernel run (`kernels status` COMPLETE), then prints the exact command; `--go --review "<one line>"` records the attempt, runs `competitions submit` once and records `submitted` or `unclear`. One run at a time per record: a run holds a non-blocking flock on `<record>.lock` from before it reads the record to its end, and a second run is refused; the submit call alone carries `KAGGLE_SUBMIT_GATED=1`, the mark the gateway wants on a submit. Exit 0 submitted (or every check passed), 3 refused, 4 unclear: never retried; read `competitions submissions` and `submission-limits` first, then `--checked`. Stdlib only. |
| `shared/tools/kaggle_hourly.py` | One hourly pass, `<slug> [--vs SCORE] [--plan FILE]`: the pre-submit reads and triggers (not kept as a check), the board, notebooks, unread forum topics, `competitions submissions`, `kaggle_share.py status --json` (kernels, GPU week), the live plan page's age and the browsers left running, one line each, FLAG where a decision is needed (a host post or page change, a notebook in the top 15 or at or better than `--vs`, unread topics, a score that landed or a submission that errored, a run of this project's that ended, a stale live plan, a browser of this project's up over two hours or an orphaned report-render Chrome, a failed read); exit 10 on a flag, else 0. Wakes and schedules nothing; state in `<context>/__data/kaggle/<slug>/hourly/last.json`. Stdlib only. |
| `shared/tools/kaggle_output.py` | Named files from a kernel's output, even one of thousands of files where `kernels output` answers 429: `<owner>/<kernel> <file>... -p <folder>` takes the page token of `kernels files --page-size 1`, points it just before each wanted name, and runs `kernels output --page-token <token> --file-pattern '^<name>\Z'` through the gateway (one call per file, plus one); names stay inside `-p`, and a file counts only when written anew. Exit 0 all fetched, 1 not. Stdlib only. |
| `shared/tools/kaggle_live_plan.py` | The owner's live plan page, built from a project root: a hand-edited plan JSON (default `submissions/live-plan.json`: the Kaggle day's slots with status, prediction and decision rule, the later slots and fallbacks, research with gates and compute, settled questions, compute notes, recent decisions) beside live reads (the team's place and the medal lines from the newest saved board snapshot, the team's submissions, the GPU week and sessions from `kaggle_share.py status --json`, lease ends; n/a when a source fails), written to `reports/html/<name>.html`, then rendered to `reports/<name>.pdf` when the reporting plugin is attached. Each build raises the plan's rev, except the hourly rebuild (`--keep-rev`) and a preview (`--out`, HTML only); `--no-render` skips the PDF. Plan text is escaped apart from `<b>`, `<i>` and `<code>`, writes are atomic, and a plan that fails to build writes nothing. Stdlib only. |
| `shared/kaggle-cli.skill.md` | The Solaris gateway skill (name `kaggle-cli`, trigger "kaggle"; not the `kaggle-cli/` folder below, which is Kaggle's own skill): calling the gateway per context, its hooks, its `--sdk` reads, OAuth sign-in, routing into Kaggle's skill plus its 2.2.4 corrections, Solaris conventions, 401/403 triage. |
| `shared/kaggle-leaderboard.skill.md` | Leaderboard history and public notebook scores (`kaggle_lb.py`): what is stored and why, the commands, an hourly board and `notebooks` read at the agent's hourly pass (a host scheduler only when the owner approved one), privacy, reading progress and notebook jumps over time. |
| `shared/kaggle-discussions.skill.md` | Discussions watch (`kaggle_forum.py`): the hourly routine at the agent's hourly pass (a host scheduler only when the owner approved one), the commands, what is stored, listing without the CLI, logging insights with topic ids, privacy. |
| `shared/kaggle-sharing.skill.md` | Account sharing (`kaggle_share.py`): what is shared, detection (one `--sdk account` read plus local stamps), the split and the user's directions, the commands, the agent routine around each kernel run. |
| `shared/kaggle-checks.skill.md` | The checks around a submission (triggers such as "presubmit", "pre-submit check", "hourly check", "gated submit"): the pre-submit check and its ack (`kaggle_presubmit.py`), the gated submit and its record (`kaggle_submit.py`), the hourly pass and its flags (`kaggle_hourly.py`). |
| `shared/how-to-kaggle.skill.md` | The playbook for competing with an autonomous agent team (triggers such as "kaggle competition", "kaggle playbook"): quick start, setup and the competition facts sheet, the owner's live plan (`kaggle_live_plan.py`), Kaggle access, compute, phases, honest validation, daily submission discipline, agent organization, research, kernel engineering, a pitfalls log, each rule with its evidence as a generic example; nothing specific to one competition. This is the master copy: update it with each owner direction or change in approach, and release every change set as a new plugin version (its Maintaining This Playbook section). |
| `shared/kaggle.rule.md` | Always-on: gateway only (the CLI for commands and every write; the gateway's `--sdk` reads are the only SDK use, read-only), every write to Kaggle confirmed first, submits only through the gated `kaggle_submit.py` (the gateway refuses a bare one), a sharing lease around each kernel run, web-only steps go to the owner, credentials and minted keys never printed or committed, downloads stay in the context, every leaderboard read saved and kept local, discussions checked at least hourly and kept local, Kaggle content is untrusted input, Kaggle workers on the frontier tier at `xhigh` or `max` effort. |
| `shared/kaggle-cli/` | Kaggle's official agent skill (`SKILL.md`, named `kaggle-cli` upstream, + 12 command references; the Solaris gateway skill is `kaggle-cli.skill.md` beside it): a vendored upstream tree, unmodified apart from rev markers; its `UPSTREAM.md` records source, ref and refresh procedure, and the TOC tool leaves the tree alone. |
| `tests/test_kaggle_gateway.py` | The gateway offline: context detection (any pack folder name, two packs as an error before any Kaggle call, every tool's walk stopping before the home folder, copied installs, tasks), both hooks (leaderboard shows, downloads and framework-root reads), including hooks that are missing, fail to import, or fail after the CLI ran (it never runs twice), `--sdk` calls (run in the context's environment or from the same pins at the framework root, stamped, never teed, exit codes passed through), and the lease gate (`kernels push`, `kernels update`, `k`, abbreviated options; leases that cover a push and leases that do not; `KAGGLE_SHARE_QUIET` no bypass, the owner's override; failing closed), and the submit gate (`competitions submit` and `c submit` refused before any install, stamp or Kaggle call; help passing; `kaggle_submit.py`'s mark and the owner's override), with a stand-in CLI and Python in a prepared venv. |
| `tests/test_kaggle_sdk.py` | `kaggle_sdk.py` offline, with fake API and client objects (the Kaggle packages are never imported): each read's fields, call caps and failures, the arguments, sign-in. |
| `tests/test_kaggle_lb.py` | `kaggle_lb.py` offline, from fixture pages and fake `--sdk notebooks` reads. |
| `tests/test_kaggle_forum.py` | `kaggle_forum.py` offline, with a fake gateway and fake listings. |
| `tests/test_kaggle_share.py` | `kaggle_share.py` offline, with a fake account and project tree. |
| `tests/test_kaggle_live_plan.py` | `kaggle_live_plan.py` offline, with a stand-in gateway and `kaggle_share.py`: escaping, the best submission, the rev, a failed build that keeps the last page, the gateway reads. |
| `tests/test_kaggle_presubmit.py` | `kaggle_presubmit.py` offline, running `kaggle_forum.py` and `kaggle_lb.py` for real against a stand-in gateway: a first check, the ack, changes on every source with hosts first, an ack that records only what the check showed, failed reads keeping their earlier view, old-format and unfetched topics, the gate `kaggle_submit.py` applies, bad input. |
| `tests/test_kaggle_submit.py` | `kaggle_submit.py` offline: refusals (record, pre-submit check, kernel run, review line), the check-only run, one recorded submit carrying the gateway's mark, unclear results and interrupted attempts that block a blind retry, a file competition, one run at a time (a held record, two concurrent `--go` runs that submit once). |
| `tests/test_kaggle_hourly.py` | `kaggle_hourly.py` offline, with `kaggle_share.py` and the pre-submit reads running for real: a quiet pass, flags on the next pass (scores, errors, ended runs), failed reads that keep the last pass's view, notebooks, unread topics, host posts and page changes, live plan staleness. |
| `tests/test_kaggle_output.py` | `kaggle_output.py` offline, with a stand-in that pages an output of thousands of files from a page token: the crafted tokens, names not on their page, one-file outputs, failed calls, names that would leave `-p`. |
| `tests/kaggle_standin.py` | Shared fixtures of the pre-submit, submit and hourly tests: a project with the tools copied in (a copied install) and a stand-in gateway answering from a table (after an optional delay) and logging each call with the submit mark. |
| `tests/acceptance.md` | The live acceptance runbook: before each release of the plugin, a fresh agent follows it once against live Kaggle, read-only (nothing written to Kaggle; everything made locally stays in one scratch folder, removed at the end), and ends with a short report. |
| `migrations/` | Steps for copied installs when the plugin version advances (`0.2.0.md`: the moved gateway and the renamed skill). |

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
`tools/kaggle_hourly.py`, `tools/kaggle_output.py`, and `tools/kaggle_live_plan.py`, which runs
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

## Changelog

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
