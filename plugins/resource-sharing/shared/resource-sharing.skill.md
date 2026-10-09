---
name: resource-sharing
triggers: ["claim a host", "host claims", "hostclaims", "share hosts", "shared hosts", "which hosts are free", "launch a host job", "resource sharing", "share resources", "shared resources", "sharing links", "extension request", "extend the lease", "audit my hosts", "owner audit", "shared pool", "fit a job", "reuse an instance", "host health", "gpu health", "host dashboard", "hosts dashboard"]
summary: Share hosts between agents and projects with claim files kept on each host - hostclaims.py claims capacity under a lock (memory counted as PSS, cores a running job is pinned to never granted twice), ties claims to their jobs' processes, renews claims in place, launches jobs pinned in tmux with a heartbeat and done marker, asks lower-priority jobs to yield (paired claims in a set order), moves stale claims aside, reports usage and each GPU's last claim, runs shared pools for account-level limits, and handles owners and guests - sharing links between projects (guests see new, gone and changed shared hosts with `shared`; owners share new machines at once with `install --all`), one owner per host, extension and maintenance requests, paid-instance fit, and the owner's audit; hosthealth.py checks GPU hosts (and applies the safe performance settings where the sharing rules allow) and hostdash.py shows their jobs and load live.
---
_Rev. 9_

# Skill: resource-sharing - Hosts Shared by Many Agents <!-- omit in toc -->

