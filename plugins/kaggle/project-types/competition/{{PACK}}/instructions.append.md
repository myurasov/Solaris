## Competition Facts Sheet

The competition's rules and facts, read through the Kaggle gateway on day 0 (the pages, the data files, the
submission limits, the hosts' posts in the forum) and read again whenever the hosts post an update. Every cutoff
and plan derives from this sheet. Times are UTC with the owner's time beside them; raw copies of the pages are in
`__data/kaggle/{{ANSWER_COMPETITION}}/pages/`.

| Item | Value |
|---|---|
| Competition | `{{ANSWER_COMPETITION}}`, https://www.kaggle.com/competitions/{{ANSWER_COMPETITION}} (day 0: its name, host and start) |
| Team | {{ANSWER_TEAM}} (the name on the leaderboard) |
| Kind | (day 0: simulation and bot ladders, tabular and time series, agent and LLM scorers, retrieval and ranking, or none of these; then read its kind skill, `{{PACK}}/plugins/kaggle/kaggle-kind-*.skill.md`) |
| Task | (day 0: what is predicted or built, and from what) |
| Metric | (day 0: its definition, and whether higher or lower is better) |
| Format | (day 0: CSV or code competition; what a submission is: a file, a notebook's output, a package) |
| Runtime and hardware | (day 0: the notebook time limit, CPU or GPU, memory) |
| Internet | (day 0: on or off in the scoring run) |
| External data and models | (day 0: what is allowed and under which licences; the hosts' rulings quoted exactly, with their topic ids) |
| Slots | (day 0: submissions per day, the reset time, the number of final picks) |
| Teams | (day 0: the size limit and the merge deadline; after a merge, every member's submissions count against one daily limit) |
| Public and private split | (day 0: the public board's share of the test, and which board decides) |
| Data | (day 0: files and sizes, the train and test makeup, whether a visible test is a copy of training rows) |
| Deadlines | (day 0: entry, team merger, final submission) |
| Board at day 0 | (day 0: teams, the top score, the medal lines with the teams at or above each, the host baseline) |
| Scorer version | (when the hosts publish a new scorer: whether it counts as live before they confirm it is the owner's call) |
| Prizes and winners' duties | (day 0: the licence, code and write-up requirements) |

## Owner's Standing Grant

The owner's answer at creation ({{DATE}}) to running unattended: **{{ANSWER_UNATTENDED}}**.

- On yes, the master runs the competition unattended: it decides on its best judgement and reports, gives a one-line
  heads-up for each outward action, and never depends on the owner's help or logins. Covered: submissions within the
  day's limit through the gated submit; private Kaggle notebooks and private datasets a submission needs; jobs on
  this project's own hosts; paid compute within the daily limits in `{{PACK}}/.memory/config.json`; commits, version
  bumps and pushes to this project's own private remote.
- Reserved for the owner in any case: credentials, the submission limits, budgets, anything public (forum posts,
  public notebooks or datasets, write-ups), rules acceptance, phone verification, teams and merges, the final picks,
  framework or plugin edits, and the guest rules on shared hosts.

## Competing Process

How this project competes from day to day; the playbook core (`{{PACK}}/plugins/kaggle/how-to-kaggle.skill.md`) holds
the rules and their evidence. Kaggle commands run from the project root; `<tools>` below is
`{{PACK}}/plugins/kaggle/tools` (for a linked plugin, the Solaris checkout's `plugins/kaggle/shared/tools`).

- **Kaggle:** every command through the gateway, `python3 <tools>/kaggle.py <args>`, naming the competition,
  `{{ANSWER_COMPETITION}}`, on each.
- **Wakes:** the master's clock is its autocode wakes ([Autocode](#autocode)), set at its first start from
  `{{PACK}}/.memory/schedule.json`, which then stays a frozen record (`autocode_wake action=list` is the truth):
  `hourly` with its check, `kaggle-reset` and `next-day-plan` in UTC on the Kaggle reset, `plan-review` in the
  owner's zone once cross-family review is set up, one-off wakes for the deadlines, and `guard`, the idle guard.
  The `plan-review` wake is only the master's turn to write the day's review package and `READY` before the
  review jobs' hour: the review itself is the `review-<name>` jobs. A new event is a new wake. No session clock
  runs, and no turn is owed by the hour.
- **Hard-rules card:** `{{PACK}}/card.md` (at most about 3 KB) holds the rules that must survive a compaction; the
  master reads it again after each one (the autocode Start skill's Read step). A new owner direction of that
  weight joins it in the same turn.
- **Hourly pass:** the check of the `hourly` wake is `sh <tools>/kaggle_hourly_check.sh {{ANSWER_COMPETITION}}`,
  with a check time limit of 5 minutes. It runs the pass (`kaggle_hourly.py {{ANSWER_COMPETITION}} --check`) and
  lets only a verified quiet pass skip the turn: a quiet hour ends there and costs no turn. When it wakes the
  master: act on each reason from the receipt it names and make no second pass, run this project's own host and
  job checks beside it, then close the hour with `python3 <tools>/kaggle_hourly.py {{ANSWER_COMPETITION}} --close`.
  The close prints what the check found and held, with the STATUS block for the status message, rebuilds the
  status page and marks the hour handled: do not rebuild the page by hand at each pass. A wake that is not closed
  wakes again the next hour. Then rewrite the snapshot in `{{PACK}}/.memory/context.md`. A wake fired by hand
  skips its check: run the check command once.
- **Status page:** the master edits `reports/status.json`; `python3 <tools>/kaggle_status.py` builds
  `reports/status.pdf` and raises the rev. The JSON, the PDF and its HTML stay out of git: they carry machines and
  spend. The hour-close rebuilds the page at each woken hour; rebuild it by hand in the same turn as any plan
  change, submission, score, verdict or launch.
- **Slots:** `submissions/PLAN.md` holds the day's plan before its first submission and tomorrow's before the reset,
  every slot with a purpose, a prediction with an interval and the rule its score triggers. Each candidate gets a
  folder in `submissions/` (its README there); each submit runs the pre-submit check
  (`python3 <tools>/kaggle_presubmit.py {{ANSWER_COMPETITION}}`, then `--ack`) and the gated submit
  (`python3 <tools>/kaggle_submit.py <record.json>`); each kernel push takes a sharing lease first.
- **Research:** `research/ideas.md` is the ranked backlog and `research/discussions.md` the forum log (topic ids and
  actions); the top-teams research track is always running or queued.
- **Spend:** autocode counts the session's own AI spend, its subagents and jobs included; each cost it does not
  count is one line in `{{PACK}}/.memory/spend.jsonl`
  (`{"day": "YYYY-MM-DD", "category": "cloud GPU", "usd": 1.5, "what": "<what it paid for>"}`); the daily limits,
  when the owner set them, are `ai.daily_budget_usd` and `brev.daily_limit_usd` in `{{PACK}}/.memory/config.json`,
  each checked on its own at the hourly pass.
- **Compute** (the owner's answer at creation): {{ANSWER_COMPUTE}}.
- **Cross-family review** (the owner's answer at creation: {{ANSWER_CROSS_REVIEW}}): once set up, models from other
  vendors review the daily plan and each pre-submit pick, read-only, on copies outside the repo, through
  `python3 <tools>/kaggle_review.py` on the master session's own hub key, which is never printed. The daily plan
  review is a set of autocode jobs, one per reviewer (`review-<name>`), each with its own cron trigger, kept as
  files in `{{PACK}}/jobs/` with `python3 <tools>/kaggle_jobs.py` (the plugin's `kaggle-reviews` skill).
- **Direction IDs:** this project numbers its own directions `L01`, `L02`, ... (the directions log, the card, this
  file). The `ODnn` numbers belong to the kaggle plugin's registry: cite them, never mint one.
- **Git:** one repo at the project root, on `main`. Commit each related group of changes; once the owner has approved
  the private remote, push each group with the README's Status and Recent Changes refreshed and the version raised
  (`.version`: MINOR for a milestone or a new capability, PATCH for fixes, records and docs; the group's last commit
  plus an annotated tag `vX.Y.Z`). Check the author, committer and tagger identity before every commit, tag and push.

## Autocode

The master runs only as an autocode agent, an OpenCode 2 session. The reference is the autocode plugin's rule,
`{{PACK}}/plugins/autocode/autocode.rule.md`, and a new session starts with its Start skill,
`{{PACK}}/plugins/autocode/autocode-start.skill.md` (for a linked plugin, both are in the Solaris checkout's
`plugins/autocode/shared`). Only what is specific to this project is kept here.

- **Agent ID:** (set at the first start; the suggested form is `kaggle:<short name>:master`).
- **Start prompt:** (recorded at the first start).
- **Wakes and checks of this project:** (the wakes set at the first start, each with its trigger and its check).
