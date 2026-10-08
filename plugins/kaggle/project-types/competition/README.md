# {{NAME}} <!-- omit in toc -->

- [Status](#status)
- [Recent Changes](#recent-changes)
- [The Competition](#the-competition)
- [How It Runs](#how-it-runs)
- [Owner Touchpoints](#owner-touchpoints)
- [Layout](#layout)

{{DESCRIPTION}}

An autonomous agent team competes in the Kaggle competition
[`{{ANSWER_COMPETITION}}`](https://www.kaggle.com/competitions/{{ANSWER_COMPETITION}}): it reads the competition,
plans, builds and checks candidates, submits within the daily limit and learns from each score. The owner handles
only the steps Kaggle reserves for a person and reads one status page.

## Status

| Item | State |
|---|---|
| **Version** | 0.1.0 (`.version`; every push raises it, and release commits are tagged `vX.Y.Z`) |
| **Team** | {{ANSWER_TEAM}} |
| **Phase** | Phase 0 (from {{DATE}}): the whole path in one day; then {{ANSWER_GOAL}} |
| **Best** | none yet |
| **Updated** | {{DATE}} |

The live state is the status page, `reports/status.pdf`, rebuilt at every hourly pass and plan change. It and its
source, `reports/status.json`, carry private operations data (machines, spend), so they stay out of git and reach
the owner through file sync.

## Recent Changes

Newest first, one line per push.

- **{{DATE}}:** the project was created with Solaris (type `{{TYPE}}`): the ai-pack with a master, a worker and a
  reviewer, the kaggle and reporting plugins, and the day-0 setup.

## The Competition

The short form of the facts sheet in `{{PACK}}/instructions.md` (Competition Facts Sheet), which is the source.

| Item | Details |
|---|---|
| **Task** | (day 0) |
| **Kind** | (day 0) |
| **Metric** | (day 0) |
| **Submission** | (day 0: CSV or code competition, runtime, internet) |
| **Limits** | (day 0: submissions per day, the reset time, final picks, deadlines) |
| **Leaderboard** | (day 0: teams, the top score, the medal lines, the host baseline) |

## How It Runs

- **The master** (`{{PACK}}/{{PRIMARY}}.agent.md`) plans, decides, reviews and submits; **workers**
  (`{{PACK}}/worker.agent.md`) each run one bounded job; a read-only **reviewer** (`{{PACK}}/reviewer.agent.md`)
  attacks every result and kernel before it counts.
- **Know-how:** the kaggle plugin's playbook and skills (`{{PACK}}/plugins/kaggle/`), plus this project's
  `{{PACK}}/instructions.md`: the facts sheet, the owner's standing grant and rules, the process, the lessons.
- **Rhythm:** an in-session clock wakes the master at least every 25 minutes; an hourly read-only pass (board,
  public notebooks, forum, submissions, compute, spend) flags what needs a decision; no daemon or outside watcher.
- **Kaggle:** every command goes through the plugin's pinned CLI gateway; each submission passes the pre-submit
  check and the gated submit.
- **Compute:** {{ANSWER_COMPUTE}}.

## Owner Touchpoints

- **Web-only steps** stay with the owner, asked with the exact URL: accepting the rules, phone verification, teams and
  merges, the final picks, anything public, and model licence consent.
- **The master session:** the owner starts it and resumes it after a pause, at the effort the owner chooses.
- **Standing grant and limits:** the grant is in `{{PACK}}/instructions.md` (Owner's Standing Grant; the answer at
  creation: {{ANSWER_UNATTENDED}}); the daily limits are in the private `{{PACK}}/.memory/config.json`. Any change is
  the owner's.

## Layout

| Path | Contents |
|---|---|
| `AGENTS.md`, `CLAUDE.md` | Entry point for agent harnesses; loads the ai-pack |
| `{{PACK}}/` | The ai-pack: the master persona and the worker and reviewer briefs, the spec, the shared instructions (with the facts sheet), rules, plugins; the private `{{PACK}}/.memory/` (not in git) holds the owner's directions, the session context, the limits, the schedule and the spend ledger |
| `source/` | Code workspace: experiments, evaluation, submission kernels |
| `submissions/` | `PLAN.md` (the day's slots) and one record folder per candidate (`submissions/README.md`) |
| `research/` | The ideas backlog (`ideas.md`), the discussions log, dated notes; the scripts and data behind them in `research/assets/` |
| `reports/` | The status page (`status.json` and the PDF and HTML built from it, all out of git) and other reports |
| `tools/` | Project tools, one folder each (`tools/README.md`) |
| `__data/`, `__out/` | Data, with the saved boards, notebook lists and forum under `__data/kaggle/`, and run outputs; local only, not in git |
| `.version` | The project's version (semver) |
