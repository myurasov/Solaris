---
name: kaggle-scout
triggers: ["open competitions", "which competitions", "other competitions", "next competition", "new competition", "scout competitions", "competitions worth our time", "kaggle_scout"]
summary: Scouting for the next Kaggle competition - kaggle_scout.py lists the competitions that are open (ref, days left, category, reward, teams, entered) and gives a first look at one (the top of its board, its files), read-only, from a project, a task folder or the framework root. Covers when to run it, how to read the table, the judgement the tool cannot make (fit for an agent team, code-only and notebook-only limits, daily submission limits, prize rules), and what joining takes - the owner accepts the rules, and a new competition is a new project of type kaggle:competition.
---
_Rev. 1_

# Skill: kaggle-scout - Which Competition Next <!-- omit in toc -->

- [When to Use](#when-to-use)
- [Commands](#commands)
- [Reading the Table](#reading-the-table)
- [What the Tool Cannot Judge](#what-the-tool-cannot-judge)
- [The Note for the Owner](#the-note-for-the-owner)
- [Joining](#joining)

## When to Use

When the owner asks what else is worth the team's time, when a competition nears its end and the team will be
free, and when a team has stalled and its compute could earn more elsewhere. A scout is a read and a note,
never a start: it changes nothing on Kaggle and joins nothing.

## Commands

| Context | Command |
|---|---|
| Project, plugin copied | `python3 <pack>/plugins/kaggle/tools/kaggle_scout.py ...` |
| Project linked, ad-hoc task, framework root | `python3 <solaris>/plugins/kaggle/shared/tools/kaggle_scout.py ...` |

| Call | Does |
|---|---|
| `kaggle_scout.py` | Every open competition: ref, days left, category, reward, teams, entered. |
| `kaggle_scout.py --min-days 21` | Only those with at least that many whole days left. |
| `kaggle_scout.py --detail <slug> ...` | Also, for each one named: the top 5 of its public board and its data files. |
| `kaggle_scout.py --detail <slug> ... --no-list` | The details alone. |
| `... --json` | One JSON document instead of the table. |

It reads through the gateway, as the `kaggle-cli` skill describes, so it runs wherever the gateway does: in a
project, in an ad-hoc task folder and at the framework root. It writes nothing: no file, no activity stamp, no
saved leaderboard. A list is eight reads, each detail two more; run it when the question comes up, not on a
schedule. Exit codes: 0 read (a failed read is named in a note), 1 no read worked, 2 bad usage.

## Reading the Table

- **ref** is the slug: the name every other tool takes.
- **days** are the whole days to the deadline. The entry deadline and the team-merger deadline come earlier,
  often by a week: they are on the competition's page, not in the list.
- **category** tells what is at stake. Featured and Research competitions carry prizes, and most carry
  medals. Playground competitions carry small prizes or none. Getting Started and Community ones never end
  and carry nothing: their far-off deadlines put them at the bottom of the table.
- **reward** is Kaggle's own text: an amount, "Swag", "Knowledge".
- **teams** is the size of the field. A small field late in a competition is either hard to enter or
  overlooked; the page says which.
- **entered** is yes where the account has joined: the team's own competitions, and old ones never left.
- **--detail, board:** the top scores show the metric's direction and scale, and how tight the top is. Many
  teams at one score mean a public notebook everyone copied.
- **--detail, files:** the size and kind of the data say what compute the work takes. "not readable" on the
  files means the account has not accepted the rules yet.
- A competition missing from the table can still be read with `--detail <slug>`: the list is built from the
  first 200 rows of four sort orders, and Kaggle lists invite-only competitions to nobody.

## What the Tool Cannot Judge

The table says what is open, not what fits. Read each candidate's Overview, Evaluation, Rules and Data pages
before it goes into a note (the gateway's `competitions pages <slug> list <slug> --content`, as the `kaggle-cli`
skill gives it, or the website), and judge:

- **How a score is made.** A score that a checker computes beats a writeup that people judge: an agent team
  learns from every submission when a metric answers within hours, and learns nothing from a jury that answers
  once, after the deadline. Rank exact, verifiable scores first; hackathons and analytics writeups last.
- **How a submission is made.** A code competition takes a notebook that Kaggle reruns, within a time limit,
  often without internet and on fixed hardware. A notebook-only limit rules out anything the team cannot fit
  into that run. A competition scored on the host's own platform, or one with only a few scored submissions in
  all, leaves little to iterate on.
- **Daily submission limits.** They set how many reads a day the team gets, and with them how fast it can
  learn. One or two a day call for a strong local holdout; a split between a public and a private board calls
  for care with the final picks; no split at all means the public score is the result.
- **Prize rules.** Who may win (countries, employers, age), what a winner must publish and under which
  licence, which models and data are allowed, whether automated or AI-written entries are welcome or barred,
  how many places are paid, whether there are medals. A prize the team cannot take is not a prize.
- **The field and the time.** Days left against what a first honest baseline takes; how far the public
  notebooks already are; whether the top has stood still for weeks.
- **The cost.** Compute, data size, paid models, and the share of the Kaggle account's GPU hours and
  sessions it would take from the running projects (the `kaggle-sharing` skill).

## The Note for the Owner

Write the scout as a short research note in the task or project (`research/<MMDD>-competitions.md`): one
table with the columns competition, prize, days left, teams, what it asks, fit; then a few lines on the top
candidates and what each would take. Say which sources were read and when. The table's figures go stale
within days; date them.

## Joining

- **Joining means accepting the competition's rules on kaggle.com, on the owner's account. That is the
  owner's step,** as is phone verification and any team change. An agent never accepts rules, and never works
  around a refusal that says they are not accepted. Ask with the competition's URL.
- **A new competition is a new project** of type `kaggle:competition`, made with `create-project`: its own
  repository, facts sheet, master and schedule. It is never a side track inside another competition's
  project.
- Until the owner decides, a scout stays a note. No data is downloaded into a project, no kernel is pushed
  and nothing is submitted for a competition the owner has not chosen.
