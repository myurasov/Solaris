---
name: autocode-start
triggers: ["run as autocode agent", "start as an autocode agent", "start the autocode session", "autocode start"]
summary: Start a project's session as an autocode agent - register the agent ID, check that its shells carry autocode's values, turn Solaris self-compaction off where needed, read the read-first set by hand, convert the schedule into wakes (wakes.py), set the idle guard wake with its check (guard.py), stop the old session clock, and tell the owner whether the session comes back by itself after a reboot.
---
_Rev. 3_

# Skill: autocode-start - Start a Session as an Autocode Agent <!-- omit in toc -->

1. [Register](#1-register)
2. [Check the Shells](#2-check-the-shells)
3. [Self-Compaction](#3-self-compaction)
4. [Read](#4-read)
5. [Wakes and the Guard](#5-wakes-and-the-guard)
6. [Where the Session Comes Back by Itself](#6-where-the-session-comes-back-by-itself)

Once, at the start of a session that runs as an autocode agent: the start prompt names the agent ID. What applies
on every turn is in the always-on rule, `autocode.rule.md`. After a compaction only [Read](#4-read) runs again.
`<pack>` is the project's ai-pack folder, and `<solaris root>` the Solaris checkout the project lives in. The tools
are named where a copied plugin has them, `<pack>/plugins/autocode/tools/`; a linked plugin's are in `shared/tools/`
under the plugin root its link file names: use that path in every call, and in the guard's check.

## 1. Register

`autocode_me action=status`, then `autocode_me action=register id=<agent id>` with the ID the start prompt names.
Then title the session `@<agent id>` with OpenCode's session rename, so the owner finds the agent by its name. At
the first start, record the ID and the start prompt under an "Autocode" heading in `<pack>/instructions.md`.

- A registration refused with `id_taken` means the ID is registered to another session: stop and tell the owner.
  Never take another ID in its place, and never unregister anything to get it. The refusal says which of two
  cases it is:
  - **That session still exists:** another controller runs this project.
  - **That session is gone** (deleted in OpenCode): the agent, with its wakes, settings and spend, waits for a
    session. Only the owner moves it to this one, with the command the refusal prints (`autocode agents register
    <agent id> --session <this session>`, in the owner's terminal). Once `autocode_me action=status` shows the
    registration, go on with step 2; the wakes came along, so step 5 finds them there.

  This is autocode 1.1.0-rc4 or later. An older server gives a new session the ID of a session that is gone, by
  itself.
- A tool that answers that this OpenCode is too old (autocode needs OpenCode 2.3.0 or later) means nothing here is
  connected: tell the owner and stop.
- To change the agent's ID later, `autocode_me action=rename id=<new id>` (the wakes, spend, settings and jobs come
  along, and the old ID forwards), then record the new ID in `<pack>/instructions.md` and title the session
  `@<new id>`; never unregister and register for that.
- Registering counts what the session spent before it (autocode 1.1.0-rc3): each earlier step on the day it ran,
  its subagents' included. With a daily cap, what it spent earlier today counts toward the cap. A session that is
  paused when this turn ends met its cap: that is the owner's to raise.

## 2. Check the Shells

Run `printenv AUTOCODE_SESSION` in a shell.

- **A value:** the scripts and jobs the session starts act as it (`autocode fire me`, `autocode send`), and the
  shells find `autocode` by name.
- **Nothing:** open a new shell and look once more: a shell that started while the server could not be reached
  has no values either. Still nothing: the owner's Hosts tab says which it is (`autocode hosts` in the owner's
  terminal). A host marked "no shell values" is an OpenCode server that a TUI or `opencode run` started for
  itself, or an `opencode serve` without a port or a password. The tools, the wakes and their checks still work
  there, but a job cannot fire its wake: the guard tells its end at the next idle check instead. Tell the owner
  once what the tab should show and the cure (stop that OpenCode and open the folder with OpenCode's background
  service, which a TUI uses by default), then go on.

## 3. Self-Compaction

Solaris self-compaction is off for a project with this plugin attached (copied or linked): autocode's compaction is
the only one. Outside a project (a session at the Solaris root or in a task), turn it off for the session when
autocode compacts it (`status` shows a compaction threshold, not `off`): `uv run --directory <solaris root> -m
solaris.tools.session_compact set-limit off`. Never run `session_compact request`, and ignore any
`self-compaction: ... DUE` line.

## 4. Read

Read the read-first set by hand (no hook loads it here): `uv run --directory <solaris root> -m
solaris.tools.read_first --check --pack <pack>` names the files; read each, then `<pack>/.memory/context.md`, the
newest `handover-*.md` and the tail of `directions.md`.

## 5. Wakes and the Guard

`autocode_wake action=list`. With the wakes already there, check that `guard` has its check (step 4 below) and go
on. With none (the first start):

1. **Look for a second controller:** `uv run --directory <solaris root> -m solaris.tools.session_clock --dir
   <project> --status`. A clock armed and alive under another live session means another session runs this
   project: stop and tell the owner.
2. **Convert the schedule.** `python3 <pack>/plugins/autocode/tools/wakes.py --dir . --zone <zone>` prints one wake
   per event of `<pack>/.memory/schedule.json`: its name, its trigger and its next time. `<zone>` is the server's
   zone, which `status` names, or the owner's.
   - Recurring events keep the instants they fire at today, as UTC crons: right for an event tied to a clock
     elsewhere.
   - An event tied to people's local time keeps that local time when the clocks change: name such events with
     `--local <name prefix>`, or give each a `"zone"` in the schedule. The last line of the output names the
     recurring events that were written with a local offset and came out in UTC: decide each.
   - `--skip-prefix <prefix>` leaves out the events another agent owns.
   - A `NOTE` says what to know of a line: the event got another wake name; its period is no cron; its start is
     still ahead, while its cron runs before that too; it has a zone and no `at`.
   - A schedule that `session_clock` would refuse is refused here too (exit 2, with the event's name): fix the
     event and run it again.
3. **Set each wake** with `autocode_wake action=set`: the name and trigger of its line, and its text from
   `--json`. A pass that mostly finds nothing to do gets its script as the wake's check (the rule's Clock).
4. **Set the guard** (the rule's Guard): `autocode_wake action=set name=guard trigger="idle 10m repeat"
   check="python3 <pack>/plugins/autocode/tools/guard.py check"`, with the rule's text. Add `--steps <file>` to the
   check for each file whose unticked `- [ ]` lines are the project's open steps: the guard tells only what is
   marked there or with `guard.py on`. Then run `python3 <pack>/plugins/autocode/tools/guard.py status` once: it
   shows the open work, the jobs and the last check, and makes the guard's state folder. A session
   outside a project (at the Solaris root or in a task) has no pack to keep the guard's state in: it names a
   folder of its own with `--state <folder>`, in the check and in every call (a check whose `--state` folder
   does not exist wakes the session and says so, since a mistyped one would hide every mark).
5. **Stop the old clock:** `uv run --directory <solaris root> -m solaris.tools.session_clock --dir <project>
   --stop`.

## 6. Where the Session Comes Back by Itself

Tell the owner at the first start which of the two holds (`status` names the session's machine and the server; ask
when it is not plain):

- **On the autocode server's own machine, in OpenCode's background service:** after a restart of OpenCode, a
  reboot or a power cut, autocode starts OpenCode again when something waits for the session (a wake that fell
  due, a message, a turn that was cut), and the turn goes on. Nobody has to open OpenCode. This needs autocode
  1.1.0-rc2 or later.
- **On any other machine, or in another kind of OpenCode:** autocode cannot start it there. After a reboot the
  owner opens OpenCode. And OpenCode unloads a folder after an hour without use, where a wake that its check
  skips is not use: until the folder is opened again, the session's wakes and messages wait. To stay reachable
  there, the session needs a wake of 30 minutes or less **without a check**, and each of its runs is a turn: say
  what that costs, and let the owner choose.
