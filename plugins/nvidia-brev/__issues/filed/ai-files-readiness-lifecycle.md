# ai-files: instance readiness lifecycle not explained

**Target:** PR to `brevdev/brev-cli` `.agents/skills/brev-cli/` (SKILL.md "Creating Instances" /
reference/commands.md)

## Problem

Agents need three distinct readiness facts that the docs omit:

1. `brev create` waits for ready but its poll can end with a spurious-looking `ErrorForbidden`
   even though the instance provisions fine - an agent treating that as failure will wrongly
   retry/abort. (Observed on launchpad 2x H100.)
2. STATUS `RUNNING` != usable: the SHELL column must be `READY` before `exec`/`shell` work.
3. Even after SHELL `READY`, sshd may refuse connections for another ~1-2 minutes
   (`Connection reset`, `kex_exchange_identification`) - first contact needs a retry loop.
   (Observed on verda B300 and massedcompute L40S.)

## Suggested fix

A short "instance lifecycle" note in SKILL.md: create -> BUILDING (unbilled) -> RUNNING ->
SHELL READY -> sshd actually accepting (retry-loop the first connection); treat post-create
ErrorForbidden as benign and watch `brev ls` instead.

**Filed:** https://github.com/brevdev/brev-cli/pull/428 (2026-07-26)
