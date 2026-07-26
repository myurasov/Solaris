# platform: shared proxy endpoint drops/resets ssh sessions

**Target:** Brev platform feedback

## Observed (2026-07-25)

The shared proxy (`global.prd.ga.run.brev.nvidia.com`, used by `~/.brev/ssh_config` entries and
`brev exec`/`shell`) intermittently fails:

- `Connection reset by peer` / `kex_exchange_identification: read: Connection reset` on fresh
  connections (verda B300, massedcompute L40S), including minutes after SHELL READY.
- `Connection closed by <proxy-ip>` mid-handshake under otherwise normal load.

Direct connections to the instance's public IP (from `curl -s ifconfig.me` on the instance, with
`~/.brev/brev.pem`) are stable throughout the same windows.

## Impact

Automation (agents polling logs, shipping payloads) sees spurious exit-255s and must implement
retry loops; heavy transfers through the proxy are unreliable.

## Suggested fix

Expose the direct endpoint (where the provider has one) in `brev ls --format json` /
`brev refresh` ssh_config output as a first-class alternative, and/or raise proxy connection
limits per instance.
