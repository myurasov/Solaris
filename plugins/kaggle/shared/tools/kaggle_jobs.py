# rev. 1

"""kaggle_jobs: the project's autocode jobs, kept as files in the pack.

An autocode job (a fresh session that runs a prompt on a trigger: a daily review, say) is defined on the autocode
server. This tool keeps each definition as a file in the pack as well, so a change starts from a saved copy and
a lost definition can be set again.

    python3 <plugin-dir>/tools/kaggle_jobs.py list [--json]              the pack's files against the server's jobs
    python3 <plugin-dir>/tools/kaggle_jobs.py backup [NAME ...]          write the server's definitions into the pack
    python3 <plugin-dir>/tools/kaggle_jobs.py apply NAME ...             set the pack's definitions on the server
    python3 <plugin-dir>/tools/kaggle_jobs.py show NAME [--tool]         print one definition, or the agent's tool call
    python3 <plugin-dir>/tools/kaggle_jobs.py new NAME --template T [--set KEY=VALUE ...] [--force]

  --root DIR       the project root (default: found from the working directory)
  --autocode PATH  the autocode command (default: autocode on PATH, else ~/.autocode/bin/autocode)

A job file, <pack>/jobs/<file>.json, holds one definition with the fields of autocode's job:

    {"name": "<the job's name on the server>", "host": "<its host>", "model": "<provider/model>",
     "variant": "<variant>", "prompt": "<what each run is told>", "trigger": "<every 1h | at 09:00 | cron ... |
     fire-only>", "to": "<who gets the results>", "description": "<what it does>", "timeout": "<1h>",
     "run_usd": <the most a run may spend>, "keep_session": <true to keep each run's session>}

and, when the job runs as an OpenCode agent of its own, "opencode_agent". A field whose name starts with _ is a
note and is ignored. Text in {{BRACES}} is a placeholder of a template: a file that still has one is never applied.

list    compares each file with the server's job of the same name (a file whose name has no colon also matches
        the one job of this project that ends with :<name>, as an agent's own jobs are named <agent ID>:<name>):
        same, differs (the fields named), pack only, or server only (a job that runs in this project's folder and
        has no file). A field the file leaves empty is not compared: the server chose it. It reads `autocode job
        list --json`. Where that command is not available (an agent's shell, no autocode) it lists the files
        alone and says so.
backup  writes the server's definition of each job named, or with no name of every job that runs in this
        project's folder, to <pack>/jobs/<file>.json: the file that already holds that job, else the part of the
        name after the last colon. NAME is the job's full name or that last part. The file is written whole (a
        temp file, then a rename).
apply   sets each file named (by file name, or by the job's name) on the server with `autocode job set NAME
        --host ... --model ... [--variant ...] --prompt ... --trigger ... [--to ...] [--description ...]
        [--timeout ...] [--run-usd ...] [--keep-session] [--agent ...]`, then reads the job back and compares.
        This changes the server: it is the owner's step.
show    prints the file's definition. With --tool it prints, as one JSON object, the arguments of the agent's
        own call `autocode_me action=job_set` (job: the name's part after the last colon; the server names the
        job <agent ID>:<job>; empty fields are left out; run_usd is required there).
new     writes <pack>/jobs/NAME.json from a template of the plugin (the jobs/ folder beside tools/: plan-review),
        with each {{KEY}} replaced by --set KEY=VALUE. Filled in when not set: {{PROJECT_DIR}} the project
        folder, {{PACK}} the ai-pack folder's name, {{TOOLS}} the folder this tool is in (relative to the
        project when inside it, as a copied plugin's is; the full path of a linked plugin's), and {{ZONE}} the
        pack's "owner.timezone" (<pack>/.memory/config.json, else <pack>/defaults.json) when that is set. It
        names the placeholders still open. An existing file is kept unless --force.

backup and apply need the owner's autocode CLI. A script in an agent's shell can call only `autocode me`, `fire
me` and `send`, so there both stop with exit 3 and say so: the owner runs them in a terminal, or the agent sets
its own job with the autocode_me tool from `show NAME --tool`.

Exit codes: 0 done, 1 the autocode command failed or a job read back differs from its file, 2 bad usage or a bad
job file (unknown name, not JSON, a required field missing, a placeholder left), 3 the owner's CLI is needed.
Stdlib only.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

NAME = "kaggle_jobs"
EXIT_FAILED, EXIT_USAGE, EXIT_OWNER = 1, 2, 3
# a job file's fields, in the order they are written; the first five are needed to set a job
FIELDS = ("name", "host", "model", "variant", "prompt", "trigger", "to", "description", "timeout", "run_usd",
          "keep_session")
REQUIRED = ("name", "host", "model", "prompt", "trigger")
OPTIONAL = ("opencode_agent",)
COMPARED = tuple(f for f in FIELDS + OPTIONAL if f != "name")
FILE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
JOB_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")
PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")
AGENT_SHELL = "agent's shell"
OWNER_HELP = ("needs the owner's autocode CLI: a script in an agent's shell can call only `autocode me`, `fire me` "
              "and `send`. The owner runs it in a terminal; an agent sets its own job with the autocode_me tool "
              "(`kaggle_jobs.py show NAME --tool` prints the arguments)")


class Stop(Exception):
    def __init__(self, code, text):
        super().__init__(text)
        self.code = code


class NoServer(Exception):
    """The server's jobs cannot be read here: in an agent's shell (owner), or for another reason."""

    def __init__(self, text, owner=False):
        super().__init__(text)
        self.owner = owner


# ---- the project and its pack

def is_pack(d):
    # an ai-pack manifest carries framework_version and a project object; a plugin's has neither
    try:
        m = json.loads((Path(d) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(m, dict) and "framework_version" in m and isinstance(m.get("project"), dict)


def pack_of(d):
    """The ai-pack folder of d: its one direct child folder holding an ai-pack manifest.json, else None."""
    try:
        packs = sorted(c for c in Path(d).iterdir() if not c.name.startswith(".") and is_pack(c))
    except OSError:
        return None
    if len(packs) > 1:
        raise Stop(EXIT_USAGE, f"{d}: more than one ai-pack ({', '.join(p.name for p in packs)})")
    return packs[0] if packs else None


def walk_up(d):
    """d and the folders above it, stopping before the home folder, any folder above it, or /."""
    home = Path(os.path.expanduser("~"))
    homes = (home, home.resolve()) if home.is_absolute() else ()
    chain = []
    for f in (d, *d.parents):
        if f.parent == f or any(f == h or f in h.parents for h in homes):
            break
        chain.append(f)
    return chain


def find_root(start=None):
    """The project root (a folder with an ai-pack) at or above start, else a copied overlay's project, else None."""
    for d in walk_up(Path(start or os.getcwd()).resolve()):
        if pack_of(d):
            return d
    if start:
        return None
    # a copied overlay sits at <project>/<pack>/plugins/kaggle/tools/kaggle_jobs.py
    up = Path(__file__).resolve().parents
    if len(up) > 4 and up[2].name == "plugins" and walk_up(up[4]) and is_pack(up[3]) and pack_of(up[4]) == up[3]:
        return up[4]
    return None


