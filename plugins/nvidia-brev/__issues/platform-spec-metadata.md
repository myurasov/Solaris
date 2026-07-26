# platform: advertised boot time / disk in search can be wildly wrong

**Target:** Brev platform/catalog feedback

## Observed (2026-07-25)

- launchpad `dmz.h100x2.pcie`: advertised "1m boot / 56TB disk" - actual ~25 min to READY,
  1.8 TB disk.
- gcp `n1-standard-1:nvidia-tesla-t4:1`: advertised 10GB target disk - actual 125 GB root.
- massedcompute L40S: advertised 2m30s boot - actual ~4 min to SHELL READY (close, acceptable).

## Impact

Agents sizing disk >= 3x payload or picking providers by boot time make wrong choices from
catalog data alone; each instance needs `df -h` / `nvidia-smi` verification on arrival (which
costs paid minutes on expensive types).

## Suggested fix

Populate catalog boot/disk figures from measured provisioning telemetry rather than provider
spec sheets, or mark unverified fields as estimates.
