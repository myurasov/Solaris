# cli: install-latest.sh fails on GitHub API rate limits

**Target:** PR to `brevdev/brev-cli` (`bin/install-latest.sh`)

## Problem

The Linux installer resolves the latest release via the GitHub REST API unauthenticated. From
shared/datacenter egress IPs (where anonymous quota is usually exhausted) it fails with the
rate-limit message ("If you are using a VPN... set GITHUB_TOKEN") and installs nothing.
Observed on a datacenter Ubuntu host, 2026-07-25.

## Suggested fix

Fall back to the API-free redirect endpoint when the API call fails:
`https://github.com/brevdev/brev-cli/releases/latest/download/<asset>` (the `latest/download`
path needs no API call), keeping GITHUB_TOKEN as an optional accelerator rather than a
requirement.

## Workaround (for docs)

Resolve the asset URL on any authenticated machine (`gh api repos/brevdev/brev-cli/releases/latest`),
then `curl -fsSL -o /tmp/brev.tgz <url> && tar -xzf /tmp/brev.tgz -C /tmp && sudo mv /tmp/brev /usr/local/bin/`.

**Filed:** https://github.com/brevdev/brev-cli/issues/430 (2026-07-26)
