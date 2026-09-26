---
name: docker-home
triggers: ["build docker home", "start docker home", "stop docker home", "enter docker home", "docker home status", "rebuild docker home", "remove docker home", "docker-home"]
summary: Build, start, enter, rebuild or remove this project's docker home (a per-project Linux container that runs the coding harness with only the project folder mounted) through the dh-*.sh shortcut scripts in this overlay, strictly on the user's request.
---
_Rev. 1_

# Skill: docker-home - Run the Agent Inside a Project Container <!-- omit in toc -->

- [When This Fires](#when-this-fires)
- [Preconditions](#preconditions)
- [Verbs](#verbs)
- [Build](#build)
- [Enter and Detach](#enter-and-detach)
- [Rebuild](#rebuild)
- [Remove](#remove)
- [Where Things Live](#where-things-live)
- [Troubleshooting](#troubleshooting)

## When This Fires

Only on an explicit request naming the docker home (the triggers above). Each verb maps to one
shortcut script in this overlay (`ai/plugins/docker-home/`, or the plugin's `shared/` when linked);
run the script, do not re-implement it. Everything here acts on the machine the session runs on:
there is no remote orchestration. If the user's Solaris tree is synced to a Linux host, they run the
build there, in a session on that host.

## Preconditions

- Docker on this machine: Docker Desktop on macOS, `docker-ce` on Linux with the user in the `docker`
  group (`docker info` must work without sudo). GPU pass-through needs the NVIDIA container toolkit.
- The scripts need `bash` and `python3` on the host (both stock on macOS and Ubuntu). Run them as
  `bash <overlay>/dh-<verb>.sh ...` so the executable bit does not matter.
- Linux inside: projects that need macOS-native tooling (Xcode) cannot use a docker home.
- No root inside the container (no `sudo`): extra system packages go into the overlay's `Dockerfile`
  and land with `dh-rebuild.sh`.
- Slugs are unique per host: container, image and state dir are keyed by slug, and the scripts refuse
  to touch ones that belong to another project folder.

## Verbs

| User says | Run | Notes |
|---|---|---|
| build docker home | `bash <overlay>/dh-build.sh [flags]` | first build asks the per-host options; then starts the container |
| start docker home | `bash <overlay>/dh-start.sh` | creates the container on first start, seeds the home directory |
| stop docker home | `bash <overlay>/dh-stop.sh` | keeps image and home; refuses while a tmux session is live inside unless `--force` |
| enter docker home | `bash <overlay>/dh-enter.sh` | attaches the tmux session; needs the user's terminal (see below) |
| docker home status | `bash <overlay>/dh-status.sh` | read-only; works inside the container too |
| rebuild docker home | `bash <overlay>/dh-rebuild.sh` | fresh base + harness versions; home kept; refuses while a tmux session is live unless `--force` |
| remove docker home | `bash <overlay>/dh-remove.sh --yes` | destructive; confirm first (see below); `--force` if a tmux session is live |

## Build

1. Check the preconditions (`docker info`). On Linux, detect GPUs (`nvidia-smi`) so the defaults are
   right.
2. Ask the user the three per-host questions in chat unless the request already answers them:
   GPUs (`all` / `none`), container network (`host` on Linux hosts, `bridge` on macOS), extra
   `docker run` arguments (extra mounts such as a datasets directory; usually none). Shared memory
   defaults to `8g` when GPUs are passed through.
3. Run non-interactively with the answers as flags, so the script never blocks on a prompt:
   ```bash
   bash ai/plugins/docker-home/dh-build.sh --non-interactive --gpus all --network host --extra "-v /data:/data"
   ```
   The build pulls Ubuntu 24.04, Node, uv, gh and the three harnesses (a few minutes, network
   needed). The answers are stored in `~/.solaris/docker-home/<slug>/dh.conf`; a later `dh-build.sh`
   reuses them, flags override, deleting the file asks again.
4. Record the inventory in `ai/.memory/resources.md`: host name, image, container, state dir, the
   options chosen. Tell the user the enter command the script printed.
5. Do not enter on their behalf: the container session is theirs. Stop editing the project from this
   host session once they are inside (one active side per project).

## Enter and Detach

`dh-enter.sh` runs `docker exec -it <container> tmux new-session -A -s agent`: the first call creates
the tmux session, later calls re-attach to it. Inside, the user starts `claude`, `codex` or
`opencode` in the project directory; the first run in a fresh home directory asks for a login (Claude
Code prints an OAuth URL to open in any browser, Codex offers device login, opencode takes API keys),
after which the logins persist in the home directory. Detach with `Ctrl-b d`; the session and any
running agent keep going. `dh-enter.sh <command>` runs a one-off command instead of the tmux session
(for example `dh-enter.sh claude --version`); this form is the one an agent may use, since it needs
no terminal.

## Rebuild

`dh-rebuild.sh` rebuilds the image with `--pull` (fresh base image and harness versions), removes
the old container and starts a new one on the same home directory and options. Use it after
editing the overlay's `Dockerfile` or `dh.conf`. Nothing in the home directory or the project is
touched.

## Remove

Destructive: the state dir under `~/.solaris/docker-home/<slug>/` holds the container's home
(harness logins, sessions, caches, Linux venvs). Show the user what `dh-remove.sh` lists, get an
explicit yes in chat, then run `bash <overlay>/dh-remove.sh --yes` (`--keep-home` keeps the state
dir and removes only container and image). The project folder is never touched by any verb.

## Where Things Live

| Path | What |
|---|---|
| `<project>/ai/plugins/docker-home/` | this overlay: `Dockerfile`, `dh-*.sh`, this skill, the rule |
| `~/.solaris/docker-home/<slug>/dh.conf` | per-host options (sourced by the scripts) |
| `~/.solaris/docker-home/<slug>/home/` | the container's `/home/dev`: logins, sessions, caches, venvs |
| `~/.solaris/docker-home/<slug>/home/.ssh/` | copies of your ssh `config` and `known_hosts` (writable); private keys stay on the host, the forwarded agent socket signs (`DH_MOUNT_SSH=keys` in `dh.conf` mounts `~/.ssh` read-only instead, `0` disables both) |
| `/home/dev/<slug>/ai/plugins/docker-home` (inside) | this overlay, re-mounted read-only so the container cannot rewrite the scripts the host runs |
| `/home/dev/<slug>` (inside) | the project folder, bind-mounted read-write |
| image `solaris-dh-<slug>`, container `dh-<slug>` | per project; `dh-status.sh` shows them |

Inside the container, keep heavy generated artifacts out of the project mount: Linux venvs via
`UV_PROJECT_ENVIRONMENT=$HOME/.venvs/<slug>`, caches under `$HOME`. The host's own `.venv` stays
untouched that way.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `docker daemon is not reachable` | Docker not running (macOS: start Docker Desktop) or the user is not in the `docker` group (Linux: `sudo usermod -aG docker $USER`, re-login) |
| `docker run` fails on `--gpus` | NVIDIA container toolkit missing; install it, or rebuild with `--gpus none` |
| `--network host` refused on macOS | Docker Desktop needs host networking enabled in its settings; or use `bridge` |
| `rootless docker is not supported` | the scripts need a rootful daemon so files land under your uid; use a standard docker-ce install |
| `tmux session 'agent' is live` on stop, rebuild or remove | an agent may be mid-flight inside; attach, end it, or pass `--force` knowingly |
| `belongs to project ...` | two projects share a slug on this host, or the project moved; use a unique slug, or clear the old container / fix `DH_OWNER_ROOT` in `dh.conf` |
| ssh inside reports no identities | on Linux the forwarded agent socket died with your old login: `docker rm -f dh-<slug>`, then `dh-start.sh` picks up the current one |
| `ping` fails inside with host networking | raw sockets are dropped on purpose (`--cap-drop NET_RAW`); use `curl` or `nc` |
| harness asks to log in again | the home directory was removed (`dh-remove.sh` without `--keep-home`); log in once more |
| `you are inside the docker home already` | management verbs run on the host only; detach (`Ctrl-b d`) or exit first |
| build fails fetching packages | the machine has no egress to Ubuntu, NodeSource, npm or GitHub; run the build where it does |
