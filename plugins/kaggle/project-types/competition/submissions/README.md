# Submissions <!-- omit in toc -->

- [Folders](#folders)
- [Each Folder's README](#each-folders-readme)
- [The Submission Record](#the-submission-record)
- [Submitting](#submitting)

`PLAN.md` beside this file is the master's daily plan: the day's slots before its first submission and tomorrow's
before the reset, each with its purpose, a prediction with an interval, and the rule its score triggers. Every
candidate built for a slot gets a folder here.

## Folders

- **One folder per candidate,** `<NNN>-<mmdd>-<slug>/`: `<NNN>` is the next free three-digit number, counted from
  `001` and never reused; `<mmdd>` is the day the folder was made, in the owner's timezone; `<slug>` names the change
  in a few kebab-case words (for example `002-0915-public-fork`).
- **Tracked:** the folder's README, its submission record, and the submitted file or the kernel with the code that
  made it (`kernel-metadata.json` keeps its pinned `docker_image`). Large outputs stay in `__out/`, data in `__data/`.
- **Kept:** every folder, scored or not. The records are the evidence for the final picks and for a write-up.

## Each Folder's README

Written before the submit and finished when the score lands, with these sections:

```text
# <NNN>: <the change in a few words>
## What It Verifies   the question this slot answers, and the scored submission it pairs with
## Approach           what was built and from what (a fork's lineage, credited); CLEAN or RESTRICTED, and what
                      is restricted
## Why                the evidence behind it and the expected gain
## Checks             the review, the commit run and its log (the change's "ON" marker), the runtime against the
                      limit, and the pre-submit check's decision: keep, switch or delay, and why
## Prediction         the expected public score with an interval, and the rule its score triggers
## Result             the public score, our place of the teams, the paired read against its base, what it taught
```

When a score lands, update this README, `PLAN.md`, `research/ideas.md` and the status page in the same turn.

## The Submission Record

`submission.json` in the folder is the record the kaggle plugin's gated submit reads; the tool adds an `attempts`
list and leaves the other fields alone:

```json
{"competition": "{{ANSWER_COMPETITION}}", "file": "submission.csv", "message": "<NNN>: one line",
 "kernel": "<owner>/<kernel>", "version": 4}
```

A code competition names the saved kernel version (`kernel`, and `version`: the number `kernels push` printed) and
the output file it wrote; a file competition names the local `file` beside the record. The run lock
`submission.json.lock` stays beside it, out of git.

## Submitting

From the project root, with `<tools>` the kaggle plugin's tools (`{{PACK}}/plugins/kaggle/tools`):

1. **Kernel run:** take a sharing lease, `python3 <tools>/kaggle_share.py acquire --path <kernel dir>`; push through
   the gateway, a Kaggle write, on the owner's approval or under the standing grant
   (`python3 <tools>/kaggle.py kernels push -p <kernel dir>`); release the lease when the run ends
   (`release --path <kernel dir>`), and at once when the push fails or is declined.
2. **Pre-submit check,** within the half hour before the submit, away from other Kaggle reads:
   `python3 <tools>/kaggle_presubmit.py {{ANSWER_COMPETITION}}`; decide keep, switch or delay; then the same command
   with `--ack`.
3. **Gated submit:** `python3 <tools>/kaggle_submit.py submissions/<folder>/submission.json` runs every check and
   prints the exact command; `--go --review "<one line>"` submits once, on the owner's approval or under the standing
   grant (with a one-line heads-up).
4. **An unclear result** (exit 4) is never resubmitted blindly: read `competitions submissions` and
   `competitions submission-limits` through the gateway first.
