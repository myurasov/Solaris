# rev. 6

"""kaggle gateway: the pinned Kaggle CLI, installed per project or task.

Solaris agents run this instead of a bare `kaggle`. It finds the calling
context - a project root (the nearest folder up with exactly one child folder
<pack> holding an ai-pack manifest.json, where <pack> is the project's ai-pack
folder: default aipack/, ai/ in older projects, any name) or an ad-hoc task
folder (the one holding the task notes.md) - keeps a private venv there with
exactly the pinned CLI installed, and execs that CLI with the arguments
unchanged. With no project or task around (the framework root) it
runs the same pinned CLI from a throwaway uv environment, so nothing is ever
installed globally.

    python3 <plugin-dir>/tools/kaggle.py <kaggle args>
    python3 <plugin-dir>/tools/kaggle.py --sdk <read> <args>

Run it from the project root or task folder; the search for one never reads
the home folder itself or any folder above it, and a folder holding two
ai-packs stops it with an error. A copied overlay
(<project>/<pack>/plugins/kaggle/tools/kaggle.py) also finds its project from
its own location; the live copy in a Solaris checkout
(<solaris>/plugins/kaggle/shared/tools/kaggle.py, used by linked projects,
ad-hoc tasks and the framework root) goes by the working directory only.

With --sdk first it runs kaggle_sdk.py, beside this file, in that same
environment instead of the CLI, with the rest of the arguments: read-only
reads of data the CLI drops, through Kaggle's Python SDK. Its output and exit
code pass through.

Inside a project or task, two hooks from the tools beside this file run first,
and neither can block or change a Kaggle command: an activity stamp for account
sharing (kaggle_share.py; skipped under KAGGLE_SHARE_QUIET=1, which the plugin's
own monitoring sets), and for a `competitions leaderboard` read (--show or
--download), a tee through kaggle_lb.py that passes the output through unchanged
and saves the read (KAGGLE_LB_RECORD=0 turns that off). At the framework root
the tee runs only when KAGGLE_LB_DIR names a store; otherwise the read is not
saved and the gateway says so. A failing hook only notes it on stderr. An --sdk
call gets the stamp, never the tee.

Two gates can block. A `kernels push` (or its alias `kernels update`; `k` for
`kernels`) runs only while an open account-sharing lease covers the pushed
kernel (kaggle_share.py push_lease: the kernel in <folder>/kernel-metadata.json,
the lease taken with `kaggle_share.py acquire --path <folder>`). Otherwise the
gateway exits 3 before any install, stamp or Kaggle call, with a one-line
message on how to take one; the gate fails closed when the lease cannot be
checked. KAGGLE_SHARE_QUIET does not bypass it; KAGGLE_PUSH_WITHOUT_LEASE=1,
the owner's explicit override, does.

The second holds submits to kaggle_submit.py, the gated submit beside this
file: a `competitions submit` (`c` for `competitions`) runs only when the call
carries KAGGLE_SUBMIT_GATED=1, which that tool sets on its own submit call.
Any other submit exits 3 the same way, before any install, stamp or Kaggle
call, with a one-line pointer to that tool. KAGGLE_SHARE_QUIET does not bypass
it; KAGGLE_SUBMIT_WITHOUT_GATE=1, the owner's explicit override, does.

Stdlib only; needs uv on PATH. Gateway messages go to stderr, so stdout stays
exactly what the Kaggle CLI (or kaggle_sdk.py) printed.
"""

import argparse
import fcntl
import importlib.util
import json
import os
import shlex
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
# hook switches: monitoring calls leave no activity stamp; 0 stops saving leaderboard reads
QUIET_ENV = "KAGGLE_SHARE_QUIET"
RECORD_ENV = "KAGGLE_LB_RECORD"
# a leaderboard store outside any project or task (kaggle_lb's --dir)
LB_DIR_ENV = "KAGGLE_LB_DIR"
# a first argument that runs this SDK reader, beside the gateway, instead of the CLI
SDK_FLAG = "--sdk"
SDK_TOOL = "kaggle_sdk.py"
# the owner's explicit override of the lease gate on `kernels push`; a refused push exits with EXIT_REFUSED
PUSH_ENV = "KAGGLE_PUSH_WITHOUT_LEASE"
# a `competitions submit` runs only with kaggle_submit.py's mark on its own call, or the owner's explicit override
SUBMIT_MARK = "KAGGLE_SUBMIT_GATED"
SUBMIT_ENV = "KAGGLE_SUBMIT_WITHOUT_GATE"
EXIT_REFUSED = 3


def say(msg):
    print(f"kaggle gateway: {msg}", file=sys.stderr)


def refuse(msg):
    say(msg)
    sys.exit(EXIT_REFUSED)


def rel(p):
    # relative to the working folder when inside it, else absolute
    p = Path(p)
    try:
        return str(p.relative_to(Path.cwd()))
    except ValueError:
        return str(p)