def pack_setting(pack, key):
    """A pack setting: <pack>/.memory/config.json wins over <pack>/defaults.json; None where neither sets it."""
    for f in (pack / ".memory" / "config.json", pack / "defaults.json"):
        try:
            cfg = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(cfg, dict) and key in cfg:
            return cfg[key]
    return None


def write_atomic(path, text):
    """Write a file whole: a temp file beside it, then a rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ---- a definition, in one form whatever its source

def blank(v):
    return v is None or (isinstance(v, str) and not v.strip())


def number(v):
    """A dollar cap as a number; None when there is none."""
    if blank(v) or isinstance(v, bool):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return v
    return int(n) if n == int(n) else n


def definition(job):
    """A job as a job file holds it: the fields in order, empty ones as "", the cap as a number."""
    out = {}
    for f in FIELDS + OPTIONAL:
        v = job.get(f)
        if f == "keep_session":
            # the server speaks of the opposite: delete_session, true unless the job keeps its sessions
            v = bool(v) if "keep_session" in job else not job.get("delete_session", True)
        elif f == "run_usd":
            v = number(v)
        elif f == "opencode_agent":
            v = v if not blank(v) else job.get("agent")
            if blank(v):
                continue
        elif blank(v):
            v = ""
        out[f] = v
    return out


def differences(job, other, exact=False):
    """The fields in which a job file differs from another definition. A field the file leaves empty is not
    compared unless exact: it leaves the choice to the server."""
    a, b = definition(job), definition(other)
    return [f for f in COMPARED if a.get(f) != b.get(f) and (exact or f == "keep_session" or not blank(a.get(f)))]


def placeholders(job):
    """{placeholder: [fields]} still open in a definition."""
    found = {}
    for f, v in job.items():
        for key in PLACEHOLDER_RE.findall(v) if isinstance(v, str) else ():
            found.setdefault(key, []).append(f)
    return found


def short(name):
    return str(name).rsplit(":", 1)[-1]


# ---- the pack's files

def jobs_dir(pack):
    return pack / "jobs"


def read_file(path):
    try:
        job = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        raise Stop(EXIT_USAGE, f"cannot read {path}: {e.strerror or type(e).__name__}") from None
    except ValueError as e:
        raise Stop(EXIT_USAGE, f"{path} is not JSON ({e})") from None
    if not isinstance(job, dict):
        raise Stop(EXIT_USAGE, f"{path} is not a JSON object")
    return {k: v for k, v in job.items() if not str(k).startswith("_")}


def pack_files(pack):
    """{file name without .json: definition} of the pack's job files; a file that cannot be read stops the run."""
    d = jobs_dir(pack)
    return {p.stem: definition(read_file(p)) for p in sorted(d.glob("*.json"))} if d.is_dir() else {}


