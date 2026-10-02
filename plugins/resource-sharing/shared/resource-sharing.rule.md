_Rev. 4_

# Rule: Resource Sharing (Always-On) <!-- omit in toc -->

Always-on while this plugin is attached: what must hold whenever more than one agent or project uses the same
hosts. The how-to is in `resource-sharing.skill.md`.

- **Launch through claims.** On shared hosts, start every host job with `hostclaims.py run` (or `claim`
  it first) and stay within the claim: its cores, GPUs, RAM, disk and hours. Check `status` before heavy
  work; on a refusal, use another host (`fit`) or ask lower-priority claims to yield - never squeeze in,
  not even with requests to a server that serves other jobs' runs (one heavy request can kill it): run
  diagnostics and experiments on a server under your own claim.
- **Keep claims alive.** A `claim` without `--pid` lapses 15 minutes after its last renewal (it prints the
  time): tie it to your process with `--pid`, or re-run the same `claim` before then. `run` needs neither.
- **Never touch another project's claim.** Do not release, edit, stop or delete another project's claim,
  hold or files. `reap` moves stale claims to `stale/` (never deleting them) and keeps orphans; run by the
  owner it also prunes finished run folders older than 14 days, `$HOSTCLAIMS_RUN_DIR` included, so keep
  results elsewhere.
- **One owner per host.** Only the owning project installs or changes a host, sets or renews its lease,
  extends it, does maintenance, and releases or deletes it, always from its own project (never with
  `--agent` naming another project; outside a project only with an explicit `--as-owner`). Guests file `extend` or `request` and show the printed text to the
  owner; nothing is approved for a human owner without their yes. Guests keep everything they write on a
  host, caches included, inside their own folder there (point the cache homes at it before the first job),
  and report problems they find to the owner with a maintenance `request` instead of fixing them; the one
  exception is `hosthealth.py --fix`, which changes only what their own live claims hold alone, without sudo.
- **Pick up sharing changes.** Guests run `shared` at every audit and at least hourly: bring NEW hosts into use
  through claims, stop using GONE ones (release your claims there), then `shared --ack`. Owners run `install --all`
  right after adding machines or changing `share_with`, and tell the projects they share with.
- **Never delete or stop a shared host blind.** Before deleting, stopping or releasing a host other projects
  may use - including a teardown step of another plugin's flow, such as a cloud run's stop or delete at its
  end - run `audit --host <name>` and go ahead only on a `delete` or `release` advice (no live claims, no work
  outside claims, no open objection); then `uninstall` and delete (a stopped host keeps its install).
  Otherwise keep it and tell the owner why.
- **Paid hosts.** Never extend a paid instance on your own: past its planned end, file an extension request
  or launch new. Owners run `audit` at least every two hours (hourly is better), answer open requests there,
  and never leave a paid instance idle without a decision.
- **Honour yield requests.** Long preemptible jobs checkpoint and exit on `SIGTERM` or when
  `$HOSTCLAIMS_YIELD_FILE` appears.
- **Watch jobs by done markers.** Poll `done.json`, never `pgrep -f` over ssh; put `;` rather than `&&`
  before background steps in hand-written launch lines. After exit code 4 (unreachable), run `status` on
  that host before retrying elsewhere: the claim or launch may have happened.
