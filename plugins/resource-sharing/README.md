# resource-sharing - Solaris Plugin <!-- omit in toc -->

- [What It Is](#what-it-is)
- [Files](#files)
- [Install](#install)
- [How It Works](#how-it-works)
- [Tests](#tests)
- [Limits](#limits)

## What It Is

Lets several agents and projects share the same machines without stepping on each other. Each host keeps a
claim file per running job under `~/.solaris/claims/`; a stdlib Python tool on the controller (the machine
an agent works from) claims capacity under a file lock, launches jobs pinned to what they claimed, keeps a
heartbeat, asks lower-priority jobs to yield, and moves crashed jobs' claims aside. On top of it: sharing
links between projects in one Solaris tree, exactly one owner per host with guest requests, an owner audit
against abandoned (and billing) machines, a fit ranking against launching a new paid instance, and shared
counters for account-level limits. No daemon, no central server, no root.

## Files

| File | Role |
|---|---|
| `shared/tools/hostclaims.py` | The tool, stdlib only, Python 3.8 or newer. Runs on the controller and sends itself to each host over ssh (`python3 -`, script on stdin). Commands: `install` (`--all` for every owned host), `uninstall`, `status`, `shared`, `claim`, `run`, `release`, `reap`, `yield`, `usage`, `pool`, `lease`, `extend`, `request`, `approve`, `decline`, `fit`, `audit`. |
| `shared/resource-sharing.skill.md` | Setup, commands, launching jobs, priorities and yield, owners and guests, picking up shared hosts, paid hosts and fit, the owner audit, pools, conventions, troubleshooting. |
| `shared/resource-sharing.rule.md` | Always-on: launch through claims and stay within them, never touch another project's claim, one owner per host (guests keep what they write on a host inside their own folder there), pick up sharing changes (guests run `shared`, owners `install --all`), no self-extension of paid hosts, honour yields, done markers. |
| `tests/test_hostclaims.py` | Unit tests (stdlib `unittest`), run against temp folders with simulated host readings; not copied into projects. |

`manifest.json` and `revisions.json` (rev ledger, managed by `solaris.tools.revs`) complete the plugin.

## Install

"install plugin resource-sharing to `<project>`" copies `shared/` into `<pack>/plugins/resource-sharing/`, where
`<pack>` is the project's ai-pack folder (default `aipack/`, `ai/` in older projects, any name). Then, in the
project:

1. List hosts in `<pack>/.memory/hosts.json` as `{name, target, opts}`, with optional `owner` and `lease`.
2. Optionally create `<pack>/.memory/resource-sharing.json`:
   `{"project": "<slug>", "share_with": ["<other-slug>"], "policy": {}}`.
3. Install every host the project owns: `python3 <pack>/plugins/resource-sharing/tools/hostclaims.py install --all`
   (again right after adding machines or changing `share_with`), and record the footprint (`~/.solaris/claims/`
   on each host) in `<pack>/.memory/resources.md`.
4. Guests run `hostclaims.py shared` at least hourly: it lists new, gone and changed hosts shared with the project
   (exit 6 until `shared --ack`).

## How It Works

- **Host footprint** `~/.solaris/claims/`: `host.json` (owner, sharing list, mode, reserve, host rules,
  lease), `status.json` (tag `free`, `partly-busy` or `busy`), `history.jsonl` (append-only ledger),
  `claims/`, `stale/`, `run/<claim-id>/` (job script, logs, done marker), `requests/`, `pools/`, and `.lock`
  (`flock`). Claim files and run folders are private to the host's login; the owner's `reap` prunes
  finished run folders after 14 days. `uninstall` removes the folder and is refused while claims or pool
  holds are open. The folder is bound to its machine (a home shared between hosts is refused; set a
  per-host `root` on local disk instead).
- **Claims** name the project, job, class (P0-P3), preemptibility, exact CPU cores, GPU indexes with a share
  and memory, RAM, disk, the job's PID and start time (so a reused PID is never mistaken for the job), the
  host's boot id and a heartbeat. Capacity is the host's totals minus live claims minus unclaimed use (GPU
  processes and memory, RAM, CPU load).
- **Liveness**: a claim is stale when the boot id changed (the host rebooted), or its heartbeat is over 15
  minutes old and no process of it lives; stale claims move to `stale/`, never deleted. A job whose watcher
  died is an orphan and keeps its claim.
- **Sharing and ownership**: a project's hosts are usable by the projects it lists in `share_with`, found
  by scanning the Solaris tree; the host mirrors the owner and the list and refuses others (no list: owner
  only). Owner actions run as the calling project, never as an `--agent` naming another. Only the owner
  changes a host; guests file extension, maintenance and objection requests (and withdraw their own) that
  the owner approves or declines. Owners share new machines at once with `install --all` (the audit flags
  hosts not installed or out of sync); guests see new, gone and changed shared hosts with `shared`, against
  a seen list in `<pack>/.memory/resource-sharing-seen.json` (no ssh unless `--probe`).

## Tests

`python3 -m unittest discover -s plugins/resource-sharing/tests -v` (about 25 seconds). Covered: parallel
claims never oversubscribing (24 processes at once), idempotency, capacity math with GPU shares and unclaimed
use, stale reaping (dead PID, changed boot id), orphan detection, yield request, acknowledgement and kill
after the grace, the watcher's environment, pinning and done marker, status tags, the usage ledger, pools
(caps, borrowing, budgets, expiry), uninstall refusals, lease kinds, the request flow (request, approve,
decline, timeout, then launch-new), fit ranking with a fake inventory, discovery in a fake Solaris tree (pack
folders `ai`, `aipack` and a custom name, one-way and mutual sharing), finding the project and its pack (from
the working folder, `--project` or a copied install; two packs are an error; the walk stops before the home
folder), shared-host changes (new, gone, changed, unreadable owner files, the seen list, `--probe` admission),
`install --all` and the audit's sync flags, ownership rules, the owner audit, the ssh path through a fake ssh program, hardening cases from a code review (a released claim is
never revived, yield admission, pending yields, stop signals, an unwritable run folder, machine binding and
network homes, pool definers), and Python 3.8 grammar.

## Limits

- GPU shares cap how many jobs share a GPU; the GPU still gives its processes equal turns. MIG or MPS
  (hardware GPU partitioning) are not used.
- RAM and disk are reservations, not limits the kernel enforces (no cgroups): a job that outgrows its claim
  is visible in `status` but not stopped. CPU pinning (`taskset`) and `CUDA_VISIBLE_DEVICES` are enforced.
- A process counts toward a claim when it descends from the claimed job, or carries `HOSTCLAIMS_CLAIM_ID` in
  an environment the tool can read (another login's processes, such as a root container's, cannot be read).
  Container processes descend from the container runtime, so start containers yourself and tie a claim to
  the container's main process (`claim --pid`); otherwise their GPU use counts as work outside claims.
- A `claim` without `--pid` lapses after 15 minutes unless renewed; `run` claims are kept alive by their
  watcher.
- An orphan's watcher is not restarted: its claim goes stale after the job ends, with no done marker.
- Fair share is reported by `usage` (and `--borrowed` marks borrowing); it does not block claims.
- Work outside claims is seen through GPU processes and memory, CPU load, and on Linux unclaimed processes of
  1 GiB or more; elsewhere large idle processes are not seen.
- Outside a project, owner actions trust `--agent` once `--as-owner` confirms it; inside one they run as the
  project.
- The tool records decisions and requests; renewing, rebooting, releasing or deleting machines stays with
  the plugins and people who own them. The rule guards other plugins' teardowns (a shared instance is
  deleted only on the owner audit's `delete` advice), since those plugins do not know about claims.
- `simulate.json` in a claims folder replaces the host's readings only when `HOSTCLAIMS_SIMULATE=1` is set
  (tests).
