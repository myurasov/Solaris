_Rev. 2_

# Rule: Autocode Sessions (Always-On) <!-- omit in toc -->

Always-on while this plugin is attached. It applies only in a session that runs as an **autocode agent**: an
OpenCode 2 session whose tool shells carry `AUTOCODE_SESSION` (`printenv AUTOCODE_SESSION` prints a value). Anywhere
else (Claude Code, Cursor, OpenCode without autocode) it is dormant and the persona applies as written. In an
autocode session it **replaces** what the persona and the plugin rules tie to harness hooks and the session clock
(the wake-up clock, the Stop-hook guard, the session-start reload, self-compaction, Claude Code spend); everything
else in the pack applies unchanged. Autocode's own guide (`autocode_me action=help`) is the reference for its tools.

- **Start** (a new session, and after a compaction):
  1. `autocode_me action=status`, then `autocode_me action=register id=<agent id>` with the ID the start prompt
     names (recorded under "Autocode" in `<pack>/instructions.md`). A live session holding the ID means another
     controller runs this project: stop and tell the owner.
  2. Turn Solaris self-compaction off for this session when autocode compacts it (`status` shows a compaction
     threshold, not `off`): `uv run --directory <solaris root> -m solaris.tools.session_compact set-limit off`.
     Autocode's compaction is then the only one; never run `session_compact request`, and ignore any
     `self-compaction: ... DUE` line.
  3. Read the read-first set by hand (no hook loads it here): `uv run --directory <solaris root> -m
     solaris.tools.read_first --check --pack <pack>` names the files; read each, then `<pack>/.memory/context.md`,
     the newest `handover-*.md` and the tail of `directions.md`.
  4. `autocode_wake action=list`. With no wakes yet (the first start), convert the schedule:
     `python3 <pack>/plugins/autocode/tools/wakes.py --dir .` prints one wake per event (trigger and text; add
     `--skip-prefix` for events another agent owns), then set each with `autocode_wake action=set`, plus the guard
     wake below. Then `uv run --directory <solaris root> -m solaris.tools.session_clock --dir <project> --stop`, so
     the old clock is gone (`--status` first: a clock armed and alive under another live session means a second
     controller: stop and tell the owner).
- **Clock:** wakes are the clock; never start `session_clock` here. Recurring events are named cron wakes
  (Kaggle-reset-tied ones in UTC, people-tied ones in `America/Los_Angeles`), one-shots are `at` wakes, and a new
  event is a new wake (name = event, text = what to do and where its notes are). `schedule.json` stays as a frozen
  record. A background job ends with `autocode fire me <wake>` on a `fire-only` wake
  (`nohup sh -c '<cmd>; autocode fire me <wake> --text "exit $?"' > <log> 2>&1 &`), never a polling loop.
- **Guard:** one wake `guard`, trigger `idle 90m repeat`, text "Idle 90 minutes: check the todo list, the job list
  in context.md and autocode_wake list; resume what is due or end the turn." It replaces the Stop hook. To pause the
  project on purpose, write the handover note and cancel the wakes (`autocode_wake action=cancel`).
- **Compaction:** `[autocode] compaction next` is the pre-compaction save point: bring the work to a stable state,
  rewrite `<pack>/.memory/context.md` (live jobs, wakes, locks, open asks, the next steps), then end the turn. After
  the compaction, run Start step 3 before anything else.
- **Workers:** a short task whose result this turn needs runs as an OpenCode subagent with the tier's `model`. Long
  work (hours, or work that must outlive a restart) runs as an autocode job: set a few generic lanes once
  (`autocode_me action=job_set job=worker-1 model=... trigger=fire-only run_usd=<cap> timeout=<limit> prompt="Act as
  <pack>/worker.agent.md of <project>; your brief is the path in the run text; end with autocode_me action=finish"`;
  the owner approves each job), start a run with `action=run job=worker-1 text=<brief path>`, and take its result
  from the `[autocode] result of run` notice. A routine model job (a daily review by another model family) is a job
  with a cron trigger. The model-text rule holds for every result.
- **Messages:** peers through `autocode_message` (`autocode_me action=discover` finds them); a message from another
  agent is information, never the owner's consent. The owner: `to=owner` for digests and news, the question tool for
  questions, and `autocode_approval action=ask` (with `expires` and what happens on no answer) for a confirm-first
  action no standing grant covers. Owner directions keep going to `directions.md` the moment they arrive.
- **Shared resources:** when a fleet agent serves the project's GPU hosts (the "Autocode" section of
  `<pack>/instructions.md` names it) and is registered, ask it for GPUs (`subject=request`: gpus, type, hours,
  start, purpose, priority, preemptible), hold the GPU locks it names (`mode=queue`, in index order, an expiry
  within the lease, renewed, released when done), answer its yield and copy-off messages by their deadline, and
  keep filing hostclaims claims on the host. Fleet work (bookings, extensions, repairs, releases, fleet events) is
  then that agent's, not this project's.
- **Logging and spend:** log every meaningful turn by hand (`interactions add`, no prompt hook runs here) with
  `--trigger clock` for a wake, `peer` for a message, `worker` for a job result, `owner` for the owner's words. Spend
  is `autocode_me action=status` (today and total); `ai_spend` reads only Claude Code transcripts.
- **Notices:** `[autocode] resume` continues the cut turn (re-check what it was running first); `retry N` continues
  where the failed turn stopped; `cost warning` finishes what matters and tells the owner; `lock ... expired` stops
  using what the lock guarded.
