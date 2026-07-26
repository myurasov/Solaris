_Rev. 3_

---
name: brev-setup
triggers:
  - "set up brev" / "install brev" / "brev setup"
  - "log in to brev" / "brev login" / "register on brev"
summary: Install the Brev CLI, guide the user through account registration and login
  (browser flow - never secrets in chat), select the org, and verify access. Run this
  before the first brev-run in any environment.
---

# Skill: brev-setup - CLI installation + account onboarding <!-- omit in toc -->

Get from "nothing installed" to "authenticated `brev` CLI ready for `brev-run`". Idempotent:
every step checks state first and skips what is already done.

## 1. CLI installed?

1. `which brev && brev --version` - if present, skip to step 2.
2. Install:
   - macOS: `brew install brevdev/homebrew-brev/brev`
   - Linux (or no brew): `sudo curl -fsSL https://raw.githubusercontent.com/brevdev/brev-cli/main/bin/install-latest.sh | bash`
   - **Gotcha:** the installer script queries the GitHub API and fails on rate limits from
     shared/datacenter IPs ("try turning off VPN / set GITHUB_TOKEN"). Fallback: resolve the
     latest release asset from any authenticated machine
     (`gh api repos/brevdev/brev-cli/releases/latest`), then on the target
     `curl -fsSL -o /tmp/brev.tgz <asset-url> && tar -xzf /tmp/brev.tgz -C /tmp && sudo mv /tmp/brev /usr/local/bin/`.
   - Details and alternatives: the plugin's `brev-cli/INSTALLATION.md` (pristine upstream
     mirror; if this install is a copy-mode ai-pack without the mirror, the upstream lives at
     `github.com/brevdev/brev-cli` under `.agents/skills/brev-cli/`).
3. Verify `brev --version` succeeds.

## 2. Account + login

1. Probe auth state with `brev ls` (fast, harmless):
   - Succeeds -> already authenticated; go to step 3.
   - Prompts for login -> continue.
2. If the user has **no Brev account yet**, point them at https://console.brev.nvidia.com
   (sign in with an NVIDIA account; org + billing are configured there) and wait until they
   confirm the account exists. Registration cannot be done for them.
3. Run `printf '\n' | brev login` (accepts the default email prompt). The CLI prints a
   device-auth / browser URL: **surface that URL to the user and ask them to complete the
   login in their browser.** Never ask for passwords, tokens, or one-time codes in chat;
   never paste or store credentials in the repo.
4. When the CLI reports success, re-verify with `brev ls`.

## 3. Organization

1. `brev org ls` - show the orgs; confirm with the user which one instances should bill to
   (the `brev_org` setup answer, if recorded, is the default).
2. If wrong: `brev org set <org>`.

## 4. Record + hand off

1. Note the account email + active org in the project's `ai/.memory/credentials.md`
   (gitignored) - account identifiers only, never secrets.
2. Ensure `ai/.memory/brev-costs.md` exists (template in `brev-run.skill.md`); create it
   empty-tabled if missing.
3. Done - `brev-run` takes it from here.
