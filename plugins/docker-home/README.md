# docker-home - Solaris Plugin <!-- omit in toc -->

- [What It Is](#what-it-is)
- [Why](#why)
- [Workflow](#workflow)
- [Layout](#layout)
- [State](#state)
- [Install](#install)
- [Limits](#limits)
- [Design Notes](#design-notes)

## What It Is

A per-project Linux container that is the agent's whole world. The project folder (the ai-pack
root, `projects/<group>/<slug>/` under Solaris) is bind-mounted at `/home/dev/<slug>`, the
container's home `/home/dev` is a host directory under `~/.solaris/docker-home/<slug>/home/`, and
the coding harness (`claude`, `codex`, `opencode`) runs inside. Seven shortcut scripts
(`dh-build.sh`, `dh-start.sh`, `dh-stop.sh`, `dh-enter.sh`, `dh-status.sh`, `dh-rebuild.sh`,
`dh-remove.sh`) manage it from any terminal; the skill drives the same scripts on request.

## Why

Coding harnesses discover instructions above the directory they are launched in: Claude Code loads
every `CLAUDE.md` up to the filesystem root, Codex, opencode and Gemini walk up to the nearest git
root for `AGENTS.md` and config, Amp walks to the home directory. A project session launched inside
a Solaris tree therefore inherits the framework's orchestrator instructions, and any harness can
write anywhere the user can. Inside the docker home neither is possible: there is no directory
above the project, and no framework path exists to write to. The boundary is the filesystem, not a
rule.

## Workflow

The primary scenario is a Linux host (a GPU box) that holds a synced copy of the whole Solaris tree:

1. `ssh host`, run `claude` or `opencode` at the Solaris root as usual, `develop-project <slug>`.
2. Say **"build docker home"**. The engineer asks the per-host options (GPUs, network, extra
   mounts), runs `dh-build.sh` locally on that host, records image, container and state dir in
   `ai/.memory/resources.md`, and prints the enter command.
3. In the same terminal: `bash ai/plugins/docker-home/dh-enter.sh`, then `claude` (or `codex`,
   `opencode`). Log in once; the login persists in the home directory. Detach with `Ctrl-b d`;
   the session keeps running. Re-attach later with the same command.
4. `dh-rebuild.sh` after Dockerfile or option changes (home kept); `dh-remove.sh` to delete
   everything but the project.

Nothing runs automatically: no build or start on `develop-project`, at session start, or on
detected drift. The same flow works on a Mac with Docker Desktop for Linux-buildable projects.

## Layout

| File | Role |
|---|---|
| `shared/dh-common.sh` | sourced helpers: project root and slug resolution, names, `dh.conf`, guards |
| `shared/dh-build.sh` | build the image (uid/gid baked in), ask and store per-host options, start |
| `shared/dh-start.sh` | create or start the container; seeds the home directory on first start |
| `shared/dh-stop.sh`, `shared/dh-enter.sh`, `shared/dh-status.sh`, `shared/dh-rebuild.sh`, `shared/dh-remove.sh` | the remaining verbs |
| `shared/Dockerfile` | Ubuntu 24.04 + git, tmux, ssh, rsync, ripgrep, jq, python3, build tools, Node 22, gh, uv, and the three harnesses via npm; user `dev` on the host uid/gid, no sudo |
| `shared/docker-home.skill.md` | trigger-invoked procedure (the verbs, the build questions, troubleshooting) |
| `shared/docker-home.rule.md` | always-on boundary contract (agent-inside model, no automatic actions, mount contract, one active side) |
| `manifest.json` | plugin manifest; `setup.notes` name the preconditions |

No `mcps.json`: the plugin ships no MCP servers.

## State

- `~/.solaris/docker-home/<slug>/dh.conf`: per-host options as shell assignments (`DH_GPUS`,
  `DH_NETWORK`, `DH_SHM_SIZE`, `DH_EXTRA_ARGS`, `DH_BASE_IMAGE`, identity-mount switches).
- `~/.solaris/docker-home/<slug>/home/`: the container's `/home/dev`. Harness logins and session
  stores, caches, Linux venvs. Seeded on first start with `.claude/settings.json`
  (`autoMemoryEnabled: false`, per the Solaris memory doctrine) and a minimal `.bashrc`.
- Image `solaris-dh-<slug>`, container `dh-<slug>` (`--init`, `--restart unless-stopped`, tmux
  session `agent`).
- Mounts: the project folder (rw), the home directory (rw), the overlay itself re-mounted read-only
  on top of the project (the container cannot rewrite the scripts the host runs), `~/.gitconfig`
  read-only when present, and the ssh agent socket; your ssh `config` and `known_hosts` are copied
  into the home (private keys stay on the host; `DH_MOUNT_SSH=keys` mounts `~/.ssh` read-only
  instead, `0` disables both). Never the framework root, other projects, or the Docker socket; no
  `sudo` inside; `--cap-drop NET_RAW` with host networking; extra `docker run` args that would breach
  the boundary are refused.
- Ownership: the container carries a `dh.project` label and `dh.conf` records `DH_OWNER_ROOT`; the
  scripts refuse to touch a container or state dir that belongs to another project folder (slug
  collisions, moved projects). Stop, rebuild and remove refuse while a tmux session is live inside
  unless `--force`.

## Install

Under a Solaris checkout: "add plugin docker-home to `<project>`" (copy mode) or "link plugin
docker-home to `<project>`". Attaching copies `shared/` into `ai/plugins/docker-home/` and surfaces
the setup notes; it builds nothing. Standalone ai-packs carry the overlay copy and need only Docker,
`bash` and `python3` on the host.

## Limits

- Linux inside: no Xcode, no macOS-only tools. macOS-native projects stay on the host.
- Only containerized launches are isolated. A harness launched on the host inside the project folder
  behaves as before.
- Only the overlay is protected from the container agent. Other host-executed files in the project
  (git hooks, a project `.claude/settings.json`, Makefiles) are agent-writable: review them before
  running them on the host.
- Slugs must be unique per host: container, image and state dir are keyed by slug.
- One active side per project: the container agent and a host session edit the same files.
- With a synced Solaris tree, git state is per host (sync tools usually exclude `.git`): pick one
  git-authoritative side per project, and expect sync conflicts if both sides append to
  `ai/.memory/*.jsonl` between syncs.
- Network egress to Ubuntu, NodeSource, npm and GitHub is needed at build time.

## Design Notes

Bind mount, never a copy: the project stays the single source of truth for code, pack and
`ai/.memory/`, and everything the container writes reaches the host (and any sync layer) directly.
Heavy generated artifacts belong in the home directory, not the mount. The home is a bind directory
rather than a named volume so it follows the Solaris remote-footprint rule (`~/.solaris/<component>/`:
discoverable, inventoried, removable in one place). The scripts derive everything from their own
location and never reference a Solaris checkout, so a detached pack keeps working.
