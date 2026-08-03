---
name: brev-run
triggers:
  - "run on brev" / "run in the cloud" / "cloud gpu run"
  - "train on brev" / "provision and train" / "remote training run"
summary: Full autonomous lifecycle for running project workloads on Brev cloud GPUs -
  pick and create the instance, ship the payload, set up, run + monitor the job, pull
  results back, ALWAYS tear down, and append the mandatory cost-ledger row. Assumes an
  authenticated CLI (else run brev-setup first). Deep CLI reference: the plugin's
  brev-cli/ upstream mirror.
---
_Rev. 10_

# Skill: brev-run - autonomous cloud runs <!-- omit in toc -->

Run a project workload (training, evaluation, batch processing) on a Brev GPU instance,
managing the entire lifecycle without babysitting. The agent does every step itself; the
user is only needed for authentication (see `brev-setup`) and for cost approvals above the
guardrail threshold. Command/flag details, search filters, and canned workflow prompts:
`brev-cli/reference/`, `brev-cli/prompts/`, `brev-cli/examples/` in the plugin source
(pristine upstream mirror; not copied into ai-packs on install - from a copied ai-pack,
read the upstream directly at `github.com/brevdev/brev-cli` under `.agents/skills/brev-cli/`).

## 0. Preconditions

`brev ls` succeeds (else run `brev-setup`), and the project has `ai/.memory/brev-costs.md`
(create from the template below if missing).

## 1. Pick and create the instance

1. Size from the job: GPU generation x count, VRAM per GPU, disk >= 3x payload. Match the
   compute to what the workload's recipe assumes (e.g. keep the same total batch across a
   different GPU count).
2. `brev search --gpu-name <gpu> --sort price` - prefer NVIDIA-infra providers (launchpad)
   when the data's handling rules require NVIDIA-provisioned hosts, then cheapest with
   acceptable boot time. `--dry-run` on `create` to preview when unsure. Filter syntax:
   `brev-cli/reference/search-filters.md`.
3. `brev create <name> --type <type>` (waits for ready; default timeout 300s - pass
   `--timeout 900` for slow bootors). Name convention: `<project-or-org-prefix>-<job>`.
4. Record start time + $/h at creation. **Every instance gets a row in
   `ai/.memory/brev-costs.md`** (created/deleted UTC, rate, hours, cost, purpose) - appended
   at teardown, including aborted attempts. The run report must state the actual cost.

Ledger template (`ai/.memory/brev-costs.md`):

```markdown
# Brev Run-Cost Ledger

Every cloud instance this project creates gets a row: created/deleted timestamps (UTC),
rate, actual cost (billed while the instance exists, incl. setup/idle).

| Instance | Type | $/h | Created (UTC) | Deleted (UTC) | Hours | Cost | Purpose / outcome |
|---|---|---|---|---|---|---|---|
| **TOTAL** | | | | | **0** | **$0** | 0 closed instances; note any still-running ones here |
```

Keep the **TOTAL** row as the last line: update its hours/cost/count when appending an
instance row, and note still-running instances (rate + start time) in its outcome cell
until their rows land at teardown.

## 2. Ship the payload

- Package on the source host: `tar -chzf` (dereference symlinks; exclude venvs, run
  outputs, caches) - or rsync `-aL` directly for trees.
- **Preferred: direct source-host -> instance push** (datacenter-to-datacenter, much faster
  than relaying through a laptop). Instances cannot reach private 10.x networks, but the
  reverse works when the source host has outbound internet: read the instance's public
  endpoint from `~/.brev/ssh_config` (after `brev refresh`), authorize the source host's
  key via `brev exec <name> -- bash -c "echo '<pubkey>' >> ~/.ssh/authorized_keys"`, then
  rsync from the source host straight to `<user>@<ip>` with `--partial`.
- Fallback (no outbound from the source host): relay via the local machine's scratch space
  (never cloud-synced paths), then `brev copy <tar> <name>:~/`.
- Restricted data (NDA/PII) may only go to hosts its handling rules allow - check the
  project's data-handling notes (`ai/.memory/resources.md` or equivalent) before shipping;
  never route through third-party storage buckets.

## 3. Set up + launch