def find_uv():
    uv = shutil.which("uv")
    if not uv:
        sys.exit("kaggle gateway: uv not found on PATH - install it first (https://docs.astral.sh/uv/)")
    return uv


def is_task(d):
    # ad-hoc task notes point at the ad-hoc-task skill near the top
    notes = d / "notes.md"
    return notes.is_file() and "ad-hoc-task" in notes.read_text(errors="replace")[:4096]


def is_pack(d):
    # an ai-pack manifest carries framework_version and a project object; a plugin's has neither
    try:
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(m, dict) and "framework_version" in m and isinstance(m.get("project"), dict)


def pack_of(d):
    """The ai-pack folder of d: its one direct child folder holding an ai-pack manifest.json, else None."""
    try:
        packs = sorted(c for c in d.iterdir() if not c.name.startswith(".") and is_pack(c))
    except OSError:
        return None
    if len(packs) > 1:
        # ambiguous: stop before any Kaggle call
        sys.exit(f"kaggle gateway: {d}: more than one ai-pack ({', '.join(p.name for p in packs)})")
    return packs[0] if packs else None


def walk_up(d):
    """d and the folders above it, stopping before the home folder, any folder above it, or /."""
    home = Path(os.path.expanduser("~"))
    homes = (home, home.resolve()) if home.is_absolute() else ()
    chain = []
    for f in (d, *d.parents):
        # never read $HOME or above: on macOS ~/Desktop can raise privacy prompts, and an automounted /home is slow
        if f.parent == f or any(f == h or f in h.parents for h in homes):
            break
        chain.append(f)
    return chain


def find_context():
    """Project root or task folder this call belongs to; None at the framework level."""
    chain = walk_up(Path.cwd())
    # a project wins over task-style notes inside it (e.g. graduated research notes)
    for d in chain:
        if pack_of(d):
            return d
    for d in chain:
        if is_task(d):
            return d
    # a copied overlay sits at <project>/<pack>/plugins/kaggle/tools/kaggle.py; a project root at home or above is
    # never listed (walk_up is empty there)
    here = Path(__file__).resolve().parent
    if len(here.parents) > 2:
        plugins, pack = here.parents[1], here.parents[2]
        if plugins.name == "plugins" and walk_up(pack.parent) and is_pack(pack) and pack_of(pack.parent) == pack:
            return pack.parent
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


def load_tool(name):
    """A tool module from this folder (kaggle_share, kaggle_lb), loaded by path."""
    path = Path(__file__).resolve().parent / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"kaggle_gateway_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    # keep __pycache__ out of the plugin folder
    sys.dont_write_bytecode = True
    spec.loader.exec_module(mod)
    return mod


def stamp_activity(ctx, args):
    """Record this call for kaggle_share's view of which projects use the account."""
    if os.environ.get(QUIET_ENV) == "1":
        return
    try:
        load_tool("kaggle_share").stamp(ctx, args)
    except (Exception, SystemExit) as e:
        say(f"activity stamp skipped ({e!r})")


class _Quiet(argparse.ArgumentParser):
    # raise instead of printing usage: the gate only inspects the arguments
    def error(self, message):
        raise ValueError(message)


def push_folder(args):
    """The kernel folder a `kernels push` call would push (alias `update`, group alias `k`; default the working
    folder), else None. Before the command only the CLI's flags can come (-W, -v, -h); -v and -h print and exit."""
    args = [str(a) for a in args]
    words = [i for i, a in enumerate(args) if not a.startswith("-")][:2]
    if len(words) < 2 or args[words[0]] not in ("kernels", "k") or args[words[1]] not in ("push", "update"):
        return None
    rest = args[words[1] + 1:]
    if {"-h", "--help", "-v", "--version"} & set(args[:words[1]]) or {"-h", "--help"} & set(rest):
        return None
    p = _Quiet(add_help=False)
    # the push options of the pinned CLI, so an abbreviation (--pa) reads as the CLI reads it
    p.add_argument("-p", "--path")
    p.add_argument("-t", "--timeout")
    p.add_argument("--accelerator")
    try:
        ns = p.parse_known_args(rest)[0]
    except (ValueError, argparse.ArgumentError):
        return None  # the CLI stops on these arguments too: nothing is pushed
    return Path(ns.path or ".")


