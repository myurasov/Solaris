# Rule: safety <!-- omit in toc -->

- [Confirm before these](#confirm-before-these)
- [rsync / deploy specifics](#rsync--deploy-specifics)
- [Read before you overwrite](#read-before-you-overwrite)
- [Secrets](#secrets)
- [Autonomy waiver](#autonomy-waiver)

Confirm before doing something that is hard to reverse or reaches outside the local machine. Solaris drives
remote hosts (`ssh`, `rsync`, deploy) and git, so this is the most important operational guardrail. It is
baked into each project's `engineer.agent.md` too.

## Confirm before these

- **Destructive (local):** `rm -rf`, deleting or overwriting files you did not create, `git reset --hard`,
  `git clean -fd`, force-overwrites, dropping databases.
- **Remote-mutating:** any `ssh`/remote command that writes, installs, deletes, or restarts services;
  `rsync` that writes to a remote (especially with `--delete`); deploy.
- **Outward / publishing:** `git push`, opening or commenting on PRs/issues, sending email or messages,
  uploading, or posting to any external service. Publishing is effectively irreversible (it may be cached
  or indexed even if later deleted).

Show the exact command (or diff) first, then ask once, concisely. Approval for one action does not extend to
the next.

## rsync / deploy specifics

- Exclude `.venv`, `.git`, secrets, and build artifacts.
- **No `--delete` by default** - do not clobber remote-generated outputs (logs, checkpoints, trained
  models). Pulling artifacts back from the remote is an explicit, separate action.
- In remote-code mode the code already lives on the remote; there is no deploy by default.

## Read before you overwrite

Inspect a target before deleting or overwriting it. If what you find contradicts how it was described, or
you did not create it, stop and surface that instead of proceeding.

## Secrets

Never print, paste, or commit the contents of any `credentials.md`. In any outward content (commits, PRs,
messages) reference a secret by name/location, never by value.

## Autonomy waiver

A durable "work autonomously until X" instruction (or `commit!`) waives the per-step confirmation for that
task's duration - the format and safety conventions still apply, and genuinely irreversible or outward
actions still get a one-line heads-up. Secrets are never waived.