1. Setup: prefer `brev create ... --startup-script @setup.sh` - the script runs at first
   boot (before you can even ssh in), so the venv/deps are ready when SHELL goes READY;
   have it write a marker file (`echo STARTUP_DONE > ~/startup.marker`) and log to
   `~/startup.log`. Field-verified: venv + torch install completed unattended. Otherwise
   run the same script post-boot via `brev exec <name> @setup.sh` (keep it linear - see
   Field Gotchas). Either way: create a venv, install the project's pinned deps (mind the
   GPU-architecture torch matrix in Field Gotchas), extract the payload, sanity-check
   `nvidia-smi` + one real workload step (a single training iteration catches arch/driver
   mismatches a matmul smoke test misses).
2. Launch detached ON the instance (never hold the ssh session):
   `setsid nohup <job> > run.log 2>&1 < /dev/null & disown`, with a completion marker
   (e.g. `echo DONE >> ~/progress.log`) so polling is trivial.
   Multi-GPU: `python -m torch.distributed.run --nproc_per_node=N` (+ framework-appropriate
   sync flags, e.g. `--sync-bn`), keeping the recipe's total batch.

## 4. Monitor

- Poll `tail`/`grep` over ssh on a sensible cadence (minutes, not seconds); watch for the
  completion marker AND failure signatures (Traceback, CUDA error, NCCL, OOM, Killed) -
  silence is not success.
- Optionally sync live artifacts (e.g. `results.csv`) back to an internal host so project
  dashboards can show the run in flight.
- If the instance dies or errors terminally: capture logs first, then decide retry vs abort.

## 5. Collect + ALWAYS tear down

1. Pull results to their canonical project location - metrics, logs, and **always both
   trained checkpoints (`best.pt` and `last.pt`)**: weights enable re-evaluation and reuse
   later; once the instance is deleted they are gone.
2. `brev delete <name>` - **unconditional**, also on failure/abort paths. Verify with
   `brev ls` that nothing from this job lingers (also check stopped instances and any
   launchables created along the way - stopped is not free on all providers).
3. Append the ledger row (created/deleted UTC, hours, actual cost, outcome). **Verify the
   cost against actual billing** rather than estimating from wall-clock x rate - Brev bills
   per-second on RUNNING time only (BUILDING/provisioning is free; no hour round-up), so
   estimates overshoot. Actuals per instance:
   `curl -H "Authorization: Bearer $(jq -r .access_token ~/.brev/credentials.json)" https://brevapi.us-west-2-prod.control-plane.brev.dev/api/organizations/$(jq -r .id ~/.brev/active_org.json)/usage`
   The usage feed lags real time - re-check a while after teardown for the final number,
   and mark the row "billed" once verified.
4. Report: what ran, where results landed, wall-clock, actual cost.
5. Log the run in the project's `ai/.memory/interactions.jsonl`; record durable host facts
   in `ai/.memory/resources.md`.

## Field Gotchas (learned on real runs)

- `brev create` may end its readiness poll with a benign `ErrorForbidden` - the instance
  still provisions; watch `brev ls` instead. RUNNING != usable: wait for SHELL `READY`,
  and even then the first ssh can fail for **minutes, as a connect timeout** (port 22
  unreachable, not just a refusal; seen on aws g5 well after "Ready") - retry-loop the
  first connection with a generous window rather than treating timeouts as fatal.
- `brev copy` right after readiness hangs silently past 120s (no output, no fast-fail)
  while the instance's sshd is still coming up - gate the first copy on an ssh
  `echo OK` retry-loop succeeding, then copy.
- Advertised boot times and disk sizes in `brev search` can be wrong (a "1m boot / 56TB"
  launchpad 2xH100 was ~25 min and 1.8 TB) - verify with `df -h` / `nvidia-smi` on arrival.
- `brev create --min-disk <GB>` is a **search filter, not a provisioning request** - an aws
  g5 created with `--min-disk 200` arrived with a 117 GB root, of which the AMI itself ate
  61 GB. Budget disk as: AMI overhead (~60 GB on aws images) + dataset + **2x dataset for
  any sharded/cache-building workload** + checkpoints; `df -h` after boot and free space
  (docker system prune -af often reclaims ~30 GB on brev AMIs) BEFORE launching a long job -
  a disk-full at the final checkpoint save wastes the whole run (torch.save dies with
  "inline_container.cc unexpected pos").
