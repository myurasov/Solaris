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
counters for account-level limits. Beside it, a GPU host health check (which also applies the safe
performance settings where the sharing rules allow) and a live dashboard of each host's jobs and load read the
same inventory. No daemon, no central server, no root.

## Files

| File | Role |
|---|---|
| `shared/tools/hostclaims.py` | The tool, stdlib only, Python 3.8 or newer. Runs on the controller and sends itself to each host over ssh (`python3 -`, script on stdin). Commands: `install` (`--all` for every owned host), `uninstall`, `status`, `shared`, `claim`, `run`, `renew`, `release`, `reap`, `yield`, `usage`, `pool`, `lease`, `extend`, `request`, `approve`, `decline`, `fit`, `audit`. |
| `shared/tools/hosthealth.py` | GPU host health over the same inventory, over ssh: GPUs visible against the inventory's `gpus`, Xid codes since boot, GPU start failures, throttling, uncorrected ECC errors, persistence, MIG and compute modes, power limits, the CPU governor. `--fix` applies the safe settings (persistence mode, GPU power limit back to its default, `performance` governor) on hosts with an owner record naming the project, or on the GPUs and cores of its live claims that no other claim shares. `--power max` (or the policy's `health.power`) holds power limits to the most each GPU allows on hosts the project owns, where `--fix` sets it; on other hosts the check and the fix keep the default. Exit 3 on problems, 4 on an unreachable host. |
| `shared/tools/hostdash.py` | Live full-screen view of each host's load, memory, disk, GPU use, claims, tmux sessions and busiest processes, over one reused ssh connection per host; `--once` prints a snapshot. |
| `shared/resource-sharing.skill.md` | Setup, commands, launching jobs, priorities and yield, owners and guests, picking up shared hosts, paid hosts and fit, the owner audit, host health and the live view, pools, conventions, troubleshooting. |
| `shared/resource-sharing.rule.md` | Always-on: launch through claims and stay within them, never touch another project's claim, one owner per host (guests keep what they write on a host inside their own folder there, and fix nothing but what `hosthealth.py --fix` sets on their own claims), pick up sharing changes (guests run `shared`, owners `install --all`), never delete or stop a shared host without the audit's advice, no self-extension of paid hosts, honour yields, done markers. |
| `tests/test_hostclaims.py` | Unit tests (stdlib `unittest`), run against temp folders with simulated host readings; not copied into projects. |
| `tests/test_hostclaims_tracking.py` | Unit tests for cores held by running jobs (overlap reproduction), memory as PSS, attached processes, `renew`, paired claims and their yield order, `--yield-signal none`, each GPU's last claim end and older claim files; fake process tables and memory readers, never the machine's own processes in an assertion. |
| `tests/test_hosttools.py` | Offline tests for `hosthealth.py` and `hostdash.py`: canned host output, and the real scripts run here through a fake ssh with stand-in `nvidia-smi`, `dmesg` and `systemctl` and a fake CPU folder (no sudo, no real setting touched). |

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
  and memory, RAM, disk, the PIDs and start times of the job and of any process attached later (so a reused PID
  is never mistaken for the job), the host's boot id and a heartbeat. Capacity is the host's totals minus live
  claims minus unclaimed use (GPU processes and memory, RAM, CPU load); a claim's memory in use is the PSS of
  its processes (summed RSS where PSS cannot be read), and cores a running process is pinned to outside its own
  claim are never granted, except to a new claim of that process's own job (a re-claim after a lapse, or a
  claim tied to it with `--pid`). `renew` changes a live claim (end, preemptible, borrowed, processes, yield
  order) without a release; dropping preemptible or borrowed passes the same admission as a claim.
- **Liveness**: a claim is stale when the boot id changed (the host rebooted), or its heartbeat is over 15
  minutes old and no process of it lives; stale claims move to `stale/`, never deleted. A job whose watcher
  died is an orphan and keeps its claim.
