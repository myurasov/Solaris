# rev. 1

"""hosthealth: GPU host health checks over the resource-sharing host inventory.

Runs on the controller and checks each host over ssh (a shell script on stdin to `sh -s`), read-only unless --fix:

    python3 hosthealth.py [--host H ...] [--fix] [--json]

Per host: kernel, load, free memory and disk, the CPU frequency governor, NVIDIA Xid codes in the kernel log since
boot (the application-class codes 13, 31, 43 and 45 are left out), GPUs that failed to start, and per GPU its
persistence mode, MIG and compute modes, power limit against its default, clocks and active clock-event reasons
(throttling), temperature, use, memory and uncorrected ECC errors. A host whose inventory entry counts its GPUs
(`gpus` such as "2x H200 NVL", a list or a number) must show that many: a GPU in confidential-computing mode, off
the bus or failing to start is missing from the driver's list.

--fix first applies the settings that are safe under running jobs and that a reboot resets (a periodic check
re-applies them): persistence mode on, each GPU's power limit back up to its default (never above it: a maximum
over the default can overload a power supply the GPUs share), and the `performance` CPU governor. It acts as the
calling project, and only as far as the sharing rules allow: on a host the project owns on record (the host's own
record, else an owner its inventory entry names; a host listed with no owner is nobody's here), on every GPU and
core (and it enables the persistence daemon); on a host where it only holds live claims, on the cores of those
claims and on the GPUs they hold that nothing else uses (a GPU another claim or work outside the claims also uses,
and a core whose frequency policy also covers other cores, are left to the owner); elsewhere not at all. Only
--fix on a host the project owns uses passwordless sudo (a login other than root), to fix and to read the kernel
log: on another's host a sudo attempt lands in the owner's security log, so a guest, and any run without --fix
(which does not look up ownership), reads and changes only what the login may. It reads ownership and claims
through hostclaims.py (python3 on the host). Everything else is only reported: the owner repairs it (driver,
reboot, GPU reset, modes), and a guest asks the owner with `hostclaims.py request --type maintenance`.

The inventory and the options are hostclaims.py's: <pack>/.memory/hosts.json plus the hosts other projects share
with this one, or --hosts FILE; --project, --local-root, --ssh, --agent and --as-owner work as there. Stdlib only,
Python 3.8 or newer. Exit codes: 0 every host healthy, 1 error, 2 bad usage, 3 problems found, 4 a host
unreachable, 5 refused by a sharing rule (--fix acting as another project).
"""

import argparse
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

OK, ERROR, USAGE, PROBLEMS, UNREACHABLE, DENIED = 0, 1, 2, 3, 4, 5
# raised by application faults or killed processes, not by the host
APP_XIDS = (13, 31, 43, 45)
# clock-event reasons that mean hardware or heat holds a GPU back (the power cap, 0x4, is normal at the limit)
THROTTLE = ((0x8, "hardware slowdown"), (0x20, "software thermal slowdown"), (0x40, "hardware thermal slowdown"),
            (0x80, "power brake"))
FIELDS = ("index", "name", "persistence_mode", "mig.mode.current", "power.limit", "power.default_limit", "clocks.sm",
          "clocks.max.sm", "clocks_event_reasons.active", "temperature.gpu", "utilization.gpu", "memory.used",
          "memory.total", "ecc.errors.uncorrected.volatile.total", "compute_mode")
NA = ("", "N/A", "[N/A]", "Not Supported", "[Not Supported]")
CPU_SYS = "/sys/devices/system/cpu"

