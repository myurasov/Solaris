# cli: backgrounded brev create can fail silently (WATCH - needs second repro)

**Target:** brev-cli bug report - NOT yet filed; single occurrence, weak attribution.

## Observed (2026-07-26)

`brev create nv-penske-plugin-test3 --type n1-standard-2:nvidia-tesla-t4:2
--startup-script @startup.sh --timeout 900`, launched non-interactively (no tty, output to
a file): printed nothing after ~15 min, and no instance ever appeared in `brev ls`. The
identical command re-run in the foreground created the instance normally.

## Status

Watch item. If a backgrounded/no-tty create fails silently again, file upstream with both
occurrences. Workaround already in `shared/brev-run.skill.md`: verify with `brev ls` after
every create rather than trusting the create call.
