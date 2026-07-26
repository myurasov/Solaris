# ai-files: billing model absent from agent docs

**Target:** PR to `brevdev/brev-cli` `.agents/skills/brev-cli/` (SKILL.md "Cost Management",
prompts/cleanup.md)

## Problem

SKILL.md's cost guidance stops at "Show instance cost/type before creating" and cleanup.md lists
stopped instances as cleanup candidates without saying why it matters. Agents managing budgets
need the actual billing model:

- Billing is **per-second** and only while the instance is RUNNING - BUILDING/provisioning time
  is free and there is no hour round-up (observed across gcp, verda, massedcompute, launchpad).
- A **STOPPED instance still accrues storage charges indefinitely** (observed: a stopped L40S
  quietly accumulated $8.40 over ~25 days). "Stopped" is not "free" - delete when done.
- The usage/billing feed lags real time by hours - agents verifying spend right after teardown
  see partial numbers.

## Suggested fix

Add a "How billing works" paragraph with the three facts above to SKILL.md, and a warning line
in prompts/cleanup.md that stopped instances continue to cost money (storage) until deleted.

**Filed:** https://github.com/brevdev/brev-cli/pull/428 (2026-07-26)