# read-only; one KEY|value line per fact. Older drivers name the clock-event reasons clocks_throttle_reasons.
PROBE = r'''
echo "KERNEL|$(uname -r)"
echo "CPUS|$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null)"
echo "LOAD|$(cut -d' ' -f1 /proc/loadavg 2>/dev/null)"
echo "MEM|$(awk '/^MemAvailable:/{print $2}' /proc/meminfo 2>/dev/null)"
echo "DISK|$(df -Pk / 2>/dev/null | awk 'NR==2{print $4}')"
echo "GOV|$(cat "$CPU"/cpu[0-9]*/cpufreq/scaling_governor 2>/dev/null | sort | uniq -c | awk '{printf "%s:%s ", $2, $1}')"
if dmesg >/dev/null 2>&1; then D=dmesg; elif [ -n "$SUDO" ] && $SUDO dmesg >/dev/null 2>&1; then D="$SUDO dmesg"; else D=; fi
if [ -n "$D" ]; then
  echo "XID|$($D 2>/dev/null | grep -o 'NVRM: Xid ([^)]*): [0-9]*' | awk '{print $NF}' | sort -n | uniq -c | awk '{printf "%s:%s ", $2, $1}')"
  echo "INIT|$($D 2>/dev/null | grep -c 'RmInitAdapter failed')"
else
  echo "XID|?"
fi
if command -v nvidia-smi >/dev/null 2>&1; then
  Q=@FIELDS@
  out=$($T nvidia-smi --query-gpu="$Q" --format=csv,noheader,nounits 2>&1); rc=$?
  case "$out" in *"valid field"*)
    out=$($T nvidia-smi --query-gpu="$(echo "$Q" | sed s/clocks_event_reasons/clocks_throttle_reasons/)" --format=csv,noheader,nounits 2>&1); rc=$?;;
  esac
  [ "$rc" = 124 ] && out="nvidia-smi did not answer within 60 s (a GPU may be hung)"
  printf '%s\n' "$out" | sed 's/^/G|/'
else
  echo "NOSMI|"
fi
'''

# the safe settings, limited to GPUS and CORES ("all" for the owner); one F|what line per change. No GPU is
# touched when the driver does not answer (the probe reports it).
FIX = r'''
if command -v nvidia-smi >/dev/null 2>&1 && idx=$($T nvidia-smi --query-gpu=index --format=csv,noheader 2>/dev/null); then
  [ "$GPUS" = all ] && GPUS=$idx
  for i in $GPUS; do
    if [ "$($T nvidia-smi -i "$i" --query-gpu=persistence_mode --format=csv,noheader 2>/dev/null)" = Disabled ]; then
      if $T $SUDO nvidia-smi -i "$i" -pm 1 >/dev/null 2>&1; then echo "F|GPU $i persistence mode on"; else echo "F|GPU $i persistence mode: not permitted"; fi
    fi
    # back up to the default limit, never above: a maximum over the default can overload a shared power supply
    pl=$($T nvidia-smi -i "$i" --query-gpu=power.limit,power.default_limit --format=csv,noheader,nounits 2>/dev/null | tr -d ' ')
    cur=${pl%%,*}; dflt=${pl#*,}
    case "$cur,$dflt" in *[!0-9.,]*|,*|*,) continue;; esac
    if [ "${cur%.*}" -lt "${dflt%.*}" ]; then
      if $T $SUDO nvidia-smi -i "$i" -pl "$dflt" >/dev/null 2>&1; then echo "F|GPU $i power limit $cur -> $dflt W"; else echo "F|GPU $i power limit: not permitted"; fi
    fi
  done
  if [ "$DAEMON" = 1 ] && command -v systemctl >/dev/null 2>&1 && systemctl cat nvidia-persistenced.service >/dev/null 2>&1 &&
     ! systemctl is-active --quiet nvidia-persistenced.service; then
    if $SUDO systemctl enable --now nvidia-persistenced.service >/dev/null 2>&1; then echo "F|nvidia-persistenced enabled"; else echo "F|nvidia-persistenced: not permitted"; fi
  fi
fi
nset=0; nfail=0; nleft=0
for g in "$CPU"/cpu[0-9]*/cpufreq/scaling_governor; do
  [ -f "$g" ] || continue
  d=${g%/scaling_governor}; c=${d%/cpufreq}; c=${c##*/cpu}
  if [ "$CORES" != all ]; then
    case " $CORES " in *" $c "*) ;; *) continue;; esac
    # a frequency policy shared with other cores would change them too
    if [ "$(cat "$d/related_cpus" 2>/dev/null)" != "$c" ]; then nleft=$((nleft + 1)); continue; fi
  fi
  [ "$(cat "$g" 2>/dev/null)" = performance ] && continue
  grep -qw performance "$d/scaling_available_governors" 2>/dev/null || continue
  if { [ -w "$g" ] && echo performance > "$g"; } 2>/dev/null || { [ -n "$SUDO" ] && echo performance | $SUDO tee "$g" >/dev/null 2>&1; }; then
    nset=$((nset + 1))
  else
    nfail=$((nfail + 1))
  fi
done
[ $nset -gt 0 ] && echo "F|CPU governor performance on $nset cores"
[ $nfail -gt 0 ] && echo "F|CPU governor: not permitted on $nfail cores"
[ $nleft -gt 0 ] && echo "F|CPU governor: $nleft claimed cores share a frequency policy with other cores: left to the owner"
true
'''