def write_file(path, job):
    write_atomic(path, json.dumps(definition(job), indent=1, ensure_ascii=False) + "\n")


def find_file(files, name):
    """The file a NAME argument means: by file name, else by the job's name, else by the name's last part."""
    if name in files:
        return name
    for test in (lambda j: j["name"] == name, lambda j: short(j["name"]) == name):
        hits = [stem for stem, job in files.items() if test(job)]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise Stop(EXIT_USAGE, f"{name} names more than one job file: {', '.join(hits)}")
    raise Stop(EXIT_USAGE, f"no job file for {name} (the pack has: {', '.join(files) or 'none'})")


# ---- the server's jobs

def find_autocode(given):
    path = given or shutil.which("autocode") or os.path.expanduser("~/.autocode/bin/autocode")
    if not Path(path).is_file():
        raise NoServer(f"the autocode command is not there: {path} (pass --autocode)")
    return str(path)


def autocode(cmd, *args):
    try:
        p = subprocess.run([cmd, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=120)
    except subprocess.TimeoutExpired:
        return 124, "", "timed out after 120 s"
    except OSError as e:
        return 126, "", f"cannot run {cmd}: {e.strerror or type(e).__name__}"
    return p.returncode, p.stdout, p.stderr


def tail(text, n=300):
    return " ".join(str(text or "").strip()[-n:].split()) or "no output"


def server_jobs(given):
    """{name: job} of every job on the server, from `autocode job list --json`."""
    cmd = find_autocode(given)
    code, out, err = autocode(cmd, "job", "list", "--json")
    if code != 0:
        if AGENT_SHELL in out + err:
            raise NoServer("this is an agent's shell: its autocode CLI has only me, fire and send", owner=True)
        raise NoServer(f"`autocode job list --json` failed (exit {code}): {tail(err or out)}")
    try:
        data = json.loads(out[min(i for i in (out.find("["), out.find("{")) if i >= 0):])
    except ValueError:
        raise NoServer("`autocode job list --json` gave no JSON") from None
    if isinstance(data, dict):
        data = data["jobs"] if isinstance(data.get("jobs"), list) else [
            {"name": k, **v} for k, v in data.items() if isinstance(v, dict)]
    return {str(j["name"]): j for j in data if isinstance(j, dict) and j.get("name")}


def here(job, root):
    """Whether a server job runs in this project's folder."""
    d = job.get("directory")
    try:
        return bool(d) and Path(str(d)).resolve() == root
    except OSError:
        return False


def match(name, jobs, root):
    """The server job a pack file's name means: the same name, else the one job of this project ending :<name>."""
    if name in jobs:
        return name
    if ":" not in name:
        hits = [n for n, j in jobs.items() if n.endswith(f":{name}") and here(j, root)]
        if len(hits) == 1:
            return hits[0]
    return None


def owner_only(what, e):
    if e.owner:
        raise Stop(EXIT_OWNER, f"{what} {OWNER_HELP}")
    raise Stop(EXIT_FAILED, f"{what} cannot read the server's jobs: {e}")


# ---- the commands

def cmd_list(a, root, pack):
    files = pack_files(pack)
    try:
        jobs, note = server_jobs(a.autocode), None
    except NoServer as e:
        jobs, note = None, str(e)
    rows, seen = [], set()
    for stem, job in files.items():
        row = {"file": stem, "name": job["name"], "state": "not compared", "differs": [], "trigger": job["trigger"],
               "next": None, "last_run": None}
        open_keys = sorted(placeholders(job))
        if jobs is not None:
            on = match(job["name"], jobs, root)
            if on is None:
                row["state"] = "pack only"
            else:
                seen.add(on)
                row["differs"] = differences(job, jobs[on])
                row["state"] = "differs" if row["differs"] else "same"
                row.update(name=on, next=jobs[on].get("next"),
                           last_run=(jobs[on].get("last_run") or {}).get("state"))
        if open_keys:
            row["placeholders"] = open_keys
        rows.append(row)
    for name, job in sorted((jobs or {}).items()):
        if name not in seen and here(job, root):
            rows.append({"file": None, "name": name, "state": "server only", "differs": [],
                         "trigger": job.get("trigger"), "next": job.get("next"),
                         "last_run": (job.get("last_run") or {}).get("state")})
    where = f"{pack.name}/jobs"
    if a.json:
        print(json.dumps({"pack": where, "server": "read" if jobs is not None else f"not read: {note}",
                          "jobs": rows}, indent=1))
        return 0
    if jobs is None:
        print(f"{len(files)} job file(s) in {where}; the server's jobs were not read: {note}")
    else:
        print(f"{len(files)} job file(s) in {where}; {sum(here(j, root) for j in jobs.values())} job(s) on the "
              "server run in this project's folder")
    width = max([len(r["file"] or "-") for r in rows] + [4])
    for r in rows:
        state = r["state"] + (f": {', '.join(r['differs'])}" if r["differs"] else "")
        if r.get("placeholders"):
            state += f"; placeholders left: {', '.join(r['placeholders'])}"
        if r["state"] == "server only":
            state += f" (kaggle_jobs.py backup {short(r['name'])})"
        more = "".join(f"; {k.replace('_', ' ')} {r[k]}" for k in ("trigger", "next", "last_run") if r[k])
        print(f"{r['file'] or '-':<{width}}  {r['name']}  {state}{more}")
    return 0


def cmd_backup(a, root, pack):
    try:
        jobs = server_jobs(a.autocode)
    except NoServer as e:
        owner_only("backup", e)
    mine = {n: j for n, j in jobs.items() if here(j, root)}
    names = []
    for want in a.names:
        hits = [want] if want in jobs else [n for n in mine if short(n) == want] or [
            n for n in jobs if short(n) == want]
        if len(hits) != 1:
            raise Stop(EXIT_USAGE, f"{want} names more than one job: {', '.join(sorted(hits))}" if hits else
                       f"no job {want} on the server (this project's: {', '.join(sorted(mine)) or 'none'})")
        names.append(hits[0])
    names = list(dict.fromkeys(names)) if a.names else sorted(mine)
    if not names:
        print("backup: no job on the server runs in this project's folder; nothing written")
        return 0
    files = pack_files(pack)
    for name in names:
        new = definition(jobs[name])
        held = [stem for stem, job in files.items() if job["name"] == name or match(job["name"], jobs, root) == name]
        stem = held[0] if held else short(name)
        if not held and (stem in files or not FILE_RE.fullmatch(stem)):
            # that file holds another job: take the whole name
            stem = re.sub(r"[^A-Za-z0-9._-]+", ".", name).strip(".")
        path = jobs_dir(pack) / f"{stem}.json"
        was = differences(files[stem], new, exact=True) if stem in files else None
        if stem in files and not was and files[stem]["name"] == name:
            print(f"backup: {name} -> {path.relative_to(root)} (unchanged)")
            continue
        write_file(path, new)
        files[stem] = new
        print(f"backup: {name} -> {path.relative_to(root)} "
              + (f"(updated: {', '.join(was) or 'name'})" if was is not None else "(written)"))
    return 0


def checked(stem, job, path):
    """A definition that may be set: complete, and no placeholder left."""
    missing = [f for f in REQUIRED if blank(job.get(f))]
    if missing:
        raise Stop(EXIT_USAGE, f"{path} lacks {', '.join(missing)}")
    if not JOB_RE.fullmatch(job["name"]):
        raise Stop(EXIT_USAGE, f"{path}: not a job name: {job['name']!r}")
    left = placeholders(job)
    if left:
        raise Stop(EXIT_USAGE, f"{path} still has placeholders: "
                   + "; ".join(f"{{{{{k}}}}} ({', '.join(v)})" for k, v in sorted(left.items())))
    return job


def set_args(job):
    """The arguments of `autocode job set` for one definition."""
    args = ["job", "set", job["name"], "--host", job["host"], "--model", job["model"]]
    if not blank(job.get("variant")):
        args += ["--variant", job["variant"]]
    args += ["--prompt", job["prompt"], "--trigger", job["trigger"]]
    for flag, f in (("--to", "to"), ("--description", "description"), ("--timeout", "timeout")):
        if not blank(job.get(f)):
            args += [flag, str(job[f])]
    if job.get("run_usd") is not None:
        args += ["--run-usd", str(job["run_usd"])]
    if job.get("keep_session"):
        args.append("--keep-session")
    if not blank(job.get("opencode_agent")):
        args += ["--agent", job["opencode_agent"]]
    return args


def cmd_apply(a, root, pack):
    files = pack_files(pack)
    todo = [(stem, checked(stem, files[stem], jobs_dir(pack) / f"{stem}.json"))
            for stem in dict.fromkeys(find_file(files, n) for n in a.names)]
    try:
        cmd = find_autocode(a.autocode)
        server_jobs(a.autocode)
    except NoServer as e:
        owner_only("apply", e)
    worst = 0
    for stem, job in todo:
        code, out, err = autocode(cmd, *set_args(job))
        if code != 0:
            print(f"apply: {job['name']} FAILED (autocode job set, exit {code}): {tail(err or out)}")
            worst = EXIT_FAILED
            continue
        try:
            now = server_jobs(a.autocode).get(job["name"])
        except NoServer as e:
            now, out = None, str(e)
        diff = differences(job, now) if now else ["the job is not on the server"]
        if diff:
            print(f"apply: {job['name']} set, but the server differs from {pack.name}/jobs/{stem}.json in: "
                  f"{', '.join(diff)}")
            worst = EXIT_FAILED
        else:
            print(f"apply: {job['name']} set from {pack.name}/jobs/{stem}.json")
    return worst


def cmd_show(a, root, pack):
    files = pack_files(pack)
    stem = find_file(files, a.name)
    job = files[stem]
    if not a.tool:
        print(json.dumps(job, indent=1, ensure_ascii=False))
        return 0
    path = jobs_dir(pack) / f"{stem}.json"
    checked(stem, {**job, "host": job.get("host") or "-"}, path)  # an agent's own job runs in its own folder
    if job.get("run_usd") is None:
        raise Stop(EXIT_USAGE, f"{path} has no run_usd: autocode_me action=job_set needs the most a run may spend")
    call = {"action": "job_set", "job": short(job["name"]), "model": job["model"]}
    for f in ("variant", "prompt", "trigger", "run_usd", "host", "to", "timeout", "description", "opencode_agent"):
        if not blank(job.get(f)):
            call[f] = job[f]
    if job.get("keep_session"):
        call["keep_session"] = True
    print(f"{NAME}: the arguments of autocode_me for {pack.name}/jobs/{stem}.json; the server names the job "
          f"<your agent ID>:{call['job']}, and the owner may have to approve it", file=sys.stderr)
    print(json.dumps(call, indent=1, ensure_ascii=False))
    return 0


def cmd_new(a, root, pack):
    if not FILE_RE.fullmatch(a.name):
        raise Stop(EXIT_USAGE, f"not a file name: {a.name!r} (letters, digits, . _ -)")
    templates = Path(__file__).resolve().parent.parent / "jobs"
    source = templates / f"{a.template}.json"
    if not FILE_RE.fullmatch(a.template) or not source.is_file():
        known = ", ".join(sorted(p.stem for p in templates.glob("*.json"))) or "none"
        raise Stop(EXIT_USAGE, f"no template {a.template} in {templates} (known: {known})")
    # what the project itself answers: its folder, its pack, where it has this plugin's tools, the owner's zone
    tools = Path(__file__).resolve().parent
    values = {"PROJECT_DIR": str(root), "PACK": pack.name,
              "TOOLS": str(tools.relative_to(root)) if root in tools.parents else str(tools)}
    zone = pack_setting(pack, "owner.timezone")
    if isinstance(zone, str) and zone.strip():
        values["ZONE"] = zone.strip()
    given = {}
    for pair in a.set:
        key, eq, value = pair.partition("=")
        if not eq or not re.fullmatch(r"[A-Za-z0-9_]+", key):
            raise Stop(EXIT_USAGE, f"--set takes KEY=VALUE, not {pair!r}")
        given[key] = value
    values.update(given)
    path = jobs_dir(pack) / f"{a.name}.json"
    if path.exists() and not a.force:
        raise Stop(EXIT_USAGE, f"{path.relative_to(root)} is there already (--force writes it again)")
    job = {k: PLACEHOLDER_RE.sub(lambda m: values.get(m[1], m[0]), v) if isinstance(v, str) else v
           for k, v in read_file(source).items()}
    unused = sorted(set(given) - set(PLACEHOLDER_RE.findall(source.read_text())))
    if unused:
        raise Stop(EXIT_USAGE, f"the template {a.template} has no placeholder {', '.join(unused)}")
    write_file(path, job)
    left = sorted(placeholders(definition(job)))
    print(f"new: {path.relative_to(root)} written from the template {a.template}"
          + (f"; still to fill: {', '.join('{{%s}}' % k for k in left)}" if left else ""))
    return 0


def parse(argv):
    p = argparse.ArgumentParser(prog="kaggle_jobs.py", description="The project's autocode jobs as files in the "
                                "pack (<pack>/jobs/<name>.json): list, backup, apply, show, new.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", metavar="DIR", help="the project root (default: found from the working directory)")
    common.add_argument("--autocode", metavar="PATH", help="the autocode command")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list", parents=[common], help="the pack's job files against the server's jobs")
    s.add_argument("--json", action="store_true", help="print one JSON document")
    s = sub.add_parser("backup", parents=[common], help="write the server's definitions into the pack")
    s.add_argument("names", nargs="*", metavar="NAME", help="a job (default: every job of this project's folder)")
    s = sub.add_parser("apply", parents=[common], help="set the pack's definitions on the server (the owner's step)")
    s.add_argument("names", nargs="+", metavar="NAME", help="a job file, or the job's name")
    s = sub.add_parser("show", parents=[common], help="print one definition")
    s.add_argument("name", metavar="NAME", help="a job file, or the job's name")
    s.add_argument("--tool", action="store_true", help="print the arguments of autocode_me action=job_set")
    s = sub.add_parser("new", parents=[common], help="write a job file from a template of the plugin")
    s.add_argument("name", metavar="NAME", help="the new file's name, without .json")
    s.add_argument("--template", required=True, metavar="T", help="the template's name (plan-review)")
    s.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="fill the placeholder {{KEY}}")
    s.add_argument("--force", action="store_true", help="write over an existing file")
    return p.parse_args(argv)


def main(argv=None):
    a = parse(argv)
    try:
        root = find_root(a.root)
        if root is None:
            raise Stop(EXIT_USAGE, "no project here: run it from the project root, or pass --root")
        pack = pack_of(root)
        return {"list": cmd_list, "backup": cmd_backup, "apply": cmd_apply, "show": cmd_show,
                "new": cmd_new}[a.cmd](a, root, pack)
    except Stop as e:
        print(f"{NAME}: {e}", file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
