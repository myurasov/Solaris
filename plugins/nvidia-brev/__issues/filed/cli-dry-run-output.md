# cli: brev create --dry-run output is uninformative

**Target:** minor bug report to `brevdev/brev-cli`

## Problem

`brev create <name> --type massedcompute_L40S --dry-run` prints only:

```
massedcompute_L40S
```

No name, provider, $/h, disk, region, or "would create" summary - so the flag adds nothing over
what the user already typed, and an agent cannot use it to confirm cost before committing
(which is exactly what dry-run is for; the skill docs themselves say "Show instance cost/type
before creating").

## Suggested fix

Print the resolved creation plan: name, type, provider, GPU config, disk, $/h, org/billing target.

**Filed:** https://github.com/brevdev/brev-cli/issues/431 (2026-07-26)