- Fresh Ubuntu images lack `python3.X-venv` (ensurepip) - `sudo apt-get install -y
  python3.$(minor)-venv` first; sudo is passwordless on the default user.
- `brev exec` holds the session even for `nohup ... &` children - detach long jobs with
  `setsid nohup ... < /dev/null & disown`, or the exec call hangs until the child exits.
- `brev exec` breaks on non-trivial payloads: multi-line `-- bash -c '...'` bodies and `@file`
  scripts containing heredocs or `&` backgrounding hang forever WITHOUT executing anything.
  Keep `@file` scripts linear (setup/install/echo markers); do heredoc writes and detached
  launches over plain ssh to the instance endpoint instead. (Filed: `__issues/cli-exec-multiline-hang.md`.)
- `pgrep -f` / `pkill -f` self-match: the pattern appears in the remote shell's own command
  line (under `brev exec` and plain `ssh host '...'` alike), so `pkill -f <pattern>` kills
  the session itself mid-script and everything after it silently never runs (ssh exits 255).
  Always bracket a char in the pattern (`train[.]py`) - and keep the kill in a **separate**
  ssh call from any command that mentions the matched path (a script path in the same
  command line re-triggers the self-match).
- GPU-architecture torch matrix: Hopper (H100/H200) runs mainstream cu121 builds; Blackwell
  (B200, RTX PRO 6000, sm_120/sm_100) needs cu128+ (e.g. torch 2.7.1+cu128); Blackwell
  Ultra B300 (sm_103) is stricter still - torch 2.7.1+cu128's NVRTC rejects sm_103
  ("invalid value for --gpu-architecture"), needs torch 2.13.0+cu130. Legacy checkpoints
  under torch >= 2.6 need `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1`.
- Fresh images often have the driver but NO CUDA toolkit (`nvcc`, `libcurand` etc. absent) -
  pip GPU stacks that don't bundle CUDA (e.g. cupy-cuda12x) need the runtime wheels too:
  `pip install nvidia-cuda-runtime-cu12 nvidia-cublas-cu12 nvidia-curand-cu12 ...`
  (torch cu-suffixed wheels bundle their own and are unaffected).
- During a heavy rsync to the instance, `brev exec` connections can fail/queue (sshd busy) -
  monitor via the direct SSH endpoint (`<user>@<ip>` from `~/.brev/ssh_config`) instead of
  the brev CLI until the transfer completes.
- The brev proxy endpoint (`global.prd.ga.run.brev.nvidia.com`) can drop or reset sessions
  under load - prefer the instance's direct public IP once known (`curl -s ifconfig.me`
  from the instance), with the brev key (`~/.brev/brev.pem`).
- Billing model (observed): per-second, RUNNING state only - slow BUILDING costs nothing,
  and an advertised $/h may appear in the usage feed split into components at fractional
  rates (sum matches). A STOPPED instance still accrues storage charges indefinitely -
  stopped is not free; delete, don't stop, when done.
- Stop/start (verified on gcp): the disk persists (venv/payload intact) but the **public IP
  changes** - re-run `brev refresh` and re-resolve the direct endpoint after every restart,
  and re-authorize nothing (keys ride the disk). Restart back to SHELL READY took ~12 min;
  multi-GPU DDP (`torch.distributed.run --nproc_per_node=N`) works stock on the brev images.
- `brev create` launched non-interactively (no tty, backgrounded) can fail silently - it
  printed nothing and no instance appeared; the identical foreground retry worked. Verify
  with `brev ls` after every create rather than trusting the create call.
- Provider switch mid-job is cheap before the payload lands: `brev delete` the laggard,
  `brev create` the alternative - ledger both.

## Guardrails

- Never leave an instance running unattended without an owner + teardown plan; if the
  session ends mid-run, the teardown check is the FIRST thing the next session does.
- Confirm with the user before creating instances above ~$15/h or beyond 8 GPUs.
- All costs come out of the org's budget - always state $/h at creation time.