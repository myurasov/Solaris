# rev. 2

"""hostdash: a live view of the jobs and load on the hosts in the resource-sharing inventory.

    python3 hostdash.py              full screen, refreshed every 5 s; q quits
    python3 hostdash.py -n 10        refreshed every 10 s
    python3 hostdash.py --once       one snapshot, printed (for agents and logs)

Per host: CPU load against its cores, memory, disk, and each GPU's use and memory; under it the host's jobs: the
claims in its claims folder (project/job, class, GPUs, cores, age, and `no process` once every process a claim is
tied to has gone), its tmux sessions, and its three busiest processes (the login's own with their command line,
any other login's by program name only: a command line can carry secrets). Read-only. Each host keeps one ssh
connection open between refreshes (ControlMaster, closed two minutes after its last use), so a refresh every few
seconds stays cheap. The inventory and the options are hostclaims.py's: <pack>/.memory/hosts.json plus the hosts
other projects share with this one, or --hosts FILE; --project, --local-root and --ssh work as there. Stdlib
only, Python 3.8 or newer. --once exits 4 when a host does not answer, else 0.
"""

import argparse
import importlib.util
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

UNREACHABLE = 4

# one script per host and refresh, on stdin to sh; each line starts with a key the parser knows
PROBE = r"""
export LC_ALL=C
T=; command -v timeout >/dev/null 2>&1 && T="timeout 5"
echo "ncpu=$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null)"
echo "load=$(cut -d' ' -f1 /proc/loadavg 2>/dev/null)"
awk '/^MemTotal:/{t=$2} /^MemAvailable:/{a=$2} END{if (t) print "mem=" (t - a) "/" t}' /proc/meminfo 2>/dev/null
df -Pk / 2>/dev/null | awk 'NR==2{print "disk=" $3 "/" $2}'
$T nvidia-smi --query-gpu=index,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null | sed 's/^/gpu=/'
tmux ls -F '#S' 2>/dev/null | sed 's/^/tmux=/'
# the three busiest processes but this probe and its login shell: the login's own with their command line, any
# other login's by program name only, as a command line can carry secrets
ME=$(id -u); n=0
ps -eo pid=,ppid=,uid=,pcpu=,etime=,user=,comm= --sort=-pcpu 2>/dev/null | while read -r pid ppid uid cpu age who prog; do
  if [ "$pid" = "$$" ] || [ "$pid" = "$PPID" ] || [ "$ppid" = "$$" ]; then continue; fi
  if [ "$uid" = "$ME" ] && a=$(ps -o args= -p "$pid" 2>/dev/null) && [ -n "$a" ]; then prog=$a; fi
  echo "proc=$cpu $age $who $prog"
  n=$((n + 1)); [ "$n" -lt 3 ] || break
done
python3 - "$ROOT" 2>/dev/null <<'HOSTDASH_CLAIMS'
import glob, json, os, sys
def up(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False
for p in sorted(glob.glob(os.path.join(os.path.expanduser(sys.argv[1]), "claims", "*.json"))):
    try:
        with open(p) as f:
            c = json.load(f)
        r = c.get("resources") or {}
        # the job, its watcher and any process attached with claim --pid or renew --pid
        pids = [x for x in [c.get("pid"), (c.get("launcher") or {}).get("pid")]
                + [p.get("pid") for p in c.get("pids") or [] if isinstance(p, dict)] if x]
        print("claim=" + json.dumps({"agent": c.get("agent"), "job": c.get("job"), "class": c.get("class"),
                                     "gpus": [[g.get("index"), g.get("share")] for g in r.get("gpus") or []],
                                     "cores": len(r.get("cores") or []), "created": c.get("created"),
                                     "alive": any(up(x) for x in pids) if pids else None}))
    except (OSError, ValueError, AttributeError):
        pass
HOSTDASH_CLAIMS
"""