- **Paired claims**: claims that only work together (a server and the queue that calls it) name each other's
  order with `--yield-with`; a yield on either goes to both, the named partner first, and the next one's
  request waits until that partner has ended or had its grace.
- **Sharing and ownership**: a project's hosts are usable by the projects it lists in `share_with`, found
  by scanning the Solaris tree; the host mirrors the owner and the list and refuses others (no list: owner
  only). Owner actions run as the calling project, never as an `--agent` naming another. Only the owner
  changes a host; guests file extension, maintenance and objection requests (and withdraw their own) that
  the owner approves or declines. Owners share new machines at once with `install --all` (the audit flags
  hosts not installed or out of sync); guests see new, gone and changed shared hosts with `shared`, against
  a seen list in `<pack>/.memory/resource-sharing-seen.json` (no ssh unless `--probe`).

## Tests

`python3 -m unittest discover -s plugins/resource-sharing/tests -v` (about 50 seconds). Covered: parallel
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
network homes, pool definers), and Python 3.8 grammar. For the health check and the dashboard: parsing of
healthy, failing, unified-memory and driverless hosts and of older drivers, the fix scope by ownership and live
claims (owner, guest, a project with no claim, the host's record over the inventory's), fixes that touch only
what the scope allows (a shared frequency policy left alone, a second run changing nothing), read-only and
unreachable sweeps through a fake ssh, and the dashboard's rendering, connection reuse and snapshot.
Also covered: an overlap reproduction (a lapsed claim's running job, a job pinned outside its claim), a re-claim taking back
the cores its running job is pinned to (with and without `--pid`), the guard against granting a held core (a
faulty plan), memory as
PSS with forked workers and the RSS fallback, attaching processes (repeat `claim --pid`, `renew --pid`, run
claims), `renew` (end, flags, paid lease, `--all`, a lapsed claim, admission on a dedicated host), paired yields (order, a partner past its
grace, loops refused, yield candidates), `--yield-signal none`, each GPU's last claim end, claim files from the
previous version, `hosthealth.py --power max` (owner and guest scopes, judged only on owned hosts, the policy key), and the dashboard with
attached processes.

## Limits

- GPU shares cap how many jobs share a GPU; the GPU still gives its processes equal turns. MIG or MPS
  (hardware GPU partitioning) are not used.
- RAM and disk are reservations, not limits the kernel enforces (no cgroups): a job that outgrows its claim
  is visible in `status` but not stopped. CPU pinning (`taskset`) and `CUDA_VISIBLE_DEVICES` are enforced.
- A process counts toward a claim when it descends from one of the claim's processes, or carries
  `HOSTCLAIMS_CLAIM_ID` in an environment the tool can read (another login's processes, such as a root
  container's, cannot be read). Container processes descend from the container runtime, so start containers
  yourself and attach the container's main process (`claim --pid` or `renew --pid`); otherwise their GPU use
  counts as work outside claims. PSS and pinned cores are read on Linux only (`/proc/<pid>/smaps_rollup`, kernel
  4.14 or newer, and CPU affinity); elsewhere memory is summed RSS and pins are not seen.
- A `claim` without `--pid` lapses after 15 minutes unless renewed (`renew`, or the same `claim` again); `run`
  claims are kept alive by their watcher. A watcher started before 0.3.0 keeps its old code until its job ends.
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
- A reboot resets what `hosthealth.py --fix` sets (persistence mode survives only where the persistence
  daemon runs), so it is run periodically. Xid codes count from the last boot. Only the owner's `--fix` uses
  sudo (passwordless `sudo` for a login other than root); read-only runs and guests never do, so a host that
  restricts the kernel log shows it as not readable. It repairs nothing disruptive (driver, reboot,
  GPU reset, modes): those stay with the owner.
- `hostdash.py` shows the claim files as they are; only `status` tells live claims from stale ones.