def lease_gate(args):
    """Refuse a `kernels push` that no open account-sharing lease covers (exit 3), before any Kaggle call."""
    folder = push_folder(args)
    if folder is None:
        return
    if os.environ.get(PUSH_ENV) == "1":
        say(f"kernels push without an account-sharing lease ({PUSH_ENV}=1)")
        return
    acquire = (f"python3 {shlex.quote(rel(Path(__file__).resolve().parent / 'kaggle_share.py'))} acquire --path "
               f"{shlex.quote(str(folder))}")
    share = None
    try:
        share = load_tool("kaggle_share")
        ref = share.read_kernel(folder)[0]
        lease = share.push_lease(folder, ref)
    except (Exception, SystemExit) as e:
        # fail closed: an unchecked push could overbook the shared account
        why = str(e) if share is not None and isinstance(e, share.ShareError) else repr(e)
        refuse(f"kernels push refused: the account-sharing lease cannot be checked ({why}); fix that and take a "
               f"lease ({acquire}), or the owner sets {PUSH_ENV}=1")
    if lease is None:
        refuse(f"kernels push refused: no open account-sharing lease covers {ref}; take one first: {acquire} "
               f"(only the owner overrides this, with {PUSH_ENV}=1)")
    say(f"kernels push of {ref} under lease {lease.get('id')}")


def submit_call(args):
    """Whether a call is a `competitions submit` (group alias `c`) that would reach Kaggle: -h and -v only print.
    Before the command only the CLI's flags can come (-W, -v, -h); after it, -v is the kernel version."""
    args = [str(a) for a in args]
    words = [i for i, a in enumerate(args) if not a.startswith("-")][:2]
    if len(words) < 2 or args[words[0]] not in ("competitions", "c") or args[words[1]] != "submit":
        return False
    rest = args[words[1] + 1:]
    # after "--" every word is a value, never an option
    rest = rest[:rest.index("--")] if "--" in rest else rest
    return not ({"-h", "--help", "-v", "--version"} & set(args[:words[1]]) or {"-h", "--help"} & set(rest))


def submit_gate(args):
    """Refuse a `competitions submit` that does not come from kaggle_submit.py (exit 3), before any Kaggle call."""
    if not submit_call(args) or os.environ.get(SUBMIT_MARK) == "1":
        return
    if os.environ.get(SUBMIT_ENV) == "1":
        say(f"competitions submit without kaggle_submit.py ({SUBMIT_ENV}=1)")
        return
    tool = shlex.quote(rel(Path(__file__).resolve().parent / "kaggle_submit.py"))
    refuse(f"competitions submit refused: submits go through the gated tool, python3 {tool} <record.json> (its checks, "
           f"then --go); only the owner overrides this, with {SUBMIT_ENV}=1")


def tee_leaderboard(ctx, cmd, args):
    """Run a leaderboard read (--show or --download) through kaggle_lb, which saves it; None means exec as usual."""
    if os.environ.get(RECORD_ENV, "1") == "0":
        return None
    try:
        lb = load_tool("kaggle_lb")
        if not lb.leaderboard_read(args):
            return None
    except (Exception, SystemExit) as e:
        say(f"leaderboard save skipped ({e!r})")
        return None
    if ctx is None and not os.environ.get(LB_DIR_ENV):
        # a store needs a project or task folder, or KAGGLE_LB_DIR
        say("leaderboard read not saved: no project or task folder here (run from one, or set KAGGLE_LB_DIR)")
        return None
    # the CLI runs inside the tee from here on, so it must never be run a second time
    try:
        code = lb.tee_leaderboard(cmd, args, ctx)
    except BrokenPipeError:
        # the reader left early (e.g. piped into head): stop quietly, as the CLI would
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 1
    except Exception as e:
        say(f"leaderboard read stopped in the save hook ({e!r})")
        return 1
    # a CLI killed by a signal reports it the shell way
    return None if code is None else (code if code >= 0 else 128 - code)


def main():
    # arguments pass through untouched: the CLI decides which commands may run signed
    # out (auth login, --version, ...) by the raw command line, so no prefix flags
    # (--sdk never reaches the CLI: it runs kaggle_sdk.py instead)
    args = sys.argv[1:]
    sdk = args[:1] == [SDK_FLAG]
    tool = str(Path(__file__).resolve().parent / SDK_TOOL)
    ctx = find_context()
    # a push needs an account-sharing lease and a submit kaggle_submit.py: checked before any install, stamp or
    # Kaggle call
    lease_gate(args)
    submit_gate(args)
    if ctx is None:
        uv = find_uv()
        say("no project or task folder here - running the pinned CLI from a throwaway uv environment")
        withs = [a for r in REQS for a in ("--with", r)]
        uv_run = [uv, "run", "--quiet", "--no-project", "--python", ">=3.11", *withs]
        if sdk:
            os.execv(uv, [*uv_run, "python", tool, *args[1:]])
        cmd = [*uv_run, "kaggle"]
        code = tee_leaderboard(None, cmd, args)
        if code is not None:
            sys.exit(code)
        os.execv(uv, [*cmd, *args])
    kaggle = ensure_env(ctx)
    stamp_activity(ctx, args)
    if sdk:
        python = str(kaggle.with_name("python"))
        os.execv(python, [python, tool, *args[1:]])
    code = tee_leaderboard(ctx, [str(kaggle)], args)
    if code is not None:
        sys.exit(code)
    os.execv(str(kaggle), [str(kaggle), *args])


if __name__ == "__main__":
    main()
