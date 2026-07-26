# ai-files: exec examples mislead agents into hanging on long jobs

**Target:** PR to `brevdev/brev-cli` `.agents/skills/brev-cli/` (SKILL.md, reference/commands.md,
prompts/ml-training.md)

## Problem

The agent-facing docs repeatedly show `brev exec my-instance "python train.py"` (SKILL.md:147 and
similar in reference/commands.md, prompts/ml-training.md). `brev exec` holds the session until the
command exits - including for `nohup ... &` children - so an agent following the docs for a
multi-hour training run blocks (or times out and loses the session) instead of running it detached.

## Repro

`brev exec <name> "nohup sleep 600 & echo launched"` - the exec call does not return at "launched";
it holds until the child exits.

## Suggested fix

Document the detach pattern for long jobs:

```sh
brev exec my-instance @launch.sh   # where launch.sh ends with:
# setsid nohup <job> > run.log 2>&1 < /dev/null & disown
```

plus a note that agents should poll a completion marker (e.g. `echo DONE >> ~/progress.log`)
instead of holding the exec session. (See also cli-exec-multiline-hang.md - currently even the
@file detach form breaks when the script contains heredocs.)

**Filed:** https://github.com/brevdev/brev-cli/pull/428 (2026-07-26)
