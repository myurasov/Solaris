# docker-home image: a Linux home for one project's agent sessions. Built per project by dh-build.sh
# with the host user's uid/gid baked in, so files written to the bind-mounted project folder stay yours.
# Harnesses (claude, codex, opencode) are installed system-wide because /home/dev is a host directory
# mounted at run time; per-user state (logins, sessions, caches) lands there and survives rebuilds.
ARG DH_BASE_IMAGE=ubuntu:24.04
FROM ${DH_BASE_IMAGE}

ARG DH_UID=1000
ARG DH_GID=1000
ARG DH_NODE_MAJOR=22

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    DISABLE_AUTOUPDATER=1 \
    UV_LINK_MODE=copy

# Base tooling: git, tmux (long-lived sessions), ssh/rsync (remote hosts), ripgrep/jq, python3, compilers.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg git tmux rsync openssh-client ripgrep jq less nano \
      python3 python3-venv build-essential pkg-config tzdata unzip zip procps \
    && rm -rf /var/lib/apt/lists/*

# Node.js (the three harnesses ship as npm packages with native binaries per platform).
RUN curl -fsSL "https://deb.nodesource.com/setup_${DH_NODE_MAJOR}.x" | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI.
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# uv (Python project + interpreter manager), system-wide.
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh

# The harnesses. Auto-update is disabled (see DISABLE_AUTOUPDATER); dh-rebuild.sh refreshes them.
RUN npm install -g @anthropic-ai/claude-code @openai/codex opencode-ai \
    && npm cache clean --force

# The container user mirrors the host user (uid/gid from build args). ubuntu:24.04 ships a stock
# 'ubuntu' user on uid 1000 that would collide on most Linux hosts, so it goes first. There is no
# sudo on purpose: root inside could plant setuid binaries or root-owned files in the bind mounts.
# Extra system packages go into this Dockerfile, applied with dh-rebuild.sh.
RUN (userdel -r ubuntu 2>/dev/null || true) \
    && (groupadd -g "${DH_GID}" dev 2>/dev/null || true) \
    && useradd -m -u "${DH_UID}" -g "${DH_GID}" -s /bin/bash dev

USER dev
WORKDIR /home/dev
CMD ["sleep", "infinity"]