- [What and Why](#what-and-why)
- [Setup](#setup)
- [Commands](#commands)
- [Launching Jobs](#launching-jobs)
- [Priorities and Yield](#priorities-and-yield)
- [Owners, Guests and Requests](#owners-guests-and-requests)
- [Picking Up Shared Hosts](#picking-up-shared-hosts)
- [Paid Hosts and Fit](#paid-hosts-and-fit)
- [Owner Audit](#owner-audit)
- [Host Health and Live View](#host-health-and-live-view)
- [Pools](#pools)
- [Conventions for Agents](#conventions-for-agents)
- [Troubleshooting](#troubleshooting)

## What and Why

Several agents (each project's primary persona and its workers) often use the same machines. Without
coordination their jobs stack on one GPU and slow each other (GPU time-slicing: the GPU takes turns between
processes), numeric libraries start a thread per core and fight over CPUs, and nobody knows what is safe to
stop. `tools/hostclaims.py` keeps one claim file per job on each host, under `~/.solaris/claims/`: who runs
what, on which GPUs (or GPU share), CPU cores, RAM and disk, at which priority. Checking free capacity and
writing a claim happen under one file lock (`flock`, a lock the kernel frees if its holder dies), so two
agents never both take the last slot. The claims folder is the truth; `status.json` beside it tags the host
`free`, `partly-busy` or `busy` for anyone who looks.

The tool runs on the controller (the machine the agent works from) and sends itself over ssh on each call
(`python3 -`, the script on stdin); hosts need only Python 3.8 or newer, `tmux` and ideally `taskset`. There
is no daemon and no central server: each host keeps working when cut off, and every failure leaves a file to
read. Here a **lease** means a host's rental term (its kind, planned end and price), not a hold on a single
run or session.

## Setup

1. **Inventory.** The project's `<pack>/.memory/hosts.json` (`<pack>` is the project's ai-pack folder: default
   `aipack/`, `ai/` in older projects, any name) lists hosts as `{name, target, opts}` (`target` is
   `user@address`, `opts` the ssh options). Optional per host: `owner`
   (a project slug; default: this project), `lease` (below), `root` (the claims folder, default
   `~/.solaris/claims`) and `gpus` (free text such as `2x H200 NVL`, which `shared` shows guests).
   `--hosts FILE` uses exactly that file instead; `--local-root DIR` works on a folder on this machine
   (tests, dry runs).
2. **Sharing links.** `<pack>/.memory/resource-sharing.json` (private) names this project and whom it
   shares its own hosts with:

   ```json
   {"project": "<this-slug>", "share_with": ["<other-slug>"], "policy": {}}
   ```

   Every call scans the Solaris tree (`projects/*/<slug>/`, then `projects/*/*/<slug>/`, each project with
   its one ai-pack folder, whatever its name) and merges in the hosts each other project owns and shares with
   this one: a host of P is usable by Q when P lists Q (`"*"` shares with every project). Sharing is one way
   unless both list each other. Every host line shows its source project and owner; a name clash becomes
   `<project>/<name>`.
   Without the file the slug is the project folder's name and the project's hosts stay private.
3. **Agent name.** `--agent`, else `HOSTCLAIMS_AGENT`, else the project slug. Workers claim under their
   project's name, so claims, usage and ownership are per project. Owner actions (install, uninstall,
   lease changes, approve, decline, pool definitions) always act as the calling project: an `--agent`
   naming another project is refused for them. Outside a project (`--hosts` or `--local-root`) nothing
   proves who calls, so they also need `--as-owner`, the caller's explicit confirmation.
4. **Install per host (owner only).** `python3 <tool> install --host <name>` creates the claims folder,
   records the owner and writes the sharing list into the host's `host.json` (from `resource-sharing.json`,
   or `--share-with a,b`, `'*'` or `none`). The host itself then admits only the owner and the projects on
   that list; with no list it admits only the owner. Options: `--mode shared|draining|dedicated:<project>`,
   `--reserve-cores N`, `--reserve-ram SIZE`, `--disk-free-pct P`, `--lease key=value`, `--rule key=value`
   (host rules below), `--owner <slug>` (hand the host over). `install --all` does every host the project
   owns in its inventory at once (idempotent; it takes no `--owner` or `--lease`, which differ per host): run
   it right after adding machines to `hosts.json` or changing `share_with`, then tell the projects you share
   with (see Picking Up Shared Hosts). `uninstall` (owner only) removes the folder and is refused while
   claims or pool holds are open. The folder is bound to its machine, and install refuses a home folder on a
   network filesystem (a home shared between hosts would mix their claims): give such hosts a `root` on
   local disk in the inventory. Record the install (host and path) in the project's `resources.md`, as for
   any remote footprint.
5. **Policy (optional)**, in the `policy` block of the sharing file or `--policy FILE`: `weights` per
   project (for `usage`), `classes` (default GPU share and preemptibility per class), `fit` (new-instance
   wait, prices per GPU type, extension thresholds), `audit` (idle limits) and `health` (`{"power": "max"}`
   makes `hosthealth.py` hold GPU power limits to their maximum; see Host Health and Live View). Host rules
   live in the host's `host.json` so every agent there applies the same numbers: `stale_min` 15,
   `yield_min_age_min` 20, `yield_grace_min` 10, `heartbeat_s` 60, `request_timeout_min` 120, `run_keep_days` 14.

`<tool>` below is `<pack>/plugins/resource-sharing/tools/hostclaims.py` in a project (copy mode), or
`plugins/resource-sharing/shared/tools/hostclaims.py` in the Solaris tree. Run it from the project root.

## Commands

| Command | Does |
|---|---|
| `status [--host H]` | Per host: tag, owner, lease, free cores/RAM/disk and per-GPU free share and memory with its last claim's end (`last_claim_end` in `--json`), claims (live, orphan, stale) with the memory they use and their processes, unclaimed GPU processes, cores two claims share and processes pinned outside their claim, requests, recent ends, stale files. All hosts by default. Other projects' commands show only their program name. |
| `shared [--probe] [--ack] [--seen FILE]` | Guest: the hosts other projects share with this one against the seen list: `NEW`, `GONE` and `CHANGED` hosts; exits 6 until `--ack` records the current set. No ssh unless `--probe`. `--seen FILE` names the seen list (required with `--hosts`). See Picking Up Shared Hosts. |
| `claim --host H --job J --cores N --ram SIZE [--gpu SPEC] [--class P1] [--hours H] [--pid P] [--yield-with JOB]` | Reserve capacity for a job you start yourself. Idempotent per project and job: a repeat returns the same claim and renews it, and a repeat with `--pid P` attaches that process to it (repeat `--pid` for more; a `run` claim takes them too); the answer names each attached program. Untied, the claim lapses 15 minutes after its last renewal; the answer prints that time. A process another claim holds is refused. A new claim takes first the cores its own job already runs pinned to: those of a process given with `--pid`, and, for a job whose claim lapsed in the last hour, those its still-running job is pinned to, then the lapsed claim's cores and GPUs while they are free (no other project gets those while the job runs). The answer says when the job runs outside the new claim (move it with `taskset -acp`). |
| `run ... [--yield-signal SIG] [--grace-min M] [--yield-with JOB] -- CMD ARGS` | Claim, then start the job in tmux (see Launching Jobs). A repeat returns the running claim; a job that ended in the last 15 min needs `--rerun`. Over an earlier bare claim of the same job it re-fits that claim to the run's size. |
| `renew --host H --job J [--hours H \| --until T] [--preemptible \| --no-preemptible] [--borrowed \| --no-borrowed] [--pid P] [--yield-with JOB]` | Change your live claim without releasing it: a new end (`--hours` from now, or `--until`; past a paid lease's planned end it is refused, as for `claim`), preemptible or borrowed (P0 is never preemptible, P3 always), more processes, its yield order (`--yield-with none` clears it). It also renews a bare claim. Dropping `--preemptible` or `--borrowed` passes the same admission as a claim: on a host dedicated to another project a guest's claim keeps both, so the owner can still ask it to yield. `--all` instead of `--job` changes every claim of yours on the host (not with `--pid` or `--yield-with`). The ledger records each change. |
| `release --host H --job J` | Release your claim; `--stop` first stops a running job (through its watcher: TERM, then KILL after the grace; a claim tied with `--pid` gets TERM to the group of each tied process and is released once they are gone). |
| `reap [--host H]` | Move stale claims to `stale/` (never deleted); orphans stay. Run by the owner, it also prunes finished run folders older than `run_keep_days` (14), with their logs and done markers. `claim` and `run` reap first anyway. |
| `yield --host H --claim ID --class P0 --job J --reason ...` | Ask a preemptible lower-priority claim to checkpoint and stop; claims paired with it yield too, in their order. |
| `usage [--window 24h]` | GPU-hours (share x hours, by GPU type) and CPU-hours per project, with the policy weights. |
| `fit --hours H [--gpus N --gpu-type T --gpu-mem SIZE] --cores N --ram SIZE [--can-wait]` | Rank hosts for a job against a new paid instance, and recommend. |
| `extend`, `request`, `approve`, `decline` | Guest requests (and `request --withdraw ID`) and owner decisions (below). |
| `lease --host H [--set key=value]` | Show the lease; the owner sets it. |
| `audit` | Owner: lease, idle time, use by project, cost so far, open requests, hosts not installed or whose sharing list differs from `resource-sharing.json` (each with its `install` command), recommendation. |
| `pool show|define|acquire|release --file PATH|HOST:PATH` | Shared counters with per-project caps. |

GPU `SPEC` is `INDEX|any[:SHARE[:MEM]]`, e.g. `--gpu 1`, `--gpu any:0.25`, `--gpu 0::12G`; `--gpus N` picks N.
Sizes take `M`, `G` or `T`; durations `90m`, `2h` or `1d`. `--json` prints machine-readable output. Exit
codes: 0 ok, 1 error, 2 bad usage, 3 does not fit (try another host, or ask to yield), 4 host unreachable
(the claim or launch may still have happened: run `status` on that host first), 5 refused by a sharing rule,
6 the hosts shared with this project changed since the last `shared --ack` (act on them, then ack).

Capacity is the host's totals minus live claims minus what runs outside claims: an unclaimed GPU process (or
at least 1 GiB of unexplained GPU memory) takes its whole GPU, unclaimed RAM use and CPU load count as used,
and the host keeps a reserve (1 core, 4 GiB RAM, 10% disk free by default). A claim's RAM counts as the larger
of what it declared and what its processes use, measured as PSS (proportional set size: a page that several
processes share counts once across them, so forked workers sharing their parent's memory are not counted once
each), or as summed RSS where PSS cannot be read (another login's processes, kernels older than 4.14); `status`
shows each claim's use and how it was measured. Cores a running process is pinned to (`taskset` or the like, on
fewer than half of the host's cores, 64 MiB or more) are never granted when they lie outside the process's own
claim, or, for a process tied to no claim, outside every claim: a job whose claim lapsed keeps its cores until it
ends (only a new claim of that same job takes them back), and `status` names such processes. GPU memory is claimed too
(default: the share times the GPU's memory, or what is free when that is less, down to half of it); GPUs
with unified memory report none, so only their shares are checked, and `--gpu-mem` matches them by share.
A GPU share only caps how many jobs pile on one GPU; it is not a speed guarantee. Work outside claims (for
the tag and the audit) means a GPU in use, a core or more of load, or unclaimed processes of 1 GiB or more
each; the system's own memory, caches and `/dev/shm` are not work.

## Launching Jobs

`run` writes `run/<claim-id>/job.sh` (the command) and starts a small watcher in tmux session
`hc-<project>--<job>` (a `setsid` process when tmux is missing). The watcher:

- starts the job pinned to the claimed cores with `taskset`, with `CUDA_VISIBLE_DEVICES` set to the claimed
  GPUs (empty for CPU-only claims, so they see no GPU), `CUDA_DEVICE_ORDER=PCI_BUS_ID`, `OMP_NUM_THREADS` and
  its siblings set to the core count, `HOSTCLAIMS_CLAIM_ID`, `HOSTCLAIMS_YIELD_FILE` and `HOSTCLAIMS_RUN_DIR`;
- refreshes the claim's heartbeat every minute while the job's process lives;
- on a yield request sends the job's process group `TERM` (`--yield-signal` to change it), and on a stop always
  `TERM`; then `KILL` after the grace period (`--grace-min`, default 10). `--yield-signal none` is for a job
  that must not be signalled when a yield comes, one that stops itself in a set order (a server behind a guard,
  say): it gets no signal, reads `$HOSTCLAIMS_YIELD_FILE` and ends itself within the grace. The `KILL` at the end
  of the grace still comes, so a yield stays binding: give such a job a `--grace-min` long enough for its own
  stop, and keep anything that must outlive it out of its process group. A stop by the claim's own project
  (`release --stop`) after a yield sends `TERM` at once;
- on exit writes `run/<claim-id>/done.json` (exit code, signal, reason `finished`, `failed`, `yielded` or
  `stopped`, times; `yielded` also when the job ended itself after reading its yield file), a copy at
  `--done-file PATH` if given, a ledger line in `history.jsonl`, and releases the claim.

The thread variables suit one multi-threaded process. A job that starts many single-threaded worker processes (a
process pool, parallel runners) hands the count to each, so N workers on an N-core claim start N threads apiece and
oversubscribe it: set one thread per process (the variables at 1 in each worker, or the runner's own thread option).
A job started outside `run` sets the same caps itself (`OMP_NUM_THREADS` and its siblings at its claimed core
count): uncapped jobs on a shared host starve each other, and one stalled job ran several times faster once capped.

Output goes to `run/<claim-id>/job.log` (follow it with `tail -f`); watcher events go to `wrapper.log`. Claim
files and run folders are readable by the host's login only. The owner's `reap` removes finished run folders
after 14 days, `$HOSTCLAIMS_RUN_DIR` included: write results and checkpoints elsewhere. Pass `--cwd DIR` for the working folder;
activate environments inside the command. Poll `done.json`, never `pgrep -f` over ssh (the pattern matches
the ssh command line itself), and in any hand-written launch line put `;` rather than `&&` before a
background step.

A process counts toward a claim when it descends from one of the claim's processes (or carries
`HOSTCLAIMS_CLAIM_ID` where the tool can read its environment, which a different login cannot). Container
processes descend from the container runtime, not from `docker run`, so do not wrap containers in `run`:
claim the capacity first, start the container, then repeat the same `claim` with
`--pid $(docker inspect -f '{{.State.Pid}}' <container>)` to tie it to the container's main process within
15 minutes (or `renew --job J --pid ...`). Otherwise its GPU use counts as work outside claims. The same
attaches other processes a job starts outside its tree, to a `run` claim too: their memory then counts toward
the claim, and a claim without a watcher lives while any of its processes runs (tie the job's own long-lived
process, not a launcher that exits at once: the answer names each attached program, and `status` marks one
that has ended `(gone)`). A claim whose processes all ended goes stale 15 minutes after its last renewal; its
ledger end is the last time a call on the host saw one of them alive.

## Priorities and Yield

| Class | For | Default GPU share | Can be asked to stop early |
|---|---|---|---|
| P0 | Deadline work: submission checks, timing runs due within a day | 1.0 | Never |
| P1 | Experiments whose result feeds the next decision (default class) | 0.5 | Only with `--preemptible` (use it when the job checkpoints) |
| P2 | Exploration and backlog | 0.25 | Yes (`--no-preemptible` to opt out) |
| P3 | Borrowed idle capacity: extra seeds, sweeps, caches | 0.25 | Always |

A job may ask only lower, preemptible classes, or a same-class claim marked `--borrowed` (running beyond its
project's fair share, see `usage`) when it is not borrowed itself. On a host dedicated to a project, that
project may also ask other projects' borrowed claims, and no other project may ever ask its jobs. Jobs
younger than 20 minutes are never asked, and only a project admitted to the host may ask. When `claim` or
`run` does not fit, its answer lists the smallest set of claims that may be asked (or says to wait when
yields already requested free enough); ask only after no other host fits (`status`, `fit`). The target's
own watcher delivers the request; the job saves a checkpoint and exits (trap `SIGTERM`, or poll
`$HOSTCLAIMS_YIELD_FILE`); its done marker says `yielded` and its project queues it again. A claim without
a watcher sees the request in `status`.

**Paired claims.** Claims that only work together, such as a server and the queue that sends it work, yield
together and in a set order. Start the one that must stop last with `--yield-with <the other's job>` (on
`claim`, `run` or `renew`; repeat it for more partners): a yield request on either claim then goes to both,
the named partner first, and the claim that names it gets its request only once that partner's claim has
ended, or its grace and half a minute have passed (a call on the host or the claim's own watcher delivers it,
and `status` shows it as waiting until then). So the queue stops before the server it calls. The order may not
loop. The partners must be your own claims on the same host; a pairing makes them yield together whatever
their own preemptibility, so pair only claims that are useless apart. When `claim` or `run` lists the claims
to ask, asking one of a pair frees the other too.

## Owners, Guests and Requests

Every host has exactly one owner: the project that leased or created it, named in its inventory and in the
host's `host.json`. Only the owner installs, changes settings or the lease, renews or extends it, does
maintenance (reboots, drivers, disk cleanup), and releases or deletes the machine; the tool enforces this for
everything it manages. Guests (projects the owner shares with) claim capacity like anyone and ask the owner
through requests:

- `extend --host H --job J --hours H <resources>`: more time past the planned end, with the fit evidence,
  the extra hours and cost, and the comparison with a new instance. It expires after two hours unanswered
  (`request_timeout_min`, at least the owner's audit interval).
- `request --host H --type maintenance --reason "..."`: a reboot, a driver fix, disk cleanup.
- `request --host H --type objection --until +12h --reason "..."`: do not release or delete before then.

Each command prints the request's text with the `approve` and `decline` commands to run from the owner's
project. The requesting agent shows that text to the owning project: if the owner is a human, in chat, and
nothing is approved without their yes. `status` lists pending requests on each host, and the owner's
`audit` shows every open one. The owner decides with `approve --host H --request ID` (an extension moves the
planned end; an objection records keep-until) or `decline --host H --request ID --reason "..."`, and moves
its own teardown plan with any new end. A declined or expired extension means the requester launches
elsewhere. A guest that launches elsewhere while its request is pending withdraws it with
`request --host H --withdraw ID` (only the requesting project may; the ledger records it), so the owner is
not left deciding a request nobody needs.

A guest onboarding that worked: the owner installs its hosts with the sharing list (`install --all`) and messages
the guest the hosts, their lease ends and its terms; the guest files each setup need (a container runtime, a GPU
reset) as `request --type maintenance`; the owner, with its human's yes (standing or per request), does the work,
checks it, and only then approves the request, so an approval also tells the guest the work is done.

A guest's bring-up on a shared host takes what is free when it runs: write bring-up steps that accept a subset of
the GPUs (or parallel workers) they plan for and add the rest as those free up, since the owner may claim one in
the middle of a bring-up. A guest claim that runs beyond the guest's fair share is marked `--borrowed` (`renew
--borrowed` marks a live one): the owner's default-class (P1) jobs may ask only lower classes, or same-class
borrowed claims, to yield.

A guest leaves a host as it found it, apart from its own folder there (for example `~/.solaris/<project>/`,
listed in its `resources.md`). Before its first job on the host it points every cache and config home into
that folder, from an env file each job sources: `XDG_CACHE_HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`,
`CUDA_CACHE_PATH`, `TRITON_CACHE_DIR`, `TORCHINDUCTOR_CACHE_DIR`, `HF_HOME`, `UV_CACHE_DIR`, `PIP_CACHE_DIR`
and each framework's own (`VLLM_CACHE_ROOT`, for example). The defaults land in the login's home, often the
owner's own: a GPU stack meeting a new GPU architecture JIT-compiles hundreds of megabytes into the CUDA cache
on its first start, and installers write to `~/.config` and `~/.local/share`. Check once after the first job:
`touch` a marker before it, then `find "$HOME" / -xdev -newer <marker>` should list nothing outside the folder
but the claims folder and temporary files (`$HOME` is named because `-xdev` keeps `find /` out of a home on
another filesystem). Problems found later (leftover GPU memory, a missing package, a stray
process that is not yours) go to the owner the same way, as a maintenance `request` plus a message; a guest
never fixes the owner's host itself (the one exception: `hosthealth.py --fix`, which touches only the cores of
the guest's own live claims and the GPUs nothing else uses, never with `sudo`; see Host Health and Live View).

## Picking Up Shared Hosts

Owners add and retire machines; their guests must notice without being told. `shared` reads only the Solaris
tree (no ssh): the hosts other projects own and share with this one, named as `status` names them, compared with
the seen list `<pack>/.memory/resource-sharing-seen.json` (private, per project). It prints `NEW`, `GONE` and
`CHANGED` hosts with owner, target, lease kind and planned end as the owner's inventory lists them (`?` when it
lists no lease; the host's own copy wins) and GPUs when the inventory names them (`gpus`, else the lease's
`gpu_type`), and exits 6 until `shared --ack` records the current set. A project whose files cannot be read
keeps its hosts as seen (`UNREAD`), never `GONE`. `--probe` adds each host's live tag, free capacity and GPUs,
and whether it admits you (a host its owner has not synced yet does not).

- **Guests** run `shared` at every audit and at least hourly, beside their other scheduled checks (an automated
  check prints and never acks). On `NEW`: `status --host H` or `fit`, then bring the host into use through
  `claim` or `run`; one that is not installed or does not admit you yet waits for its owner (ask them). On
  `GONE`: start nothing new there, and release your claims there (let running jobs finish, or stop them). On
  `CHANGED`: check lease ends and targets against your claims and scripts. Then `shared --ack`.
- **Owners** run `install --all` right after adding machines to `hosts.json` or changing `share_with`: it
  installs the new hosts and syncs the sharing list on the others. Then they tell the projects they share with
  (whose next `shared` finds the change anyway). `audit` flags owned hosts that are not installed, or whose
  sharing list differs from `resource-sharing.json`, each with the exact command to fix it.
- **Lease ends** (guests): owners extend leases far more often than they shorten them, and keep adding hosts. Read
  each host's planned end at every audit (`status` lists it) and plan as if it will be extended; collect finished
  results hourly, so an ending lease costs at most the run in flight; stop work on a host only when its end is under
  two hours away and its owner confirms no extension.

## Paid Hosts and Fit

A host's lease (inventory `lease` or `install --lease key=value`, owner only) has a `kind`:

- `none`: an owned machine, no end.
- `free`: a leased pool whose extensions cost nothing; `planned_end` is the current end, and the owner renews
  by its own rule (for example every hour when 48 hours or less remain and the host is in use). Claims past
  the end are granted with a warning.
- `paid`: a cloud instance billed until deleted (for example Brev): `planned_end`, `usd_per_hour`,
  `gpu_type`, `instance`, `provider`, `started`. A claim must state `--hours`; one that would run past the
  planned end is refused, and the answer is an `extend` request, never an extension on your own.

`fit` ranks every host for a job: GPU type and memory, free capacity now, time to start (from the `--hours`
of running claims), time left against time needed (job plus a 15-minute margin), and the extra cost. A new
instance costs only its wait (create, not-ready time after "Ready" of about 12 minutes, setup, `--stage-min`
data staging) and its dollars (`--new-usd-per-hour`, or `fit.new_instance.usd_per_hour` per GPU type in the
policy). It recommends one of: `reuse` (fits as is, starting no later than a new instance plus 30 minutes),
`wait-for-owner` (an extension request is pending, for at most as long as a new instance takes to be ready,
unless `--can-wait`; after that its `launch-new` advice prints the command to withdraw the request),
`reuse-with-extension` (only a really good fit: it
starts within 10 minutes, needs at most 4 extra hours and $20, and costs at most half a new instance;
thresholds in `fit`) with the `extend` command to run, or `launch-new`. Launching stays with the nvidia-brev
plugin's `brev-run` flow; afterwards the creating project adds the instance to its `hosts.json` and makes it
shareable:

```bash
python3 <tool> install --host <new> --lease kind=paid --lease planned_end=<end> \
  --lease usd_per_hour=<rate> --lease gpu_type=<type> --lease instance=<name>
```

Before any teardown of a shared instance (brev-run stops an instance it will reuse within about a day and
deletes the others), the owner runs `audit --host <new>` and stops or deletes only on a `delete` advice
(`uninstall` first when deleting); otherwise the instance stays and its guests are told why. A stopped
instance answers no ssh, so `status` and `audit` show it unreachable until it is started again.

## Owner Audit

`audit` lists the hosts this project owns: lease and time left, idle time since the last claim, work
outside claims, GPU-hours and CPU-hours by project, cost so far for paid hosts (rate times hours since
`started`), open requests, hosts not installed yet or whose sharing list differs from `resource-sharing.json`
(each with the exact `install` command; `install --all` fixes them all), old run folders, and a
recommendation:

- `extend`: live claims run past the end, a guest asks for more time, or a free lease is in use and ends
  within 48 hours;
- `decline`: a guest's extension request is older than a new instance takes to be ready (about 35 minutes),
  so its guest has most likely launched elsewhere;
- `keep`: in use, protected by an approved objection, or idle within its limit;
- `release` (free leases): idle 24 hours or more with no claims or objections;
- `delete` (paid): idle 30 minutes or more, or past its planned end, with no claims, extension requests or
  objections;
- `check`: unreachable or not installed (a paid instance keeps billing while unreachable), busy outside
  claims (a GPU, a core or more of load, or unclaimed processes of 1 GiB or more: never delete or release
  under it), or a pending objection to decide first.

Owners run `audit` at least every two hours (hourly is better, beside other scheduled checks), answer the
open requests it lists, and act on every line: never leave a paid instance idle without a decision. On a
`delete` advice, an instance the owner will reuse within about a day is stopped instead (a stopped instance
still bills its storage); any other is deleted. Releases, stops and deletions still need the owner's (or its
human's) confirmation under the project's safety rules.

## Host Health and Live View

Two companions of `hostclaims.py` sit beside it (`<tools>` below is the folder holding `<tool>`) and read the
same inventory (the project's `hosts.json` plus the hosts shared with it; `--host`, `--hosts`, `--project`,
`--local-root` and `--ssh` work as there):

- `python3 <tools>/hosthealth.py [--host H] [--fix] [--json]` checks each GPU host over ssh: the GPUs visible
  against the inventory's `gpus` count (a GPU in confidential-computing mode, off the bus or failing to start
  is missing from the driver's list), Xid codes in the kernel log since boot (the application-class codes 13,
  31, 43 and 45 are left out), GPU start failures, and per GPU persistence mode, MIG and compute modes, power
  limit against its default, throttling (hardware or thermal slowdown, power brake) and uncorrected ECC errors;
  plus load, free memory and disk, and the CPU governor. Exit codes: 0 healthy, 3 problems found, 4 a host did
  not answer.
- `--fix` first applies three settings that are safe under running jobs: persistence mode on, each GPU's power
  limit back up to its default (never above it by default: a maximum over the default can overload a power
  supply the GPUs share), and the `performance` CPU governor. An owner who wants the most performance passes
  `--power max` (or sets `"health": {"power": "max"}` in the policy block of `resource-sharing.json`): the check
  then holds each GPU's limit to the most the GPU allows (`power.max_limit`) and `--fix` raises it there, on
  hosts the project owns only (the host's own record when `--fix` reads it, else the inventory's owner). On any
  other host the check and the fix keep the default as before, so a guest that sets the policy gets no lasting
  problem there (with `--fix` the output says why). The two are
  equal on many GPUs. A reboot resets these settings (persistence mode survives only where
  the persistence daemon runs), so owners run `hosthealth.py --fix` with their hourly checks. It acts as the
  calling project and follows ownership: on a host the project owns on record (the host's `host.json` from
  `install`, else an `owner` its inventory entry names; a host listed without one is nobody's here), every GPU
  and core (and it enables the persistence daemon); on a host where it only holds live claims, the cores of
  those claims and the GPUs they hold that nothing else uses (a GPU another claim or work outside the claims
  also uses, and a core whose frequency policy also covers other cores, are left alone); elsewhere nothing. The
  rest it only reports: the owner repairs it (driver, reboot, GPU reset, modes), and a guest files what it
  printed as a maintenance `request`. Fixing and reading the kernel log need root, which a login other than root
  gets through passwordless `sudo`, used only by `--fix` on a host the project owns (on another's host a sudo
  attempt lands in the owner's security log): guests and runs without `--fix` read and change only what the
  login may, and the output says what it could not do or read.
- Verify each repair with real GPU work: a host can list its GPU in `nvidia-smi` and still fail CUDA
  initialization or kernels. After a repair, run a tiny allocation, matrix multiply, synchronization and result
  check in the environment its jobs use before the host takes jobs again.
- `python3 <tools>/hostdash.py` is a live full-screen view, refreshed every 5 seconds (`-n` to change; `q`
  quits), of each host's load, memory, disk and GPU use, its claims (project/job, class, GPUs, cores, age; `no
  process` once the process a claim is tied to has gone), tmux sessions and busiest processes (another login's
  by program name only, as a command line can carry secrets), over one ssh connection per host kept open
  between refreshes. `--once` prints one snapshot: after a restart it is a quick first look, before `status`
  and the done markers.

## Pools

A pool file holds named counters with per-project caps, for limits shared by account rather than by host
(for example concurrent sessions on a shared service, or a weekly quota). Kaggle's account limits have their
own layer in the kaggle plugin (its `kaggle-sharing` skill and tool); use pools for other services. Keep a
pool file on one always-on coordination host (`--file HOST:~/.solaris/claims/pools/<name>.json`) or
locally, never in a synced folder (a sync tool copies lock files instead of locking them).

- `pool define --name N --capacity C --cap '*=3' --cap other=2 [--window-hours W] [--borrow-max-hours B]`,
  or `--preset FILE` with a `pools` map. `window-hours` above 0 makes a budget pool: released amounts keep
  counting for that long (for example 168 for a weekly quota). The project that first defines a pool owns
  its capacity and caps; others ask it to change them.
- `pool acquire --name N --id ID [--amount A] [--hours H]` before using the resource; a repeat with `--hours`
  extends the hold. Past its cap a project may `--borrow` (slot pools only) while no other project holds any,
  for at most `borrow-max-hours`, extensions included. Holds expire at their end.
- `pool release --name N --id ID [--actual A]` when done (`--actual` records a budget pool's real use).

## Conventions for Agents

- Check `status` before heavy work; start every host job with `run` (or `claim` it first) and stay inside
  the claim: its cores, GPUs, RAM, disk and hours.
- Before heavy jobs, also see who else is on the host beyond what `status` counts: logged-in users, containers and
  recent logins (`who`, `docker ps`, `last`). When another project moves in, move your work elsewhere and leave your
  files in place unless the owner says otherwise. Pick x86 hosts for x86-only stacks (some libraries ship no ARM
  CUDA builds).
- A server that serves other jobs' runs (an inference engine behind an evaluation queue, say) is part of
  their claim: send it no diagnostic or experimental requests. Start your own under your own claim, or use it
  only while its runs are held between batches: one memory-heavy request can kill it and every run it serves
  (asking vLLM for prompt log-probabilities over a 5k-token prompt allocated about 5 GiB of logits, and its
  engine died).
- Give every `claim` a `--pid` (the job's own long-lived process; more attach with a repeat `claim --pid` or
  `renew --pid`), or renew it (`renew --job J`, or re-run the same `claim`) within 15 minutes, or it lapses and
  its capacity goes to others. When a job outlives its planned end, or becomes preemptible, `renew` the claim
  instead of releasing and claiming again.
- Copy bulk data between hosts in a few parallel flows (for example three `rsync` processes, each over its own
  part of the tree), under a claim on each end: one flow seldom fills the link, and three copied about 1.7 times
  as fast as one (58 GB in about 12 minutes).
- Never release, edit or delete another project's claim or hold; `reap` moves stale claims to `stale/`
  without deleting them and is safe for anyone to run; only the owner's `reap` also prunes run folders
  older than 14 days.
- After a restart, rebuild your running list from `status` on every host, then read the done markers and
  `stale/`: resume from checkpoints or close.
- Put the claim id, tmux session and done marker in your notes and job ledger.
- Honour yield requests: checkpoint on `SIGTERM` in long jobs (a job run with `--yield-signal none` watches
  `$HOSTCLAIMS_YIELD_FILE` instead and ends itself within the grace, before the `KILL`). Pair claims that only
  work together with `--yield-with`, so they stop in order.
- Owners audit their hosts and answer requests promptly; guests relay requests to the owner and wait.
- Guests run `shared` at least hourly and act on `NEW` and `GONE` hosts before `shared --ack`; owners run
  `install --all` after adding machines or changing `share_with`, and tell the projects they share with.
- Guests keep everything they write on a host, caches included, inside their own folder there, and report
  host problems to the owner instead of fixing them (see Owners, Guests and Requests).
- Owners run `hosthealth.py --fix` with their hourly checks and repair what it reports; anyone may look with
  `hosthealth.py` or `hostdash.py --once`.

## Troubleshooting

- **Exit 3, does not fit**: the answer lists the reasons and any yield candidates; try another host (`fit`),
  wait, or ask to yield. It also covers a host that is draining, dedicated to another project, or not shared
  with you (a host with no sharing list admits only its owner). Unclaimed GPU use shows in `status`: its
  owner claims or stops it.
- **Exit 5**: a sharing rule (ownership, another project's claim or process, a younger or non-preemptible
  yield target, an owner action run as another project).
- **Exit 4**: the host did not answer in time. The claim or launch may still have happened: run `status` on
  that host before trying another one (repeating the same `claim` or `run` only returns the existing
  claim). Claims on the host stay valid and its jobs keep running.
- **Exit 6**: the hosts shared with this project changed since the last `shared --ack`: act on the `NEW`,
  `GONE` and `CHANGED` lines, then ack. A `NEW` host that is not installed or does not admit you (`--probe`)
  is its owner's to sync with `install --all`.
- **`orphan`**: the watcher died but the job lives; the claim is kept. When the job ends, the claim goes
  stale after 15 minutes with no done marker; read `job.log`.
- **`stale/`**: the host rebooted (boot id changed), the job died unwatched, or a claim without `--pid` was
  not renewed; the file keeps the claim and the reason (for a claim tied to processes, when one was last seen
  alive). Claim the job again within the hour and the new claim takes back the cores its still-running job is
  pinned to, then the old claim's cores while they are free.
- **`pinned outside claims`** or **`runs on cores ... outside its claim`** in `status`: a process is pinned to
  cores no claim of its own holds (a job whose claim lapsed, or one pinned by hand); no other claim gets those
  cores while it runs. Its project claims the job again (the same job's new claim, or one with `--pid` for it,
  takes those cores back), re-pins it (`taskset -acp <cores> <pid>`), or stops it.
- **`claims ... share cores`** in `status`: two claim files name the same core (a hand edit, or a tool version
  that did not check); the tool never grants such a core again, and one of the two projects releases its claim
  and claims again.
- **A yield asked, the job still running**: a job run with `--yield-signal none` got no signal and is stopping
  itself; the watcher sends `KILL` when its grace (`--grace-min`) runs out. A claim without a watcher sees the
  request only in `status`: its project stops the job.
- **Lock busy**: another call held the lock for 60 seconds; retry. Locks work on the host's own disk, not
  on network or synced folders.
- **"belongs to another machine"** or **"network filesystem"**: the home folder is shared between hosts;
  set a per-host `root` on local disk in the inventory. After a genuine move, the owner's `install` takes
  the folder over.
- **No pinning** (`wrapper.log` says `not pinned`): the host lacks `taskset`; install util-linux.