def load_tool(name):
    # a tool module from this folder, loaded by path, so a copied install works too
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name + ".py")
    spec = importlib.util.spec_from_file_location("hostdash_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    # keep __pycache__ out of the plugin folder
    sys.dont_write_bytecode = True
    spec.loader.exec_module(mod)
    return mod


hc = load_tool("hostclaims")


def ssh_cmd(ctx, h):
    if h.get("target") == "local":
        return ["sh", "-s"]
    # one connection per host, reused by every refresh; the inventory's own options come first and win
    ctl = os.path.expanduser("~/.ssh/cm-hostdash-%C")
    return ctx.ssh + hc.expand_opts(h.get("opts") or []) + [
        "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ControlMaster=auto", "-o", "ControlPath=" + ctl,
        "-o", "ControlPersist=120", h["target"], "sh -s"]


def parse(out, name):
    r = {"name": name, "gpus": [], "tmux": [], "procs": [], "claims": []}
    for line in out.splitlines():
        key, _, val = line.partition("=")
        if key in ("ncpu", "load", "mem", "disk"):
            r[key] = val.strip()
        elif key == "gpu":
            r["gpus"].append([v.strip() for v in val.split(",")])
        elif key == "tmux" and val.strip():
            r["tmux"].append(val.strip())
        elif key == "proc" and val.strip():
            r["procs"].append(" ".join(val.split()))
        elif key == "claim":
            try:
                r["claims"].append(json.loads(val))
            except ValueError:
                pass
    return r


def probe(ctx, h, timeout):
    cmd = ssh_cmd(ctx, h)
    script = "ROOT=%s\n%s" % (shlex.quote(h.get("root") or hc.DEFAULT_ROOT), PROBE)
    try:
        out = subprocess.run(cmd, input=script, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             universal_newlines=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"name": h["name"], "error": "no answer within %g s" % timeout}
    except OSError as e:
        return {"name": h["name"], "error": "cannot run %s: %s" % (cmd[0], e)}
    if out.returncode != 0 and not out.stdout.strip():
        return {"name": h["name"], "error": (out.stderr.strip().splitlines() or ["ssh failed"])[-1][:100]}
    return parse(out.stdout, h["name"])


def gib(pair):
    # "used/total" in KiB -> ("used/totalG", fraction used)
    try:
        used, total = (int(x) for x in pair.split("/"))
        return "%.0f/%.0fG" % (used / 1048576.0, total / 1048576.0), used / float(total)
    except (AttributeError, ValueError, ZeroDivisionError):
        return "?", 0.0


def gpu_text(g):
    # index, use %, memory used and total in MiB ([N/A] on GPUs that share the host's memory)
    if len(g) != 4:
        return None
    try:
        mem = "%d/%dG" % (int(g[2]) // 1024, int(g[3]) // 1024)
    except ValueError:
        mem = "shared mem"
    return "#%s %3s%% %s" % (g[0], g[1], mem)


def claim_text(c, now):
    gpus = ",".join(str(i) if s is None or s >= 1 else "%s:%g" % (i, s) for i, s in c.get("gpus") or [])
    parts = ["%s/%s" % (c.get("agent"), c.get("job")), c.get("class") or "?"]
    if gpus:
        parts.append("GPU " + gpus)
    parts.append("%s cores" % c.get("cores", 0))
    try:
        parts.append(hc.fmt_age(now - hc.parse_iso(c["created"])))
    except (KeyError, TypeError, ValueError):
        pass
    if c.get("alive") is False:
        parts.append("(no process)")
    return " ".join(parts)


class Collector(object):
    """Refreshes the hosts in a background thread so the screen stays responsive."""

    def __init__(self, ctx, hosts, interval, timeout):
        self.ctx, self.hosts, self.interval, self.timeout = ctx, hosts, interval, timeout
        self.data, self.updated = [], None
        self.lock = threading.Lock()
        self.stop = threading.Event()

    def refresh(self):
        with ThreadPoolExecutor(max_workers=max(1, min(16, len(self.hosts)))) as ex:
            data = list(ex.map(lambda h: probe(self.ctx, h, self.timeout), self.hosts))
        with self.lock:
            self.data, self.updated = data, time.strftime("%H:%M:%S")

    def run(self):
        def loop():
            while not self.stop.is_set():
                self.refresh()
                self.stop.wait(self.interval)

        threading.Thread(target=loop, daemon=True).start()


def render_lines(c, width):
    """The screen as (text, level) lines; level 0 normal, 1 warm, 2 hot, 3 header."""
    with c.lock:
        hosts, upd = list(c.data), c.updated
    now = time.time()
    out = [("hostdash   hosts updated %s   this machine's load %.1f   q quits" % (upd or "...", os.getloadavg()[0]), 3),
           ("", 0), ("%-16s%12s%12s%8s  GPU use / memory" % ("HOST", "CPU", "MEM", "DISK"), 3)]
    for h in hosts:
        if "error" in h:
            out.append(("%-16s  unreachable: %s" % (h["name"], h["error"]), 2))
            continue
        try:
            ncpu = max(int(h.get("ncpu") or 1), 1)
            load = float(h.get("load") or 0)
            cpu_txt = "%.1f/%d" % (load, ncpu)
        except ValueError:
            ncpu, load, cpu_txt = 1, 0.0, "?"
        mem, mem_frac = gib(h.get("mem"))
        _, disk_frac = gib(h.get("disk"))
        gpus = "  ".join(t for t in (gpu_text(g) for g in h["gpus"]) if t) or "no GPU"
        hot = max(load / ncpu, mem_frac)
        out.append(("%-16s%12s%12s%7.0f%%  %s" % (h["name"], cpu_txt, mem, disk_frac * 100, gpus),
                    2 if hot > 0.9 else 1 if hot > 0.6 else 0))
        if h["claims"]:
            out.append(("  claims: " + "; ".join(claim_text(x, now) for x in h["claims"]), 0))
        if h["tmux"]:
            out.append(("  tmux: " + ", ".join(h["tmux"]), 0))
        for p in h["procs"]:
            out.append(("  " + p, 0))
    return [(t[:max(1, width - 1)], lvl) for t, lvl in out]


def draw(stdscr, c):
    import curses
    curses.curs_set(0)
    stdscr.timeout(300)
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_YELLOW, -1)
        curses.init_pair(2, curses.COLOR_RED, -1)
        curses.init_pair(3, curses.COLOR_CYAN, -1)
    while True:
        rows, cols = stdscr.getmaxyx()
        stdscr.erase()
        for i, (text, level) in enumerate(render_lines(c, cols)[:rows]):
            attr = curses.color_pair(level) if curses.has_colors() and level else 0
            if level == 3:
                attr |= curses.A_BOLD
            try:
                stdscr.addstr(i, 0, text, attr)
            except curses.error:
                pass
        stdscr.refresh()
        if stdscr.getch() in (ord("q"), ord("Q")):
            return


def build_parser():
    ap = argparse.ArgumentParser(prog="hostdash.py", description="Live view of the jobs and load on the hosts in the "
                                 "resource-sharing inventory. See the resource-sharing skill.")
    ap.add_argument("-n", "--interval", type=float, default=5.0, help="seconds between refreshes (default 5)")
    ap.add_argument("--once", action="store_true", help="print one snapshot and exit")
    ap.add_argument("--host", action="append", help="inventory host (repeat; default: every host)")
    ap.add_argument("--timeout", type=float, default=10.0, help="seconds before a host counts as unreachable "
                    "(default 10)")
    ap.add_argument("--hosts", help="use exactly this inventory JSON [{name, target, opts}] (or HOSTCLAIMS_HOSTS)")
    ap.add_argument("--project", help="project folder to act for (default: found from here)")
    ap.add_argument("--local-root", help="show this machine, with the claims folder DIR (no ssh)")
    ap.add_argument("--ssh", help="ssh program (default ssh, or HOSTCLAIMS_SSH)")
    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    if a.interval <= 0 or a.timeout <= 0:
        ap.error("--interval and --timeout must be positive")
    try:
        ctx = hc.Ctx(a)
        hosts = ctx.hosts(a.host)
    except (hc.Fail, ValueError) as e:
        print("hostdash: %s" % e, file=sys.stderr)
        return getattr(e, "code", 2)
    c = Collector(ctx, hosts, a.interval, a.timeout)
    if a.once:
        c.refresh()
        width = os.get_terminal_size().columns if sys.stdout.isatty() else 160
        for text, _ in render_lines(c, width):
            print(text)
        for n in ctx.notes:
            print("note: " + n)
        return UNREACHABLE if any("error" in h for h in c.data) else 0
    # only the full-screen view needs curses, so --once runs where Python lacks it
    import curses
    c.run()
    try:
        curses.wrapper(draw, c)
    except KeyboardInterrupt:
        pass
    finally:
        c.stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
