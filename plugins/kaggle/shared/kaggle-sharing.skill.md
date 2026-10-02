---
name: kaggle-sharing
triggers: ["kaggle sessions", "share the kaggle account", "kaggle quota", "kaggle sharing", "kaggle gpu quota", "kaggle concurrent sessions"]
summary: Share one Kaggle account between the projects using it - detects the active projects (account-wide queued and running kernels, local gateway activity), splits the concurrent CPU and GPU sessions and the weekly GPU hours equally or as the user directs, and hands out a lease around each kernel run. Covers kaggle_share.py (status, acquire, release, ledger, config, stamp) and the agent routine.
---
_Rev. 6_

# Skill: kaggle-sharing - One Kaggle Account, Several Projects <!-- omit in toc -->

- [When to Use](#when-to-use)
- [What Is Shared](#what-is-shared)
- [Detection](#detection)
- [The Split and the User's Directions](#the-split-and-the-users-directions)
- [Commands](#commands)
- [Agent Routine](#agent-routine)
- [Limits of What It Sees](#limits-of-what-it-sees)

## When to Use

Before every kernel run (`kernels push`, or its alias `kernels update`), after it ends, in each upkeep
check, and whenever the user says how the account should be split. Several projects (or several
agents) on one Kaggle account compete for the same few sessions and the same weekly GPU hours; this
skill keeps them from starving or overbooking each other. It needs nothing outside this plugin:
`resource-sharing`, for sharing hosts between agents and projects, is a separate plugin, installed on
its own if wanted.

## What Is Shared

- **Concurrent sessions:** Kaggle caps how many kernel runs one account has at a time; the tool
  assumes 5 CPU and 2 GPU sessions (`config --limit` corrects them if Kaggle's numbers change). A
  queued run holds a session too.
- **Weekly GPU hours:** the account's accelerator quota (`quota` shows the live total, use and reset
  time; 30 hours a week at the time of writing).
- **Not shared:** daily submission slots are per competition, so each project keeps its own.

## Detection

- **Account-wide, from any machine:** one read-only SDK read through the gateway (`kaggle.py --sdk
  account`), in one process: the quota, the account's kernels (latest run first) and the run state
  of each kernel run in the last 12 hours (`scan_hours` in `sharing.json`).
  Finished runs are remembered and not asked again, so after the first check (two calls plus one per
  recent kernel) a check costs about three calls.
- **This machine:** the gateway stamps each call in `~/.solaris/kaggle/activity/` - one small JSON
  file per project or task folder with its path, name, first and last call time, call count and the
  last command words, never arguments. Stamps stay out of `~/.kaggle/`, which holds credentials.
  The plugin's own monitoring (this tool's checks, leaderboard snapshots) runs with
  `KAGGLE_SHARE_QUIET=1` and is not stamped, so checking never makes a project look active.
- **Active** means gateway calls in the last 24 hours (`config --active-hours`), a queued or running
  kernel, or a lease or waiting request.
- **Kernels map to projects** through the `id` in each `kernel-metadata.json` under the projects of
  the Solaris tree (whatever their ai-pack folder is named, embedded repos included) and under any folder a stamp
  names; hidden, `__*` and package folders are skipped. `enable_gpu`, `enable_tpu` and `machine_shape` give each kernel's
  kind. A running kernel that no local folder describes shows as "(no local folder)" with an unknown
  kind and counts against both pools until `status --probe` reads its metadata (a read-only
  `kernels pull -m` into a throwaway folder inside the project).

## The Split and the User's Directions

- **Default:** equal shares among the active projects. Each session pool is divided evenly and
  rounded up (two GPU sessions among three projects gives each a share of one, first come first
  served), and the week's GPU hours are divided evenly.
- **Within its share** a project may take one more session whenever the pool has room.
- **Borrowing:** beyond its share (of sessions or GPU hours) only while every other sharing project
  uses none of that kind and none is waiting; the lease is marked borrowed. Runs are never stopped:
  a project that wants its share back waits for the borrowed run to end.
- **Always enforced:** the pool limit, per-project caps and the account's remaining GPU hours.
- **The user's directions override detection.** They live in `~/.solaris/kaggle/sharing.json`, set
  with `config`:

| Direction | Command |
|---|---|
| Equal shares among active projects (the default) | `config --equal` |
| Only these projects use the account | `config --only <project> [--only <project>]` |
| Fixed weights (unlisted projects get no share but may borrow) | `config --weight <project>=3 --weight <other>=1` |
| A cap for one project | `config --cap <project>:gpu=1` (or `:cpu=N`, `:gpu_hours=N`); `--uncap <project>` |
| Keep sessions free for the owner's own notebooks | `config --reserve gpu=1` |
| Kaggle's limits changed | `config --limit cpu=5 --limit gpu=2 --limit gpu_hours=30` |
| Keep the direction in words | `config --note "<the user's words, with the date>"` |

A project's name is its folder name; where two folders share a name, each gets its parent folder
added (`alpha (my)`, `alpha (nv)`). `status` lists them. Leases and the ledger follow the project
folder itself, so two projects of one name never share a count.

## Commands

Run from the project root or task folder:

| Context | Command |
|---|---|
| Project, plugin copied | `python3 <pack>/plugins/kaggle/tools/kaggle_share.py <command> ...` |
| Project linked, or ad-hoc task | `python3 <solaris>/plugins/kaggle/shared/tools/kaggle_share.py <command> ...` |

`<pack>` is the project's ai-pack folder (default `aipack/`, `ai/` in older projects, any name).

| Command | Does |
|---|---|
| `status [--json] [--probe]` | Active projects and why, sessions in use against each share, GPU hours this week against each budget, queued and running kernels, leases and waiting requests. Closes leases whose run has finished or that expired. |
| `acquire --path <kernel dir>`, or `--kind cpu\|gpu [--kernel <owner/slug>]` | Takes a lease for the next run: prints its id (exit 0) or why it is refused (exit 3). `--path` reads the kernel and its kind from `kernel-metadata.json` (`--kind` overrides, e.g. for a push with `--accelerator`). `--wait <minutes>` waits for a free share, checking every `--poll` seconds (default 120) and marking the project as waiting; `--hours` gives the run's expected GPU hours. |
| `release [<lease id>] [--path <kernel dir> \| --kernel <ref>] [--all]` | Closes the lease into the ledger when the run has ended. |
| `ledger` | Lease hours by project, this Kaggle week and in total, beside the account's own GPU use. |
| `config ...` | Shows or sets the user's split (above). |
| `stamp` | Marks this project active by hand; the gateway does this on each call. |

`status` and `acquire` also take `--gateway <path>` (default: the `kaggle.py` beside the tool),
`--offline` (no Kaggle calls: use the last account read), `--solaris <checkout>`, and `--state
<folder>` (or `KAGGLE_SHARE_DIR`). A lease ends when it is released, when Kaggle shows its kernel's run
finished (if the lease names the kernel), or after 12 hours. The finished run must be a new one: not
the run Kaggle showed when the lease was taken, and started no more than 10 minutes before the lease,
since Kaggle's clock and this machine's can differ. When Kaggle could not be read at `acquire` (the
read failed, or `--offline` had only an older read), the lease records that (`prior_unknown`), and
only a run started after the lease closes it. A push that fails or is declined starts no run, so
only a release ends that lease early.

## Agent Routine

1. **Before each run:** `acquire --path <kernel dir>` (with `--wait 30` when waiting is fine). If it
   is refused, do not push: report the reason and retry later, or ask the owner to change the split.
2. **Push** the kernel as usual, after the owner's go-ahead (the kaggle rule). **If the push fails or
   the owner declines it,** release the lease at once: `release --path <kernel dir>`. The gateway
   checks the lease: a `kernels push` (or `kernels update`) that no open lease covers (one this
   project took for that kernel, or for no kernel) stops with exit 3 before any Kaggle call.
   `KAGGLE_SHARE_QUIET` does not bypass it; only the owner overrides it, with
   `KAGGLE_PUSH_WITHOUT_LEASE=1`.
3. **When the run ends** (complete, failed or cancelled): `release --path <kernel dir>`. A lease that
   names its kernel also closes itself at the next `status` once Kaggle shows the run finished.
4. **Upkeep** (hourly, or with each planning pass): `status` - is the project within its share, is a
   run stuck in the queue, will the week's GPU hours last until the reset?
5. **When the user directs a split,** apply it with `config` and keep their words with `--note`.

## Limits of What It Sees

- Interactive notebook sessions in the browser are invisible to the CLI: reserve a session for them
  (`config --reserve gpu=1`) while the owner works on the site.
- Leases are local to this machine. Runs started from another machine count once Kaggle shows them
  queued or running, so two machines launching at the same moment can still overbook, and Kaggle may
  then queue or refuse the extra run.
- Lease hours include queue time, so the ledger is an upper bound; the account's true GPU use comes
  from `quota`.
- The session limits are defaults the tool assumes; `config --limit` corrects them without a code
  change.