def load_tool(name):
    # a tool module from this folder, loaded by path, so a copied install works too
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name + ".py")
    spec = importlib.util.spec_from_file_location("hosthealth_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    # keep __pycache__ out of the plugin folder
    sys.dont_write_bytecode = True
    spec.loader.exec_module(mod)
    return mod


hc = load_tool("hostclaims")


def host_script(scope=None, sudo=True, cpu=CPU_SYS):
    # what one ssh call runs: the fixes the scope allows (none without one), then the probe. SUDO runs the
    # privileged steps and reads the kernel log for a login other than root, and only in the owner's scope: on
    # another's host a sudo attempt lands in the owner's security log. T bounds nvidia-smi, which can hang on a GPU
    # off the bus.
    whole = bool(scope) and scope.get("scope") == "host"
    s = ("export LC_ALL=C\nSUDO=\n" + ('[ "$(id -u)" = 0 ] || SUDO="sudo -n"\n' if sudo and whole else "")
         + 'T=\ncommand -v timeout >/dev/null 2>&1 && T="timeout 60"\nCPU=%s\n' % shlex.quote(cpu))
    if scope and scope.get("scope"):
        gpus = "all" if whole else " ".join(str(int(i)) for i in scope.get("gpus") or [])
        cores = "all" if whole else " ".join(str(int(c)) for c in scope.get("cores") or [])
        s += "GPUS=%s\nCORES=%s\nDAEMON=%d\n%s" % (shlex.quote(gpus), shlex.quote(cores), 1 if whole else 0, FIX)
    return s + PROBE.replace("@FIELDS@", ",".join(FIELDS))


def run_script(ctx, h, script, timeout):
    # (output, error, code): the script runs in sh on the host over ssh, or here for the local target
    if h.get("target") == "local":
        cmd = ["sh", "-s"]
    else:
        cmd = ctx.ssh + hc.expand_opts(h.get("opts") or []) + [
            "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", h["target"], "sh -s"]
    try:
        r = subprocess.run(cmd, input=script.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return "", "no answer within %d s" % timeout, UNREACHABLE
    except OSError as e:
        return "", "cannot run %s: %s" % (cmd[0], e), ERROR
    out = r.stdout.decode("utf-8", "replace")
    if "\nKERNEL|" in "\n" + out:
        return out, None, OK
    err = (r.stderr.decode("utf-8", "replace").strip() or out.strip())[-300:] or "no output (exit %d)" % r.returncode
    return out, err, UNREACHABLE if r.returncode == 255 else ERROR


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def kb_to_gb(s):
    v = num(s)
    return None if v is None else v / 1048576.0


def counts(text):
    # "performance:70 ondemand:2 " -> {"performance": 70, "ondemand": 2}
    out = {}
    for part in text.split():
        k, _, n = part.rpartition(":")
        if k and n.isdigit():
            out[k] = out.get(k, 0) + int(n)
    return out


def want_gpus(v):
    # the GPU count an inventory entry names: "2x H200 NVL", a list of GPUs, or a number; None when it names none
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, (list, tuple)):
        return len(v)
    m = re.match(r"\s*(\d+)\s*x", str(v or ""), re.I)
    return int(m.group(1)) if m else None


def parse_gpu(line):
    f = [x.strip() for x in line.split(",")]
    n = len(FIELDS) - 2
    if len(f) < len(FIELDS) or not f[0].isdigit():
        return None
    # a name may hold commas: the fields after it are counted from the end
    pm, mig, pl, pdef, sm, smax, ev, temp, util, mu, mt, ecc, cm = f[len(f) - n:]
    mu, mt = num(mu), num(mt)
    return {"index": int(f[0]), "name": ",".join(f[1:len(f) - n]), "persistence": pm, "mig": mig,
            "power_limit_w": num(pl), "power_default_w": num(pdef), "sm_mhz": num(sm), "sm_max_mhz": num(smax),
            "reasons": ev, "temp_c": num(temp), "util_pct": num(util),
            "mem_used_gb": None if mu is None else mu / 1024.0, "mem_total_gb": None if mt is None else mt / 1024.0,
            "ecc_uncorrected": ecc, "compute_mode": cm}


def gpu_problems(g):
    i, out = g["index"], []
    if g["persistence"] == "Disabled":
        out.append("GPU %d persistence mode off" % i)
    if g["mig"] not in NA and g["mig"] != "Disabled":
        out.append("GPU %d MIG mode %s" % (i, g["mig"]))
    if g["compute_mode"] not in NA and g["compute_mode"] != "Default":
        out.append("GPU %d compute mode %s (jobs sharing a GPU need Default)" % (i, g["compute_mode"]))
    # only a limit below the default: one above it was raised on purpose, and the maximum is no target
    pl, pdef = g["power_limit_w"], g["power_default_w"]
    if pl is not None and pdef is not None and pl < pdef - 1:
        out.append("GPU %d power limit %.0f W below its %.0f W default" % (i, pl, pdef))
    try:
        bits = int(g["reasons"], 16)
    except ValueError:
        bits = 0
    hits = [name for bit, name in THROTTLE if bits & bit]
    if hits:
        out.append("GPU %d held back: %s" % (i, ", ".join(hits)))
    if g["ecc_uncorrected"] not in NA and g["ecc_uncorrected"] != "0":
        out.append("GPU %d uncorrected ECC errors: %s" % (i, g["ecc_uncorrected"]))
    return out


def assess(out, want):
    # the probe's lines -> facts, problems (what needs a repair) and notes
    rec = {"kernel": None, "cpus": None, "load": None, "mem_free_gb": None, "disk_free_gb": None, "governors": {},
           "xids": None, "gpu_start_failures": 0, "gpus": [], "gpus_expected": want, "nvidia_smi": True,
           "smi_errors": [], "fixed": [], "problems": [], "notes": []}
    for line in out.splitlines():
        key, sep, val = line.partition("|")
        if not sep:
            continue
        val = val.strip()
        if key == "KERNEL":
            rec["kernel"] = val or None
        elif key == "CPUS":
            rec["cpus"] = int(val) if val.isdigit() else None
        elif key == "LOAD":
            rec["load"] = num(val)
        elif key == "MEM":
            rec["mem_free_gb"] = kb_to_gb(val)
        elif key == "DISK":
            rec["disk_free_gb"] = kb_to_gb(val)
        elif key == "GOV":
            rec["governors"] = counts(val)
        elif key == "XID":
            rec["xids"] = None if val == "?" else dict((int(k), n) for k, n in counts(val).items() if k.isdigit())
        elif key == "INIT":
            rec["gpu_start_failures"] = int(val) if val.isdigit() else 0
        elif key == "G":
            g = parse_gpu(val)
            if g:
                rec["gpus"].append(g)
            elif val:
                rec["smi_errors"].append(val)
        elif key == "NOSMI":
            rec["nvidia_smi"] = False
        elif key == "F":
            rec["fixed"].append(val)
    probs, notes, seen = rec["problems"], rec["notes"], len(rec["gpus"])
    if not rec["nvidia_smi"]:
        if want:
            probs.append("no nvidia-smi: the NVIDIA driver is missing")
    else:
        if rec["smi_errors"]:
            probs.append("nvidia-smi: " + "; ".join(rec["smi_errors"][:3])[:300])
        if want is not None and seen < want:
            probs.append("%d of %d GPUs visible" % (seen, want))
        elif want is not None and seen > want:
            notes.append("%d GPUs visible where the inventory names %d: update its gpus" % (seen, want))
    if rec["gpu_start_failures"]:
        probs.append("GPU start failed %d time(s) since boot (RmInitAdapter): confidential-computing mode on, or a "
                     "failing GPU" % rec["gpu_start_failures"])
    if rec["xids"] is None:
        notes.append("kernel log not readable (needs root, or passwordless sudo, which only --fix on a host this "
                     "project owns uses): Xid codes unknown")
    else:
        hw = sorted((c, n) for c, n in rec["xids"].items() if c not in APP_XIDS)
        if hw:
            probs.append("Xid %s since boot (hardware or driver class: see NVIDIA's Xid catalog)"
                         % ", ".join("%d x%d" % x for x in hw))
    for g in rec["gpus"]:
        probs.extend(gpu_problems(g))
    slow = sorted((name, n) for name, n in rec["governors"].items() if name != "performance")
    if slow:
        probs.append("CPU governor " + ", ".join("%s on %d cores" % x for x in slow))
    return rec


def named_owner(h):
    # the owner an inventory entry names outright; the default one a listed host gets is no record of ownership
    return h.get("owner") if h.get("owner_explicit") else None


def fix_scope(me, h, st):
    # what --fix may change on a host, from its hostclaims status: everything on a host this project owns on record
    # (the host's own record wins, else the owner its inventory entry names); on a host it uses as a guest, the
    # cores of its live claims and the GPUs they hold that no other claim and no work outside the claims uses (a
    # GPU's settings are the whole GPU's, whatever share a claim holds); nothing elsewhere
    owner = st.get("owner") or named_owner(h)
    if owner is not None and owner == me:
        return {"scope": "host", "why": "this project owns it"}
    live = [c for c in st.get("claims") or [] if c.get("state") in ("live", "orphan")]
    mine = [c for c in live if c.get("agent") == me]
    if not mine:
        if owner is None:
            return {"scope": None, "why": "no owner on record: install hostclaims there from the owning project, or "
                                          "name the owner in its inventory entry"}
        return {"scope": None, "why": "owned by %s, and %s holds no live claim here: report problems to the owner "
                                      "with a maintenance request" % (owner, me)}
    res = [c.get("resources") or {} for c in mine]
    held = set(g["index"] for r in res for g in r.get("gpus") or [] if isinstance(g.get("index"), int))
    # GPUs another project's live claim holds or work outside the claims uses
    used = set(g.get("index") for c in live if c.get("agent") != me
               for g in (c.get("resources") or {}).get("gpus") or [])
    used.update(g.get("index") for g in (st.get("free") or {}).get("gpus") or [] if g.get("unclaimed"))
    cores = sorted(set(int(c) for r in res for c in r.get("cores") or []))
    return {"scope": "claims", "gpus": sorted(held - used), "shared_gpus": sorted(held & used), "cores": cores,
            "why": "%s: only the cores of the live claims of %s and the GPUs they alone use"
                   % ("owned by %s" % owner if owner else "no owner on record", me)}


def check_host(ctx, h, me, fix, timeout):
    rec = {"host": h["name"], "project": h.get("project"), "owner": h.get("owner"), "fix": None}
    if fix:
        # the host's own record of its owner wins; failing that, only an owner the inventory names outright counts
        st = ctx.call(dict(h, owner=named_owner(h)), {"op": "status"})
        if st.get("ok"):
            rec["owner"] = st.get("owner") or rec["owner"]
            rec["fix"] = fix_scope(me, h, st)
        else:
            rec["fix"] = {"scope": None, "why": "its owner and claims could not be read: %s" % st.get("error")}
    out, err, code = run_script(ctx, h, host_script(rec["fix"]), timeout)
    if err:
        rec.update(code=code, error=err, problems=[("unreachable: " if code == UNREACHABLE else "") + err], notes=[],
                   fixed=[ln[2:] for ln in out.splitlines() if ln.startswith("F|")])
        return rec
    rec.update(assess(out, want_gpus(h.get("gpus"))))
    rec.update(code=OK, error=None)
    return rec


def fmt(x, unit=""):
    return "?" if x is None else "%.0f%s" % (x, unit)


def gpu_text(g):
    mem = "%s/%s" % (fmt(g["mem_used_gb"]), fmt(g["mem_total_gb"], "G")) if g["mem_total_gb"] is not None else "shared mem"
    return "%d %s%% %s %s %s/%s %s/%s" % (g["index"], fmt(g["util_pct"]), mem, fmt(g["temp_c"], "C"), fmt(g["sm_mhz"]),
                                          fmt(g["sm_max_mhz"], "MHz"), fmt(g["power_limit_w"]),
                                          fmt(g["power_default_w"], "W"))


def host_lines(h, rec):
    lines = ["%s: %s" % (hc.host_label(h, {"owner": rec.get("owner")}),
                         "%d problem(s)" % len(rec["problems"]) if rec["problems"] else "ok")]
    if not rec.get("error"):
        gov = ", ".join("%s on %d cores" % x for x in sorted(rec["governors"].items())) or "n/a"
        lines.append("  kernel %s; load %s of %s cores; %s RAM and %s disk free; CPU governor %s" % (
            rec["kernel"], "?" if rec["load"] is None else "%.1f" % rec["load"], rec["cpus"] or "?",
            fmt(rec["mem_free_gb"], "G"), fmt(rec["disk_free_gb"], "G"), gov))
        if rec["gpus"]:
            names = sorted(set(g["name"] for g in rec["gpus"]))
            lines.append("  GPUs (%dx %s): %s" % (len(rec["gpus"]), " + ".join(names),
                                                  " | ".join(gpu_text(g) for g in rec["gpus"])))
    lines += ["  ! " + p for p in rec["problems"]]
    lines += ["  note: " + n for n in rec["notes"]]
    fx = rec.get("fix")
    if fx and not fx.get("scope"):
        lines.append("  fix: none here: " + fx["why"])
    elif fx and fx["scope"] == "claims":
        line = "  fix: only your claims here (GPUs %s; cores %s)" % (
            ",".join(str(i) for i in fx["gpus"]) or "none", hc.fmt_cores(fx["cores"]) or "none")
        if fx.get("shared_gpus"):
            line += "; GPUs %s also in other use: left to the owner" % ",".join(str(i) for i in fx["shared_gpus"])
        lines.append(line)
    if rec.get("fixed"):
        lines.append("  fixed: " + "; ".join(rec["fixed"]))
    return lines


def exit_code(recs):
    codes = [r["code"] for r in recs]
    if UNREACHABLE in codes:
        return UNREACHABLE
    if ERROR in codes:
        return ERROR
    return PROBLEMS if any(r["problems"] for r in recs) else OK


def build_parser():
    ap = argparse.ArgumentParser(prog="hosthealth.py", description="GPU host health over the resource-sharing host "
                                 "inventory; --fix applies the safe performance settings where the sharing rules "
                                 "allow. See the resource-sharing skill.")
    ap.add_argument("--host", action="append", help="inventory host (repeat; default: every host)")
    ap.add_argument("--fix", action="store_true", help="also set persistence mode, the default GPU power limit and "
                    "the performance CPU governor: on hosts this project owns, or on what only its live claims use")
    ap.add_argument("--json", action="store_true", help="print JSON")
    ap.add_argument("--hosts", help="use exactly this inventory JSON [{name, target, opts}] (or HOSTCLAIMS_HOSTS)")
    ap.add_argument("--project", help="project folder to act for (default: found from here)")
    ap.add_argument("--local-root", help="check this machine, with the claims folder DIR (no ssh)")
    ap.add_argument("--ssh", help="ssh program (default ssh, or HOSTCLAIMS_SSH)")
    ap.add_argument("--agent", help="who acts: default HOSTCLAIMS_AGENT, else the project's slug")
    ap.add_argument("--as-owner", action="store_true", help="outside a project: confirm that --fix acts for --agent")
    ap.add_argument("--timeout", type=float, help="seconds per host (default 120)")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        if a.timeout is not None and a.timeout <= 0:
            raise hc.Fail("--timeout must be positive", USAGE)
        ctx = hc.Ctx(a)
        hosts = ctx.hosts(a.host)
        # --fix changes hosts, so it acts as the calling project, like hostclaims' owner actions
        me = ctx.need_owner_identity() if a.fix else ctx.agent
        timeout = a.timeout or 120.0
        with ThreadPoolExecutor(max_workers=max(1, min(16, len(hosts)))) as ex:
            recs = list(ex.map(lambda h: check_host(ctx, h, me, a.fix, timeout), hosts))
    except hc.Fail as e:
        if a.json:
            print(json.dumps({"ok": False, "code": e.code, "error": str(e)}, indent=1, sort_keys=True))
        else:
            print("hosthealth: %s" % e, file=sys.stderr)
        return e.code
    except ValueError as e:
        print("hosthealth: %s" % e, file=sys.stderr)
        return USAGE
    lines = []
    for h, r in zip(hosts, recs):
        lines += host_lines(h, r)
    lines += ["note: " + n for n in ctx.notes]
    code = exit_code(recs)
    hc.emit(ctx, {"hosts": dict((r["host"], r) for r in recs), "notes": ctx.notes, "code": code}, lines)
    return code


if __name__ == "__main__":
    sys.exit(main())
