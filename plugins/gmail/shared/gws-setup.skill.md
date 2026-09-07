---
name: gws-setup
triggers: ["set up gmail", "setup gmail", "gmail setup", "install gws", "set up gws", "gws setup", "gws login", "gws auth", "log in to gmail", "gmail login", "google workspace cli", "set up google workspace cli", "install google workspace cli"]
summary: Install gws (the Google Workspace CLI) on macOS or Linux, put an OAuth Desktop client in place, sign in with the Gmail scope (gws prints a URL, the owner completes consent in a browser; owner-confirmed export hand-off for headless hosts), verify, and record the account. Idempotent - run before the first gmail use on any machine, and again when auth expires.
---
_Rev. 4_

# Skill: gws-setup - Install and Sign In the Google Workspace CLI <!-- omit in toc -->

1. [CLI Installed?](#1-cli-installed)
2. [OAuth Client (One-Time per Google Cloud Project)](#2-oauth-client-one-time-per-google-cloud-project)
3. [Sign In With Gmail Scopes](#3-sign-in-with-gmail-scopes)
4. [Verify + Record](#4-verify--record)
- [Troubleshooting](#troubleshooting)

Get from "nothing installed" to "authenticated `gws` ready for [`gmail.skill.md`](gmail.skill.md)".
Idempotent: every step checks state first and skips what is already done. Only Gmail is set
up here today; the same CLI covers Drive, Calendar, Sheets, and the rest of Workspace, so a
later service needs only extra scopes at login (step 3) plus its own skill.

## 1. CLI Installed?

1. `which gws && gws --version` - prints `gws X.Y.Z` plus a "not an officially supported
   Google product" line. Present -> step 2. **Name collision:** a `gws` that talks about git
   repositories/workspaces is the unrelated `gws` git-workspaces tool (Homebrew formula
   `gws`); it conflicts with the Workspace CLI - remove it first.
2. Install, one channel per machine (all are the same prebuilt binary):
   - **macOS or Linux with Homebrew:** `brew install googleworkspace-cli` (homebrew-core
     formula, bottled for macOS and Linux; **not** `brew install gws`).
   - **Linux without Homebrew - prebuilt release binary** (upstream's recommended path):
     ```bash
     ARCH=$(uname -m); LIBC=gnu   # x86_64 or aarch64; LIBC=musl for Alpine or glibc < 2.39
     URL=$(curl -fsSL https://api.github.com/repos/googleworkspace/cli/releases/latest \
       | grep -o "https://[^\"]*google-workspace-cli-${ARCH}-unknown-linux-${LIBC}.tar.gz" | head -n1)
     curl -fsSL -o /tmp/gws.tgz "$URL" && curl -fsSL "$URL.sha256" | sed 's# .*##' > /tmp/gws.sha
     echo "$(cat /tmp/gws.sha)  /tmp/gws.tgz" | sha256sum -c
     mkdir -p ~/.local/bin && tar -xzf /tmp/gws.tgz -C /tmp ./gws && mv /tmp/gws ~/.local/bin/ && ~/.local/bin/gws --version
     ```
     If the unauthenticated API call answers 403 (GitHub rate limit, common from shared or
     corporate IPs), pin the version instead:
     `URL=https://github.com/googleworkspace/cli/releases/download/v<X.Y.Z>/google-workspace-cli-${ARCH}-unknown-linux-${LIBC}.tar.gz`
     (latest tag: `gh api repos/googleworkspace/cli/releases/latest --jq .tag_name` from any
     authenticated machine). The `gnu` build needs glibc 2.39+ (Ubuntu 24.04 and newer); on
     Ubuntu 22.04, Debian 12, RHEL 9, or Amazon Linux 2023 set `LIBC=musl`. The archive holds `gws` at
     its root (plus LICENSE/README/CHANGELOG). `~/.local/bin` joins `PATH` at the next login
     on Debian/Ubuntu - until then call `~/.local/bin/gws` by path; other distros: add it to
     the shell profile. On a **remote host Solaris manages**, use `~/.solaris/gws/bin/`
     instead (the remote footprint convention) and add it to `PATH` in the shell profile;
     uninstall = remove that directory and `~/.config/gws/`. Record host + path in
     `ai/.memory/resources.md`.
   - **npm (Node 18+ and `tar` on PATH, any OS):** `npm install -g @googleworkspace/cli`
     (downloads the same release binary; make sure npm's global bin is on `PATH`).
   - **From source:** `cargo install --git https://github.com/googleworkspace/cli --locked`
     (slow; only when no binary fits).
3. Verify `gws --version`. Upgrade later through the same channel (`brew upgrade
   googleworkspace-cli`, `npm update -g @googleworkspace/cli`, or re-download).

## 2. OAuth Client (One-Time per Google Cloud Project)

`gws auth status` prints JSON: `"client_config_exists": true` means a client is in place
(default path `~/.config/gws/client_secret.json`) -> step 3. Otherwise gws needs its **own**
OAuth client of type **Desktop app** from a Google Cloud project with the **Gmail API**
enabled. Never reuse another tool's client file (a Gmail MCP's, another machine's): each
tool gets its own client so revoking or deleting one never breaks the others - and a
`deleted_client` error at login means exactly that, the file points at a client that no
longer exists. Only the owner can create the client (it lives in their Google account), so
send them the steps below **verbatim in one message**, with `<PROJECT_ID>` filled in when
known, and wait:

1. **Project:** pick an existing Google Cloud project or create one at
   `https://console.cloud.google.com/projectcreate` (any name, e.g. `solaris`); note its
   project id - every URL below takes `?project=<PROJECT_ID>`.
2. **Enable the Gmail API:**
   `https://console.cloud.google.com/apis/library/gmail.googleapis.com?project=<PROJECT_ID>`
   -> **Enable**.
3. **Consent screen** (Google Auth Platform; older console: APIs & Services > OAuth consent
   screen). First time in a project:
   `https://console.cloud.google.com/auth/overview?project=<PROJECT_ID>` -> **Get started**:
   app name `gws`, support email = your address, audience: **Internal** when the project
   belongs to your Google Workspace organization and gws will act as an account of that org
   (no test users, no "unverified app" screen, no 7-day token expiry - field-verified); else
   **External**, then `https://console.cloud.google.com/auth/audience?project=<PROJECT_ID>`
   -> **Test users** > **Add users** > the Google account gws will act as (without it login
   fails with a generic "Access blocked"). Contact email, **Create**.
4. **Create the client:** `https://console.cloud.google.com/auth/clients?project=<PROJECT_ID>`
   (older console: APIs & Services > Credentials > Create credentials > OAuth client ID) ->
   **Create client** -> application type **Desktop app**, name `gws` -> **Create** ->
   **Download JSON** (`client_secret_<id>.json` lands in Downloads; re-downloadable from the
   client's page later).
5. **Install it** (the agent does this once told the download finished; take the newest
   file - Downloads often still holds older clients):
   `f=$(ls -t ~/Downloads/client_secret_*.json | head -n1) && mkdir -p ~/.config/gws && mv "$f" ~/.config/gws/client_secret.json && chmod 600 ~/.config/gws/client_secret.json`,
   then `gws auth status` shows `"client_config_exists": true`, the new client's id prefix
   in `config_client_id`, and the project id.

`gws auth setup` (needs `gcloud` logged in as the same Google account) can do steps 1-2 and
then prints the Console steps for 3-4 - it does **not** create the client or add Test users.
Skip its `--login` flag: it runs an unscoped login whose terminal picker preselects seven
Workspace services; do step 3 of this skill instead.

- The client JSON is a credential: never paste it into chat, commits, or shared files;
  reference it by path. Record the GCP project id in `ai/.memory/resources.md`.
- **Testing-mode expiry (External audience only):** an External app left in **Testing** gets
  refresh tokens that expire after 7 days, so `gws` needs step 3 again weekly. To stop that,
  publish the app (Audience > **Publish app** > In production) - the "Google hasn't verified
  this app" interstitial then stays (expected for a personal app; verification only matters
  when distributing to other users) but tokens no longer expire. Internal-audience apps have
  neither problem.

## 3. Sign In With Gmail Scopes

1. `gws auth status` - `"auth_method"` other than `"none"` means credentials exist -> step 4.
   `gws gmail +triage --max 1` is the harmless liveness probe: exit code 2 = expired or
   revoked -> continue here.
2. Run `gws auth login --scopes https://www.googleapis.com/auth/gmail.modify 2>&1` as a
   **background task** with both streams captured. Despite its `--help` line, gws does
   **not** open a browser: it prints `Open this URL in your browser to authenticate:` plus
   the URL (to stderr; stdout when a proxy variable is set), then blocks on a random
   `http://localhost:<port>` callback until consent completes - a foreground call hangs the
   session, and there is no timeout. `gmail.modify` alone covers everything in
   `gmail.skill.md` (triage, read, attachments, send, reply, forward, drafts); gws adds
   openid/email/profile itself. `--scopes` takes full scope URLs, comma-separated - short
   names are rejected. Do not use `--readonly` (it breaks send, reply, forward, and drafts)
   and, from an agent, not `-s gmail` alone: in a terminal (TTY) it opens a full-screen scope
   picker before printing any URL (Space selects, Enter confirms); without a TTY it requests
   `gmail.modify` just the same. Unscoped, the picker preselects seven Workspace services,
   and its Full Access template exceeds the ~25-scope cap of an unverified app and fails.
3. Read the URL from the task output and hand it to the owner: paste it in chat, or open it
   for them (`open "<url>"` on macOS - through the `/tmp/nepo` wrapper where bare `open` is
   blocked; `xdg-open "<url>"` on a Linux desktop). They choose the account, click
   **Continue** (or Advanced > Go to `<app>` (unsafe)) on "Google hasn't verified this app",
   and tick the Gmail box (or Select all). Never ask for passwords, codes, or tokens in chat.
   The redirect must land on the machine running `gws`: over SSH the owner first forwards
   the port (`ssh -L <port>:localhost:<port> <host>`; the port is the `redirect_uri` in the
   printed URL) and only then opens the URL - if that is impractical, use step 5.
4. The task ends with a JSON block containing `"status": "success"` and the account; if you
   missed it, poll `gws auth status` every ~15 s until `auth_method` is no longer `none`
   (give up after ~5 min: kill the task and ask). Credentials are encrypted at rest
   (`~/.config/gws/credentials.enc`); the key sits in the OS keychain on macOS, and in
   `~/.config/gws/.encryption_key` on Linux (the default `keyring` backend has no secret
   service there and falls back to the file automatically - SSH, CI, and Docker work;
   `GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file` forces file-only storage anywhere, needed on a
   Mac whose keychain is locked, e.g. over SSH).
5. **Headless / remote host (no browser at all) - the one time a live credential leaves
   this machine; confirm with the owner first:** finish steps 2-4 on a machine with a
   browser, then stream the export straight onto the host so no plaintext copy lands in a
   working tree: `gws auth export --unmasked | ssh <host> 'umask 077; mkdir -p ~/.solaris/gws;
   cat > ~/.solaris/gws/gws-credentials.json'` (through the `/tmp/hss` wrapper where bare
   `ssh` is blocked). On the host `export
   GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE=~/.solaris/gws/gws-credentials.json` (persist it in
   the shell profile or the service's environment); the file carries the client id/secret and
   the refresh token, so the host needs no `client_secret.json`. It expires like any login
   (testing-mode 7 days): exit code 2 there means re-login here and repeat the hand-off, as
   `gws auth login` cannot run headless. Treat the file like `credentials.md`.

## 4. Verify + Record

1. `gws gmail users getProfile --params '{"userId":"me"}'` - the `emailAddress` in the reply
   is the signed-in account; `gws gmail +triage --max 3` proves read access.
2. Record in the project's `ai/.memory/resources.md`: `gmail_account: <email>`, the GCP
   project id, and (remote hosts) host + install path + credentials-file path. Identifiers
   only - never tokens or the client JSON. `gws-setup` is done; `gmail.skill.md` takes over.

## Troubleshooting

- **"Access blocked" / 403 at consent** -> the account is not a Test user (step 2) -> add
  it, retry login.
- **`redirect_uri_mismatch`** -> the client is not a Desktop app -> recreate it as one.
- **`accessNotConfigured` on the first call** -> Gmail API not enabled in the project ->
  enable it, retry after a minute.
- **Exit code 2 on any command** -> credentials missing or expired (testing-mode 7-day
  expiry) -> rerun the step 3 login; on a headless host redo the step 3.5 hand-off (login
  cannot run there).
- **Consent screen error about too many scopes** -> more than ~25 scopes were selected
  (picker Full Access, or a long `--scopes` list) -> rerun step 3.2 as written.
- **`Using keyring backend: keyring` on stderr** on every run is normal, not an error.
- **Unexpected account, or it works without any login** -> Application Default Credentials
  (`GOOGLE_APPLICATION_CREDENTIALS`, or gcloud's ADC file) are a silent extra credential
  source; `credential_source` in `gws auth status` names the active one.
- **Start over:** `gws auth logout` clears `credentials.enc` and the token cache and keeps
  `client_secret.json` - but it also deletes the file named by
  `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE`, so unset that first on a headless host.