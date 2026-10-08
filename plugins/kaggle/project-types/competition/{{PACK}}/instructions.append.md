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
- **Session clock:** the master keeps one wake clock running as a background command, at the Solaris root
  `uv run -m solaris.tools.session_clock --dir <project>` (`<project>`: this project's path from there), on
  `{{PACK}}/.memory/schedule.json` (the hourly pass, the daily reset, tomorrow's plan before the reset, the
  deadlines), and starts it again with the `--after` it printed.
- **Hourly pass:** `python3 <tools>/kaggle_hourly.py {{ANSWER_COMPETITION}}` (with `--vs <our best score>` once there
  is one); act on every FLAG, paste its STATUS block into the status message, run this project's own host and job
  checks beside it, rebuild the status page with `--keep-rev`, and rewrite the snapshot in
  `{{PACK}}/.memory/context.md`.
- **Status page:** the master edits `reports/status.json`; `python3 <tools>/kaggle_status.py` builds
  `reports/status.pdf` and raises the rev. The JSON, the PDF and its HTML stay out of git: they carry machines and
  spend. Rebuild the page in the same turn as any plan change, submission, score, verdict or launch.
- **Slots:** `submissions/PLAN.md` holds the day's plan before its first submission and tomorrow's before the reset,
  every slot with a purpose, a prediction with an interval and the rule its score triggers. Each candidate gets a
  folder in `submissions/` (its README there); each submit runs the pre-submit check
  (`python3 <tools>/kaggle_presubmit.py {{ANSWER_COMPETITION}}`, then `--ack`) and the gated submit
  (`python3 <tools>/kaggle_submit.py <record.json>`); each kernel push takes a sharing lease first.
- **Research:** `research/ideas.md` is the ranked backlog and `research/discussions.md` the forum log (topic ids and
  actions); the top-teams research track is always running or queued.
- **Spend:** each cost other than Claude Code is one line in `{{PACK}}/.memory/spend.jsonl`
  (`{"day": "YYYY-MM-DD", "category": "cloud GPU", "usd": 1.5, "what": "<what it paid for>"}`); the daily limits,
  when the owner set them, are `ai.daily_budget_usd` and `brev.daily_limit_usd` in `{{PACK}}/.memory/config.json`,
  each checked on its own at the hourly pass.
- **Compute** (the owner's answer at creation): {{ANSWER_COMPUTE}}.
- **Cross-family review** (the owner's answer at creation: {{ANSWER_CROSS_REVIEW}}): once set up, a model from another
  vendor reviews the daily plan and each pre-submit pick, read-only, on copies outside the repo, with this project's
  own key.
- **Git:** one repo at the project root, on `main`. Commit each related group of changes; once the owner has approved
  the private remote, push each group with the README's Status and Recent Changes refreshed and the version raised
  (`.version`: MINOR for a milestone or a new capability, PATCH for fixes, records and docs; the group's last commit
  plus an annotated tag `vX.Y.Z`). Check the author, committer and tagger identity before every commit, tag and push.
