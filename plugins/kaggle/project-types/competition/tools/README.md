# Tools <!-- omit in toc -->

One folder per tool, `tools/<name>/`, with a README or a header in its main script, and one row in the table below.
Run each from the project root. Committed tools read only committed files and the project's data folders, never a
job's scratch output in `__out/` or a session's scratch folder: copy what they need into the project first.

| Folder | Tool | Docs |
|---|---|---|

The kaggle plugin's tools are not copied here: they run from `{{PACK}}/plugins/kaggle/tools/` (the gateway
`kaggle.py`, `kaggle_lb.py`, `kaggle_forum.py`, `kaggle_share.py`, `kaggle_presubmit.py`, `kaggle_submit.py`,
`kaggle_hourly.py` with the hourly wake's check `kaggle_hourly_check.sh`, `kaggle_watch.py`, `kaggle_review.py`,
`kaggle_jobs.py`, `kaggle_scout.py`, `kaggle_output.py` and `kaggle_status.py`). A project tool that builds on
one (an hourly check that adds this project's own host and job checks, say) calls it there and adds only what it
does not do.

The autocode plugin's tools run from `{{PACK}}/plugins/autocode/tools/`: `guard.py` (the idle guard's check, open
work marked for it, and watched background jobs) and `wakes.py` (the schedule as wakes).

Framework tools run at the Solaris root with `uv run -m solaris.tools.<name> --dir <project>`: `housekeeping`
(folder sizes and pruning by the project's rules) and `interactions` (the turn log).
