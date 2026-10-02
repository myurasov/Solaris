---
name: handover
triggers: ["handover", "stop for handover", "pause for handover", "resume after handover"]
summary: Pause {{NAME}} or hand it to another session or machine without losing work - save context, write a dated handover note, commit, leave remote jobs running - and resume from that note after re-checking hosts, leases and jobs.
---
_Rev. 2_

# Skill: handover - Pause, Hand Over, Resume <!-- omit in toc -->

1. [Pause Rules](#1-pause-rules)
2. [Before Stopping](#2-before-stopping)
3. [Resume](#3-resume)

Run when the owner pauses the session, stops it so another harness or machine can take over, or brings it
back. Everything the next session needs goes to disk: what dies with a session (the conversation,
subagents, schedules, clocks, background watchers) is written down so it can be recreated, and remote jobs
keep running. Project specifics (which hosts, leases and schedules to check) live in
`{{PACK}}/instructions.md` and `{{PACK}}/.memory/resources.md`.

## 1. Pause Rules

- A pause holds until the owner explicitly resumes. Answering a question during the pause is not a resume,
  and work that "does not need X" is still work: start nothing, and when asked, say what is still running.
- One controller at a time: the old session stops before a new one acts, and the new one confirms that
  before it resumes.

## 2. Before Stopping

1. **Stop starting.** Launch nothing new; stop schedules and periodic checks first, so nothing wakes the
   session mid-pause. Let running steps reach a safe point, or note exactly where each one stopped.
2. **Save `{{PACK}}/.memory/context.md`** (a save point; rewrite it in place): the goals and current plan;
   jobs in flight, each with its host, tmux session, log, done marker and next step; unfinished subagents,
   each with its brief file and what it has done so far; pending decisions and open questions for the
   owner; the next actions, in order.
3. **Write a dated handover note**, `{{PACK}}/.memory/handover-<YYYY-MM-DD-HHMM>.md` (time read from a
   clock): when and why the session stopped, who resumes it (this harness later, or another harness or
   machine), what still runs where, what the next session must recreate (subagents, schedules, clocks,
   periodic checks), and a short resume prompt the owner can paste. It names hosts, so it stays in the
   private `.memory/` and never goes into git.
4. **Commit** the work in progress per the commit policy, so nothing lives only in the working tree. Push
   only where pushing is already allowed (an Autonomy Grant in `{{PACK}}/spec.md`, or the owner's yes); a
   move to another machine needs it.
5. **Leave remote jobs running**: they outlive the session, and step 2 lists them.
6. **Paid instances** that would keep billing through a long pause with no job on them: stop (not delete)
   those you will reuse within about a day, per the owner's rule, and add them to the handover note; list
   the rest, with what they cost, and ask the owner what to do with them.
7. **Report** in one line: paused at what time, what is still running, and the phrase that resumes. Log the
   turn in this machine's file in `{{PACK}}/.memory/interactions/` (see `engineer.agent.md`, Memory), so a
   session on another machine can tell when this one stopped.

## 3. Resume

Only on the owner's explicit word.

1. **Read** the newest `{{PACK}}/.memory/handover-*.md`, then `context.md` (and `{{PACK}}/directions.md`
   when the project keeps one).
2. **Confirm sole control:** the old session has stopped, as its note says. On another machine, work from
   that machine's own clone, brought up to date with what was pushed, and copy across what git does not
   carry (at least the private `{{PACK}}/.memory/`).
3. **Re-verify external state** before acting on the note: each host reachable; leases and paid instances
   still there, with their end times; each listed job's done marker, log and tmux session; `git status`.
   Note what changed during the pause.
4. **Relaunch** each unfinished subagent from its brief, saying what is already done; restart the clocks
   and periodic checks the note lists, and start each instance it lists as stopped when its work resumes.
5. **Record:** add a "resumed at <time>" line to the handover note, refresh `context.md`, tell the owner in
   one line what resumed and what changed, and log the turn.
