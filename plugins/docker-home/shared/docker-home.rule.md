_Rev. 1_

# Rule: docker-home Boundary <!-- omit in toc -->

- [What the Docker Home Is](#what-the-docker-home-is)
- [Hard Rules](#hard-rules)
- [Inside the Container](#inside-the-container)
- [On the Host](#on-the-host)

Always-on while this plugin is attached. The procedure and the command reference live in
[`docker-home.skill.md`](docker-home.skill.md).

## What the Docker Home Is

A per-project Linux container that is the agent's whole world: the project folder (this ai-pack's
root) is bind-mounted at `/home/dev/<slug>`, the container's home `/home/dev` is a host directory
under `~/.solaris/docker-home/<slug>/home/`, and the coding harness (claude, codex, opencode) runs
inside it. Nothing above the project exists in there: no parent directories, no framework files, no
other projects, no host dotfiles. That is the isolation: not a rule the agent follows, but a
filesystem it cannot see past.

## Hard Rules

- **Nothing runs by itself.** Building, starting, stopping, entering, rebuilding or removing the
  docker home happens only when the user asks for it (the skill's triggers). Never build or start it
  as a side effect of another task, at session start, during `develop-project`, or because the image
  looks stale. Status is reported only when asked.
- **The shortcut scripts are the only management path** (`dh-*.sh` in this overlay). No hand-written
  `docker run` for this project; options go through `dh.conf`.
- **Mount nothing beyond the contract.** The container gets the project folder, its own home
  directory, this overlay re-mounted read-only on top of the project (so the scripts the host runs
  cannot be rewritten from inside), the optional read-only git identity, and the ssh agent socket;
  your ssh client config and known hosts are copied in, private keys stay on the host. Never add
  the framework root, other projects, the Docker socket, or `--privileged`: each would breach the
  boundary. There is no root inside (no `sudo`).
- **Host-executed project files are untrusted.** Anything the host later runs from the project
  folder (git hooks, a project `.claude/settings.json` with hooks, Makefiles, a rewritten plugin
  link file) may have been written by the container agent. Only this overlay is protected; review
  the rest before executing it on the host.
- **One active side per project.** While a container session is working, do not edit the project from
  a host session (same files, and any sync or git layer sees both). Hand off, then step back.
- **Removal is destructive.** `dh-remove.sh` removes the container and the image and, unless
  `--keep-home`, the home directory (harness logins, sessions, caches). Confirm with the user first,
  then pass `--yes`.

## Inside the Container

`/.dockerenv` exists and `$DH_PROJECT` names the project mount. In there this project is standalone:
no Solaris checkout, no framework tools, no framework memory; the pack's "under a Solaris checkout"
affordances do not apply. Work only under `$DH_PROJECT`; the home directory is for harness state and
caches. There is no root inside: extra system packages go into the overlay's `Dockerfile` and land
with `dh-rebuild.sh` on the host. The management scripts refuse to run in there (only `dh-status.sh` answers, read-only); do not try to
build or start containers from inside.

## On the Host

When a session runs on the host inside this project and the user asks for long-lived or isolated
work, point them to the docker home (`dh-enter.sh`) instead of improvising isolation by other means.
Otherwise say nothing about it; the plugin acts only on request.
