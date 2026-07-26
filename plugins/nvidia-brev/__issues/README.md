# Issues Found While Field-Testing Brev (2026-07-25)

Issues discovered developing the nvidia-brev plugin, on real runs (2x H100 launchpad, B300
verda, T4 GCP, L40S massedcompute). `filed/` holds items already submitted upstream (as
`myurasov-nv`); the root holds items awaiting a channel.

## Filed upstream

| File | Upstream | One-liner |
|---|---|---|
| [filed/ai-files-exec-long-jobs.md](filed/ai-files-exec-long-jobs.md) | [PR #428](https://github.com/brevdev/brev-cli/pull/428) | Docs tell agents `brev exec "python train.py"` - hangs for long jobs; no detach pattern |
| [filed/ai-files-readiness-lifecycle.md](filed/ai-files-readiness-lifecycle.md) | [PR #428](https://github.com/brevdev/brev-cli/pull/428) | RUNNING vs SHELL READY vs sshd-accepting not explained; benign ErrorForbidden |
| [filed/ai-files-billing-model.md](filed/ai-files-billing-model.md) | [PR #428](https://github.com/brevdev/brev-cli/pull/428) | Billing model absent (per-second, RUNNING-only, stopped accrues storage) |
| [filed/cli-exec-multiline-hang.md](filed/cli-exec-multiline-hang.md) | [#429](https://github.com/brevdev/brev-cli/issues/429) | `brev exec` hangs, never executes multi-line/heredoc payloads |
| [filed/cli-installer-rate-limit.md](filed/cli-installer-rate-limit.md) | [#430](https://github.com/brevdev/brev-cli/issues/430) | install-latest.sh fails on GitHub API rate limits |
| [filed/cli-dry-run-output.md](filed/cli-dry-run-output.md) | [#431](https://github.com/brevdev/brev-cli/issues/431) | `brev create --dry-run` prints only the type name |
| [filed/cli-silent-create-failure.md](filed/cli-silent-create-failure.md) | [#432](https://github.com/brevdev/brev-cli/issues/432) | `brev create` fails silently without a tty |

Round-3 additions: stop/start IP-change behavior folded into PR #428 (second commit).
The silent-create failure reproduced a second time on 2026-07-26 and is now filed as
[#432](https://github.com/brevdev/brev-cli/issues/432)
([filed/cli-silent-create-failure.md](filed/cli-silent-create-failure.md)).

## Awaiting a channel (Brev platform - not repo-addressable; route via NVIDIA-internal / Brev support)

| File | One-liner |
|---|---|
| [platform-spec-metadata.md](platform-spec-metadata.md) | Advertised boot time/disk wildly wrong for some providers |
| [platform-proxy-stability.md](platform-proxy-stability.md) | Shared proxy endpoint drops/resets ssh sessions under load |
| [platform-usage-lag-and-rates.md](platform-usage-lag-and-rates.md) | Usage feed lags hours; billed rate decomposition undocumented |
