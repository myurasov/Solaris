# rev. 1

"""kaggle gateway: the pinned Kaggle CLI, installed per project or task.

Solaris agents run this instead of a bare `kaggle`. It finds the calling
context - a project root (the folder holding ai/manifest.json) or an ad-hoc
task folder (the one holding the task notes.md) - keeps a private venv there
with exactly the pinned CLI installed, and execs that CLI with the arguments
unchanged. With no project or task around (the framework root) it runs the
same pinned CLI from a throwaway uv environment, so nothing is ever installed
globally.

    python3 <plugin-dir>/kaggle.py <kaggle args>

Stdlib only; needs uv on PATH. Gateway messages go to stderr, so stdout stays
exactly what the Kaggle CLI printed.
"""

import fcntl
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Latest release of the newest minor line (2.2), plus the kagglesdk it was tested
# with (the SDK holds the auth/HTTP/API code and would otherwise float). Move both
# together with the vendored kaggle-cli/ skill from the same tag - see the README.
PIN = "2.2.4"
SDK_PIN = "0.1.37"
REQS = [f"kaggle=={PIN}", f"kagglesdk=={SDK_PIN}"]
ENV_DIR = ".venv-kaggle"
STAMP = ".solaris-kaggle-pin"


def say(msg):
    print(f"kaggle gateway: {msg}", file=sys.stderr)


def find_uv():
    uv = shutil.which("uv")
    if not uv:
        sys.exit("kaggle gateway: uv not found on PATH - install it first (https://docs.astral.sh/uv/)")
    return uv


def is_task(d):
    # ad-hoc task notes point at the ad-hoc-task skill near the top
    notes = d / "notes.md"
    return notes.is_file() and "ad-hoc-task" in notes.read_text(errors="replace")[:4096]


def find_context():
    """Project root or task folder this call belongs to; None at the framework level."""
    cwd = Path.cwd()
    chain = (cwd, *cwd.parents)
    # a project wins over task-style notes inside it (e.g. graduated research notes)
    for d in chain:
        if (d / "ai" / "manifest.json").is_file():
            return d
    for d in chain:
        if is_task(d):
            return d
    # a copied overlay sits at <project>/ai/plugins/kaggle/kaggle.py
    here = Path(__file__).resolve().parent
    if len(here.parents) > 2 and here.parent.name == "plugins" and here.parents[1].name == "ai":
        root = here.parents[2]
        if (root / "ai" / "manifest.json").is_file():
            return root
    return None


def ready(env, want):
    stamp = env / STAMP
    # exists() follows the symlink: a venv whose base Python was removed counts as broken
    return ((env / "bin" / "kaggle").is_file() and (env / "bin" / "python").exists()
            and stamp.is_file() and stamp.read_text() == want)


def install(env, want):
    if env.exists() and not (env / "pyvenv.cfg").is_file():
        sys.exit(f"kaggle gateway: {env} exists but is not a venv - move it aside first")
    uv = find_uv()
    say(f"installing {' '.join(REQS)} into {env}")
    for cmd in (
        [uv, "venv", "--quiet", "--clear", "--python", ">=3.11", str(env)],
        [uv, "pip", "install", "--quiet", "--python", str(env / "bin" / "python"), *REQS],
    ):
        if subprocess.run(cmd, stdout=sys.stderr).returncode:
            sys.exit(f"kaggle gateway: install failed: {' '.join(cmd)}")
    (env / STAMP).write_text(want)


def ensure_env(ctx):
    """Make <ctx>/.venv-kaggle hold exactly REQS; return its kaggle executable."""
    env = ctx / ENV_DIR
    # the stamp records the path too: a venv bakes absolute paths and breaks when its folder moves
    want = "\n".join([*REQS, str(env.resolve())]) + "\n"
    if not ready(env, want):
        # parallel first calls in one context would clobber each other's install
        fd = os.open(ctx, os.O_RDONLY)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
            except OSError:
                pass  # no flock on this filesystem (e.g. NFS): install unlocked
            if not ready(env, want):
                install(env, want)
        finally:
            os.close(fd)
    return env / "bin" / "kaggle"


def main():
    # arguments pass through untouched: the CLI decides which commands may run signed
    # out (auth login, --version, ...) by the raw command line, so no prefix flags
    args = sys.argv[1:]
    ctx = find_context()
    if ctx is None:
        uv = find_uv()
        say("no project or task folder here - running the pinned CLI from a throwaway uv environment")
        withs = [a for r in REQS for a in ("--with", r)]
        os.execv(uv, [uv, "run", "--quiet", "--no-project", "--python", ">=3.11", *withs, "kaggle", *args])
    kaggle = ensure_env(ctx)
    os.execv(str(kaggle), [str(kaggle), *args])


if __name__ == "__main__":
    main()
