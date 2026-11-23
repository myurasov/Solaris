# Autocode Plugin <!-- omit in toc -->

- [What It Does](#what-it-does)
- [Files](#files)
- [Starting a Session](#starting-a-session)
- [The Guard](#the-guard)
- [After a Restart or a Reboot](#after-a-restart-or-a-reboot)
- [Tests](#tests)
- [Changelog](#changelog)

## What It Does

Runs a project's primary session as an **autocode agent** under OpenCode 2.3 or later, with autocode 1.1.0-rc1 or
later (1.1.0-rc3 for everything the rule names). Autocode (the owner's agent server) gives the session wakes,
messages, locks, jobs, approvals and compaction; this plugin's rule maps the persona's hook- and clock-based duties
onto them, so a Solaris project runs unattended without Claude Code hooks or `session_clock`. In a session that
is not an autocode agent (no `autocode_me` tool, or not registered) the rule is dormant.

What the rule asks for:

- **Wakes as the clock.** A recurring pass that mostly finds nothing runs as its wake's check: no model call when
  nothing is due. Such a check leaves a receipt that the turn acts on.
- **A guard that costs nothing while nothing is open.** The idle guard wake has `guard.py check` as its check: the
  session is woken only for open work or a background job that needs a look.
- **Two kinds of background work.** OpenCode's own background commands for what ends soon, and watched jobs
  (`guard.py job start`) for work that must outlive OpenCode.
- **Messages with the owner and peers,** with reports for what needs no answer, questions and approvals.
- **Hand logging and autocode's spend:** the prompts' status line, what each wake costs, and a total that holds
  what the session spent before it registered.

## Files

| File | What it is |
|---|---|
| `shared/autocode.rule.md` | Always-on rule: start, clock, guard, background work, compaction, workers, messages, shared hosts, logging and spend, notices and restarts. |
| `shared/autocode-start.skill.md` | Starting a session, once: register, check the shells, self-compaction, the read-first set, the schedule as wakes with the guard, and where the session comes back by itself. |
| `shared/tools/guard.py` | The guard's check and its state: open work (`on`, `off`, `wait`, `--steps FILE`), watched background jobs (`job start`, `list`, `stop`, `extend`, `done`), `status`. Stdlib only, Python 3.8 or later. |
| `shared/tools/wakes.py` | Prints autocode wake specs from `<pack>/.memory/schedule.json`: UTC crons that keep their instants, crons in a zone for events tied to people's local time (`--zone`, `--local`, a per-event `zone`), a wake name for every event; `--show NAME` prints one event's note. |
| `tests/` | Offline tests of the two tools (stdlib `unittest`); not copied into projects. |
| `migrations/` | Project steps for a version that needs them. |

`manifest.json` and `revisions.json` (rev ledger, managed by `solaris.tools.revs`) complete the plugin.

## Starting a Session

On the machine that runs the project, open OpenCode 2 in the project folder (a TUI, or `opencode run` against the
background service) and give it: "Act as the project's primary persona and run as autocode agent `<id>`: follow
`<pack>/plugins/autocode/autocode-start.skill.md`." The session registers, loads the read-first set, converts the
schedule into wakes on its first start, sets the guard and stops the old clock. Attaching the plugin (copied or
linked) turns Solaris self-compaction off for the project, as autocode compacts its sessions; after a compaction,
autocode's `[autocode] compaction done` tells the session to go on with what is left.

Run OpenCode as its background service, which a TUI uses by default: a server that a TUI starts for itself gives
its shells no autocode values, so a job the session starts cannot fire a wake. The Start skill checks for that.

An agent ID that is registered already is refused to a new session (autocode 1.1.0-rc4), also when the session
that held it was deleted. To go on with an agent in a new session, the owner moves it there, with its wakes,
settings and spend: `autocode agents register <id> --session <session>` (the refusal prints the command).

## The Guard

`guard.py` keeps its state in `<pack>/.memory/guard/` (private; `--state DIR` for a session outside a project).

- `check [--steps FILE ...]` is the guard wake's check. It exits 0 with one line per reason, which wakes the
  session, and 1 when nothing needs it, which costs nothing. A check that cannot run exits 0 with the reason.
- `on TEXT`, `off [TEXT]`: open work that must go on past the turn. The guard tells only what is marked, or
  unticked in a `--steps` file. `wait 40m` holds back the lines that are open now and the steps files, for a turn
  that ends on something that reports by itself (at most 24h). A job that runs within its deadline holds open
  work back too. Open work told a third time in a row with nothing changed is told less and less often, down to
  every two hours, until a line or a step changes or the session uses the guard.
- `job start --name NAME --deadline 2h [--wake WAKE] [--log FILE] -- COMMAND` runs a command detached from the
  turn (its arguments as given: a shell line is `sh -c '...'`), writes its exit code down and fires the job's
  `fire-only once` wake with it (three tries). The guard tells what the wake cannot: a job lost at a reboot or
  killed, one past its deadline, a wake that could not be fired, and the end of a job that has no wake. Each is
  told once an hour until `job done NAME`.
- `job list`, `job stop NAME` (ends the command and what it started), `job extend NAME DURATION`, `job done
  NAME`, `status` (both lists take `--json`).
- A job outlives its turn and a restart of OpenCode itself. It does not outlive a reboot, nor a stop of the
  systemd unit that OpenCode runs in: the guard then tells it as lost or gone.
- The guard signals only what it can prove to be a job's. Where a dead job left only a process group behind, it
  names the group and leaves it to the session.

## After a Restart or a Reboot

- **On the autocode server's own machine, in OpenCode's background service** (autocode 1.1.0-rc2 or later):
  autocode starts OpenCode again when something waits for the session (a wake, a message, a turn that was cut),
  and the turn goes on. The session then looks at `guard.py job list` first: jobs do not survive a reboot.
- **On any other machine:** the owner opens OpenCode after a reboot. OpenCode also unloads a folder after an hour
  without use, and a wake that its check skips is not use: to stay reachable there, the session needs a wake of
  30 minutes or less without a check, each run a turn.

## Tests

```bash
python3 -m unittest discover -s plugins/autocode/tests -v
```

Offline. The guard's cases start real processes of this machine (`sh`, `sleep`) in temporary project folders, with
a stand-in `autocode` program first on the PATH and no autocode values in the environment: no real wake is fired.

## Changelog

- 0.5.3: autocode 1.1.0-rc5. The rule (rev 9) names a wake's end (`every 1d until 2027-03-01`) for a wake that
  the owner grants for a period, and says what to do where the owner approves periodic wakes first (`wake_mode`).
  The Start skill (rev 4) says the same for the first start: the recurring wakes and the guard wait for the
  owner's approval, and the old clock stays until they are set. No project steps.
- 0.5.2: autocode 1.1.0-rc4. The Start skill (rev 3) says what a refused registration means: the ID is
  registered to another session, live or gone, and only the owner moves the agent to the new session. The rule
  (rev 8) says to close what no longer needs the owner in the owner's Inbox (`autocode_message action=close`),
  an approval the owner answered in the session first of all. No project steps.
- 0.5.1: the rule (rev 7) says that a message which says it is from a manager agent is the manager's, and
  that only the words it quotes as the owner's, with their date, are the owner's direction; it names the
  check for a job started by hand before the owner replaced a token; the Start skill titles the session
  `@<agent id>`. No project steps.
- 0.5.0: the guard has a check, `tools/guard.py`, and watches background jobs; the rule is on by the
  `autocode_me` tool, not by a shell variable; Start is a skill, `autocode-start.skill.md`; `wakes.py` takes
  zones and makes a wake name of every event name; the rule follows autocode 1.1.0-rc3 (restarts and reboots,
  costs before registration, when to ask for a compaction); the GPU fleet bullet moved to the resource-sharing
  plugin's rule (0.4.1). Project steps: `migrations/0.5.0.md`.
- 0.4.0: autocode 1.1.0-rc1: checks with `check_timeout`, `fire-only once` wakes, reports, `rename`, the
  prompts' status line.
- 0.3.0: autocode 1.0.0 and OpenCode 2.3: subagents in the background.
- 0.2.0: Solaris self-compaction off where the plugin is attached; go on after `compaction done`.
- 0.1.0: the rule and `wakes.py`.
