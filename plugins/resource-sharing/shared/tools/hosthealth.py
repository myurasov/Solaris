# rev. 3

"""hosthealth: GPU host health checks over the resource-sharing host inventory.

Runs on the controller and checks each host over ssh (a shell script on stdin to `sh -s`), read-only unless --fix:

    python3 hosthealth.py [--host H ...] [--fix] [--power default|max] [--write] [--json]
    python3 hosthealth.py --shared [--json]

Per host: kernel, load, free memory and disk, the CPU frequency governor, NVIDIA Xid codes in the kernel log of the
current boot (the application-class codes 13, 31, 43 and 45 are left out), GPUs that failed to start, and per GPU
its persistence mode, MIG and compute modes, power limit against its default, clocks and active clock-event reasons
(throttling), temperature, use, memory and uncorrected ECC errors. A host whose inventory entry counts its GPUs
(`gpus` such as "2x H200 NVL", a list or a number) must show that many: a GPU in confidential-computing mode, off
the bus or failing to start is missing from the driver's list. The kernel log is the journal (`journalctl -k -b
0`) where the login may read it; where journalctl is missing or denied, `dmesg`, whose ring buffer drops its
oldest lines on a busy host, so a count can fall. The output names the source. The journal keeps a boot only
within its size limits (journald's SystemMaxUse and the like): on a long boot whose oldest lines were vacuumed, a
since-boot count covers what is left. --write reads only the journal lines after where the host's last check
stopped (a cursor; one whose line was vacuumed resumes at the next line), so after its first reading it counts
every new event however old the boot, and reads little.

--write also keeps the newest health of the hosts checked in the project's <pack>/.memory/host-health.json
(atomic replace; hosts not checked keep their entries): {"schema": 1, "checked": ISO, "hosts": {name: {"health":
"ok"|"warn"|"bad", "gpus", "gpus_visible", "busy" (mean GPU use, 0-1), "xid_new", "temp_max_c", "notes",
"checked"}}}. Guests read the owner's file instead of asking for readings. With the state it keeps beside it
(host-health-state.json) it prints `TRIPPED <host> <what>` or `CLEARED <host> <what>` only when a host crosses a
threshold since the last --write: that line is what an owner sends its guests. Settings, each overridable as
"hosthealth.<key>" in <pack>/.memory/config.json (or the pack's defaults.json); 0 (false, an empty list) turns
each one off:

    temp_warn_c 85, temp_bad_c 90       the hottest GPU's temperature (C): warn, bad
    gpus_missing_warn 1,                GPUs missing against the inventory's count: warn, bad
      gpus_missing_bad 1
    xid_new_warn 1                      new hardware-class Xid events since the last check: warn
    xid_bad_codes [48, 79, 95, 154]     a new Xid of these codes (a GPU to reset or a host to reboot): bad
    throttle_warn true                  a GPU held back by hardware or thermal slowdown or the power brake: warn
    idle_busy_pct 90, idle_mem_gb 1     a GPU this busy with less memory in use, so no real job (hung, or a stray
                                        process): warn
    unreachable_bad_checks 2            bad after this many failed checks in a row; a single failure is only a
                                        note, and the entry keeps its last reading
    clear_checks 2                      a level clears after this many clean checks in a row (off: at the first)
    stale_h 3                           --shared: an owner's reading older than this is stale

A driver that fails (no nvidia-smi, or one that errs, where GPUs are expected) is bad, uncorrected ECC errors warn.
The first reading of a source only sets its Xid base, and after a reboot every Xid since boot is new. The journal
and dmesg keep a base each, with the events the other counted since, so runs that alternate between them (the
owner's sudo reads the journal, a plain run only dmesg) neither lose nor count twice an event. --shared (a guest)
reads, with no ssh, each sharing owner's health file and prints the hosts it shares with this project: health, the
reading's age, and its notes; exit 3 when one is not ok, stale or missing.

--fix first applies the settings that are safe under running jobs and that a reboot resets (a periodic check
re-applies them): persistence mode on, each GPU's power limit back up to its default (never above it: a maximum
over the default can overload a power supply the GPUs share), and the `performance` CPU governor. --power max (or
"health": {"power": "max"} in the policy block of the project's resource-sharing.json) makes the most the GPU
allows the limit the check expects and --fix sets, on a host the project owns only (the host's own record, read
for --fix, else its inventory entry): going over the default is the owner's call, so on any other host the check
and a guest's --fix hold the limit to the default as before. It acts as the
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
with this one, or --hosts FILE, or --hosts NAME[,NAME] to pick hosts from those (or from the seen list); --project,
--local-root, --ssh, --agent and --as-owner work as there. Stdlib only,
Python 3.8 or newer. Exit codes: 0 every host healthy, 1 error, 2 bad usage, 3 problems found, 4 a host
unreachable, 5 refused by a sharing rule (--fix acting as another project).
"""

import argparse
import datetime
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import time
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
XSRC_TEXT = {"journal": "journalctl -k -b 0", "dmesg": "dmesg (a ring buffer: on a busy host its oldest lines are gone)"}
# the health thresholds --write judges by; a project overrides each in <pack>/.memory/config.json (or its
# defaults.json) as "hosthealth.<key>", and 0 turns a threshold off
HEALTH = {"temp_warn_c": 85, "temp_bad_c": 90, "gpus_missing_warn": 1, "gpus_missing_bad": 1, "xid_new_warn": 1,
          "xid_bad_codes": [48, 79, 95, 154], "throttle_warn": True, "idle_busy_pct": 90, "idle_mem_gb": 1.0,
          "unreachable_bad_checks": 2, "clear_checks": 2, "stale_h": 3}
RANK = {None: 0, "warn": 1, "bad": 2}
HEALTH_FILE, STATE_FILE = "host-health.json", "host-health-state.json"
ENTRY = ("gpus", "gpus_visible", "busy", "xid_new", "temp_max_c", "checked")
KSOURCES = ("journal", "dmesg")

# read-only; one KEY|value line per fact. Older drivers name the clock-event reasons clocks_throttle_reasons.
PROBE = r'''
echo "KERNEL|$(uname -r)"
echo "CPUS|$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null)"
echo "LOAD|$(cut -d' ' -f1 /proc/loadavg 2>/dev/null)"
echo "MEM|$(awk '/^MemAvailable:/{print $2}' /proc/meminfo 2>/dev/null)"
echo "DISK|$(df -Pk / 2>/dev/null | awk 'NR==2{print $4}')"
echo "GOV|$(cat "$CPU"/cpu[0-9]*/cpufreq/scaling_governor 2>/dev/null | sort | uniq -c | awk '{printf "%s:%s ", $2, $1}')"
B=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)
echo "BOOT|$B"
# the kernel log of this boot: the journal where the login (or the owner's sudo) may read it, else dmesg, whose ring
# buffer drops its oldest lines on a busy host. A journal that answers with a hint or an error (no access, no
# journal) is passed over. With JCUR from the last check of this boot, only the journal lines after it are read and
# the counts start from JBASE and JINIT; KSTATE=1 (--write) also counts dmesg beside the journal, for its own base.
XAWK='BEGIN { m = split(base, b, " "); for (k = 1; k <= m; k++) { split(b[k], p, ":"); x[p[1]] += p[2] } i = init + 0 }
{ if (match($0, /NVRM: Xid \([^)]*\): [0-9]+/)) { n = split(substr($0, RSTART, RLENGTH), w, " "); x[w[n]]++ } }
/RmInitAdapter failed/ { i++ }
/^-- cursor: / { c = substr($0, 12) }
END { printf "%s|", tag; for (k in x) printf "%s:%d ", k, x[k]; printf "\n"; if (tag == "XID") printf "INIT|%d\n", i
  if (c != "") printf "XCUR|%s\n", c }'
jok() { e=$($1 journalctl -k -b 0 -n 0 --no-pager 2>&1 >/dev/null) && [ -z "$e" ]; }
J=; D=; X=; L=; JB=; JI=
if command -v journalctl >/dev/null 2>&1; then
  if jok ""; then J=journalctl; elif [ -n "$SUDO" ] && jok "$SUDO"; then J="$SUDO journalctl"; fi
fi
if [ -n "$J" ]; then
  if [ -n "$JCUR" ] && [ "$JBOOT" = "$B" ] && L=$($J -k -b 0 -o cat --no-pager --show-cursor --after-cursor="$JCUR" 2>/dev/null); then
    X=after; JB=$JBASE; JI=$JINIT
  elif L=$($J -k -b 0 -o cat --no-pager --show-cursor 2>/dev/null | grep -E 'NVRM: Xid|RmInitAdapter failed|^-- cursor: '); then
    X=boot
  fi
fi
if [ -z "$X" ] || [ "$KSTATE" = 1 ]; then
  if dmesg >/dev/null 2>&1; then D=dmesg; elif [ -n "$SUDO" ] && $SUDO dmesg >/dev/null 2>&1; then D="$SUDO dmesg"; fi
fi
if [ -n "$X" ]; then
  echo "XSRC|journal"
  echo "XREAD|$X"
  printf '%s\n' "$L" | awk -v tag=XID -v base="$JB" -v init="$JI" "$XAWK"
  [ "$KSTATE" = 1 ] && [ -n "$D" ] && $D 2>/dev/null | awk -v tag=XDMESG "$XAWK"
elif [ -n "$D" ]; then
  echo "XSRC|dmesg"
  $D 2>/dev/null | awk -v tag=XID "$XAWK"
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
  # the most each GPU's power limit may be set to, asked apart so a driver without the field changes nothing above
  [ "$rc" = 0 ] && $T nvidia-smi --query-gpu=index,power.max_limit --format=csv,noheader,nounits 2>/dev/null | sed 's/^/PMAX|/'
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
    # back up to the default limit, never above (a maximum over the default can overload a shared power supply),
    # unless the owner asks for the most the GPU allows (POWER=max)
    q=power.limit,power.default_limit; [ "$POWER" = max ] && q=power.limit,power.max_limit
    pl=$($T nvidia-smi -i "$i" --query-gpu=$q --format=csv,noheader,nounits 2>/dev/null | tr -d ' ')
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


def host_script(scope=None, sudo=True, cpu=CPU_SYS, power="default", kernel=None):
    # what one ssh call runs: the fixes the scope allows (none without one), then the probe. SUDO runs the
    # privileged steps and reads the kernel log for a login other than root, and only in the owner's scope: on
    # another's host a sudo attempt lands in the owner's security log. T bounds nvidia-smi, which can hang on a GPU
    # off the bus. POWER max raises power limits to the most each GPU allows, in the owner's scope only. kernel
    # (--write) is where the last journal read of the host stopped (kernel_arg), {} for none.
    whole = bool(scope) and scope.get("scope") == "host"
    s = ("export LC_ALL=C\nSUDO=\n" + ('[ "$(id -u)" = 0 ] || SUDO="sudo -n"\n' if sudo and whole else "")
         + 'T=\ncommand -v timeout >/dev/null 2>&1 && T="timeout 60"\nCPU=%s\n' % shlex.quote(cpu)
         + "KSTATE=%s\nJCUR=\nJBOOT=\nJBASE=\nJINIT=\n" % ("" if kernel is None else "1"))
    if kernel and kernel.get("cursor"):
        s += "JCUR=%s\nJBOOT=%s\nJBASE=%s\nJINIT=%d\n" % (
            shlex.quote(kernel["cursor"]), shlex.quote(kernel.get("boot") or ""),
            shlex.quote(" ".join("%d:%d" % x for x in sorted(kernel.get("base", {}).items()))), kernel.get("init") or 0)
    if scope and scope.get("scope"):
        gpus = "all" if whole else " ".join(str(int(i)) for i in scope.get("gpus") or [])
        cores = "all" if whole else " ".join(str(int(c)) for c in scope.get("cores") or [])
        s += "GPUS=%s\nCORES=%s\nDAEMON=%d\nPOWER=%s\n%s" % (shlex.quote(gpus), shlex.quote(cores), 1 if whole else 0,
                                                            "max" if whole and power == "max" else "default", FIX)
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


def held_back(g):
    # the active clock-event reasons that mean hardware or heat holds the GPU back
    try:
        bits = int(g["reasons"], 16)
    except ValueError:
        bits = 0
    return [name for bit, name in THROTTLE if bits & bit]


def gpu_problems(g, power="default"):
    i, out = g["index"], []
    if g["persistence"] == "Disabled":
        out.append("GPU %d persistence mode off" % i)
    if g["mig"] not in NA and g["mig"] != "Disabled":
        out.append("GPU %d MIG mode %s" % (i, g["mig"]))
    if g["compute_mode"] not in NA and g["compute_mode"] != "Default":
        out.append("GPU %d compute mode %s (jobs sharing a GPU need Default)" % (i, g["compute_mode"]))
    # only a limit below the target: by default the GPU's default limit (one above it was raised on purpose); with
    # power max the most the GPU allows, where the driver reports it
    pl, pdef, pmax = g["power_limit_w"], g["power_default_w"], g.get("power_max_w")
    if power == "max" and pl is not None and pmax is not None:
        if pl < pmax - 1:
            out.append("GPU %d power limit %.0f W below its %.0f W maximum" % (i, pl, pmax))
    elif pl is not None and pdef is not None and pl < pdef - 1:
        out.append("GPU %d power limit %.0f W below its %.0f W default" % (i, pl, pdef))
    hits = held_back(g)
    if hits:
        out.append("GPU %d held back: %s" % (i, ", ".join(hits)))
    if g["ecc_uncorrected"] not in NA and g["ecc_uncorrected"] != "0":
        out.append("GPU %d uncorrected ECC errors: %s" % (i, g["ecc_uncorrected"]))
    return out


def assess(out, want, power="default"):
    # the probe's lines -> facts, problems (what needs a repair) and notes; power is the limit the check expects
    rec = {"kernel": None, "cpus": None, "load": None, "mem_free_gb": None, "disk_free_gb": None, "governors": {},
           "xids": None, "xid_source": None, "xid_read": None, "xid_cursor": None, "xids_alt": {}, "boot_id": None,
           "gpu_start_failures": 0, "gpus": [],
           "gpus_expected": want, "nvidia_smi": True, "smi_errors": [], "fixed": [], "problems": [], "notes": [],
           "power": power}
    pmax = {}
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
        elif key == "BOOT":
            rec["boot_id"] = val or None
        elif key == "XSRC":
            rec["xid_source"] = val or None
        elif key == "XREAD":
            rec["xid_read"] = val or None
        elif key == "XCUR":
            rec["xid_cursor"] = val or None
        elif key == "XID":
            rec["xids"] = None if val == "?" else dict((int(k), n) for k, n in counts(val).items() if k.isdigit())
        elif key == "XDMESG":
            rec["xids_alt"]["dmesg"] = dict((int(k), n) for k, n in counts(val).items() if k.isdigit())
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
        elif key == "PMAX":
            f = [x.strip() for x in val.split(",")]
            if len(f) == 2 and f[0].isdigit() and num(f[1]) is not None:
                pmax[int(f[0])] = num(f[1])
    for g in rec["gpus"]:
        g["power_max_w"] = pmax.get(g["index"])
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
        probs.extend(gpu_problems(g, power))
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


def check_host(ctx, h, me, fix, timeout, power="default", kernel=None):
    rec = {"host": h["name"], "project": h.get("project"), "owner": h.get("owner"), "fix": None}
    if fix:
        # the host's own record of its owner wins; failing that, only an owner the inventory names outright counts
        st = ctx.call(dict(h, owner=named_owner(h)), {"op": "status"})
        if st.get("ok"):
            rec["owner"] = st.get("owner") or rec["owner"]
            rec["fix"] = fix_scope(me, h, st)
        else:
            rec["fix"] = {"scope": None, "why": "its owner and claims could not be read: %s" % st.get("error")}
    # the maximum is the owner's call: judged and set only on a host this project owns (the host's own record, read
    # for --fix, else its inventory entry); on a host it only uses, the limit is held to the default as without it
    owned = (rec["fix"] or {}).get("scope") == "host" if fix else (me is not None and h.get("owner") == me)
    judged = power if owned else "default"
    rec["power"] = judged
    out, err, code = run_script(ctx, h, host_script(rec["fix"], power=judged, kernel=kernel), timeout)
    if err:
        rec.update(code=code, error=err, problems=[("unreachable: " if code == UNREACHABLE else "") + err], notes=[],
                   fixed=[ln[2:] for ln in out.splitlines() if ln.startswith("F|")])
        return rec
    rec.update(assess(out, want_gpus(h.get("gpus")), judged))
    if fix and power == "max" and not owned:
        rec["notes"].append("power max is the owner's call (over the default a shared power supply may overload): on a "
                            "host this project does not own the limit is held to, and raised to, the default only")
    rec.update(code=OK, error=None)
    return rec


def fmt(x, unit=""):
    return "?" if x is None else "%.0f%s" % (x, unit)


def gpu_text(g):
    mem = "%s/%s" % (fmt(g["mem_used_gb"]), fmt(g["mem_total_gb"], "G")) if g["mem_total_gb"] is not None else "shared mem"
    # limit/default W, and the most it may be set to where that differs from the default
    pmax = g.get("power_max_w")
    top = " (max %s)" % fmt(pmax, "W") if pmax is not None and g["power_default_w"] is not None \
        and abs(pmax - g["power_default_w"]) >= 1 else ""
    return "%d %s%% %s %s %s/%s %s/%s%s" % (g["index"], fmt(g["util_pct"]), mem, fmt(g["temp_c"], "C"), fmt(g["sm_mhz"]),
                                            fmt(g["sm_max_mhz"], "MHz"), fmt(g["power_limit_w"]),
                                            fmt(g["power_default_w"], "W"), top)


def host_lines(h, rec):
    lines = ["%s: %s" % (hc.host_label(h, {"owner": rec.get("owner")}),
                         "%d problem(s)" % len(rec["problems"]) if rec["problems"] else "ok")]
    if not rec.get("error"):
        gov = ", ".join("%s on %d cores" % x for x in sorted(rec["governors"].items())) or "n/a"
        src = XSRC_TEXT.get(rec.get("xid_source"))
        if src and rec.get("xid_read") == "after":
            src += " (the lines since the last check, added to its counts)"
        lines.append("  kernel %s; load %s of %s cores; %s RAM and %s disk free; CPU governor %s%s" % (
            rec["kernel"], "?" if rec["load"] is None else "%.1f" % rec["load"], rec["cpus"] or "?",
            fmt(rec["mem_free_gb"], "G"), fmt(rec["disk_free_gb"], "G"), gov, "; Xid from " + src if src else ""))
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


def local_iso(t):
    return datetime.datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


def health_settings(project):
    # HEALTH, then each "hosthealth.<key>" of the pack's defaults.json, then of its .memory/config.json (which wins);
    # (settings, notes)
    th, notes = dict(HEALTH), []
    pack = os.path.join(project["root"], project["pack"])
    for path in (os.path.join(pack, "defaults.json"), os.path.join(pack, ".memory", "config.json")):
        try:
            raw = hc.read_json(path)
        except OSError as e:
            notes.append("cannot read %s: %s" % (path, e.strerror))
            continue
        if not isinstance(raw, dict):
            if raw is not None or os.path.isfile(path):
                notes.append("cannot read %s: its hosthealth settings are left out" % path)
            continue
        for k, v in sorted(raw.items()):
            if not k.startswith("hosthealth."):
                continue
            key = k[len("hosthealth."):]
            if key not in HEALTH:
                notes.append("%s: unknown setting %s left out (known: %s)" % (path, k, ", ".join(sorted(HEALTH))))
                continue
            want = HEALTH[key]
            if isinstance(want, bool):
                ok, kind = isinstance(v, bool), "true or false"
            elif isinstance(want, list):
                ok = isinstance(v, list) and all(isinstance(x, int) and not isinstance(x, bool) for x in v)
                kind = "a list of Xid codes"
            else:
                ok, kind = isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0, "a number, 0 or more"
            if not ok:
                raise hc.Fail("%s: %s must be %s, like %s (not %s)" % (path, k, kind, json.dumps(want), json.dumps(v)),
                              USAGE)
            th[key] = v
    return th, notes


def xid_counts(c, hardware=True):
    # {code: n} from a reading or the state (keys may be strings), the hardware-class codes only unless hardware=False
    out = {}
    for k, n in (c or {}).items() if isinstance(c, dict) else ():
        try:
            k, n = int(k), int(n)
        except (TypeError, ValueError):
            continue
        if n > 0 and not (hardware and k in APP_XIDS):
            out[k] = out.get(k, 0) + n
    return out


def stored(c):
    return dict((str(k), n) for k, n in sorted(c.items()))


def kernel_arg(st):
    # where the last journal read of a host stopped, for the probe to read only the lines after it; {} for none
    k = st.get("kernel") if isinstance(st.get("kernel"), dict) else {}
    j = (k.get("sources") or {}).get("journal") if isinstance(k.get("sources"), dict) else None
    if not isinstance(j, dict) or not isinstance(j.get("cursor"), str) or not j["cursor"]:
        return {}
    return {"cursor": j["cursor"], "boot": k.get("boot") if isinstance(k.get("boot"), str) else "",
            "base": xid_counts(j.get("xids"), hardware=False), "init": j["init"] if isinstance(j.get("init"), int) else 0}


def count_xids(rec, prev):
    # (new hardware-class Xid events {code: n}, or None while the kernel log is unread; the new kernel state).
    # Each source (the journal, dmesg) keeps its own base, its counts at its last reading, and what other sources
    # counted since then (pending): a reading counts its rise over both, so runs that alternate between sources
    # neither lose nor count twice an event. A first reading of a source only sets its base (another source read
    # beside it with a base of its own counts instead); after a reboot every Xid since boot is new.
    k = prev if isinstance(prev, dict) else {}
    if rec["xids"] is None:
        return None, k
    old = k.get("sources") if isinstance(k.get("sources"), dict) else {}
    old = dict((s, v) for s, v in old.items() if s in KSOURCES and isinstance(v, dict))
    p = rec["xid_source"] if rec["xid_source"] in KSOURCES else "dmesg"
    read = dict(rec.get("xids_alt") or {})
    read[p] = rec["xids"]
    boot = rec["boot_id"]
    if boot and k.get("boot") and boot != k["boot"]:
        # a source not read now starts this boot from nothing
        new, old = xid_counts(read[p]), dict((s, {"xids": {}, "pending": {}}) for s in KSOURCES)
    else:
        src = next((s for s in [p] + sorted(read) if s in read and s in old), None)
        new = {}
        if src:
            base, pend = xid_counts(old[src].get("xids")), xid_counts(old[src].get("pending"))
            for c, n in xid_counts(read[src]).items():
                if n - base.get(c, 0) - pend.get(c, 0) > 0:
                    new[c] = n - base.get(c, 0) - pend.get(c, 0)
    out = {}
    for s, v in old.items():
        if s not in read:
            pend = xid_counts(v.get("pending"))
            for c, n in new.items():
                pend[c] = pend.get(c, 0) + n
            out[s] = dict(v, pending=stored(pend))
    for s, c in read.items():
        out[s] = {"xids": stored(xid_counts(c, hardware=False)), "pending": {}}
    if p == "journal":
        # a read that found no line after the cursor keeps it
        cur = rec.get("xid_cursor") or (old.get("journal", {}).get("cursor") if rec.get("xid_read") == "after" else None)
        if cur:
            out["journal"]["cursor"] = cur
        out["journal"]["init"] = rec["gpu_start_failures"]
    return new, {"boot": boot or k.get("boot"), "sources": out}


def level_of(v, warn, bad):
    if bad and v >= bad:
        return "bad"
    return "warn" if warn and v >= warn else None


def readings(rec, want, th, xids):
    # {key: (level, detail)} for each threshold this reading crosses; xids is None when the kernel log was unread
    out, gpus = {}, rec["gpus"]
    if want and (not rec["nvidia_smi"] or rec["smi_errors"]):
        out["driver"] = ("bad", "no nvidia-smi" if not rec["nvidia_smi"] else ("nvidia-smi: " + rec["smi_errors"][0])[:80])
    missing = (want or 0) - len(gpus)
    level = missing > 0 and level_of(missing, th["gpus_missing_warn"], th["gpus_missing_bad"])
    if level:
        out["gpus"] = (level, "%d of %d GPUs visible" % (len(gpus), want))
    hot = max((g for g in gpus if g["temp_c"] is not None), key=lambda g: g["temp_c"], default=None)
    level = hot and level_of(hot["temp_c"], th["temp_warn_c"], th["temp_bad_c"])
    if level:
        out["temp"] = (level, "GPU %d at %.0f C" % (hot["index"], hot["temp_c"]))
    if xids:
        n = sum(xids.values())
        level = "bad" if set(xids) & set(th["xid_bad_codes"]) else \
            "warn" if th["xid_new_warn"] and n >= th["xid_new_warn"] else None
        if level:
            out["xid"] = (level, "%d new Xid (%s)" % (n, ", ".join("%d x%d" % x for x in sorted(xids.items()))))
    held = ["GPU %d %s" % (g["index"], ", ".join(held_back(g))) for g in gpus if held_back(g)]
    if th["throttle_warn"] and held:
        out["throttle"] = ("warn", "held back: " + "; ".join(held))
    ecc = ["GPU %d %s" % (g["index"], g["ecc_uncorrected"]) for g in gpus
           if g["ecc_uncorrected"] not in NA and g["ecc_uncorrected"] != "0"]
    if ecc:
        out["ecc"] = ("warn", "uncorrected ECC errors: " + ", ".join(ecc))
    # a GPU busy with almost no memory in use runs no real job: a hung GPU, or a stray process
    idle = ["GPU %d %.0f%% with %.1f GB" % (g["index"], g["util_pct"], g["mem_used_gb"]) for g in gpus
            if th["idle_busy_pct"] and g["util_pct"] is not None and g["mem_used_gb"] is not None
            and g["util_pct"] >= th["idle_busy_pct"] and g["mem_used_gb"] < th["idle_mem_gb"]]
    if idle:
        out["idle"] = ("warn", "busy with almost no memory in use (hung, or a stray process): " + ", ".join(idle))
    return out


def hold(prev, now, skip, th, t):
    # the levels guests see: a rise counts at once, a fall only after clear_checks clean checks in a row (0 or 1: at
    # the first); a key in skip was not read this time and keeps its state. (levels, [(event, key, level, detail)])
    out, changes = {}, []
    for k in sorted(set(prev) | set(now)):
        p = prev.get(k) if isinstance(prev.get(k), dict) else {}
        old = p.get("level") if p.get("level") in ("warn", "bad") else None
        if k in skip:
            if old:
                out[k] = p
            continue
        level, detail = now.get(k, (None, None))
        if RANK[level] >= RANK[old]:
            if level:
                out[k] = {"level": level, "detail": detail, "clean": 0,
                          "since": (p.get("since") if level == old else None) or local_iso(t)}
            if RANK[level] > RANK[old]:
                changes.append(("TRIPPED", k, level, detail))
            continue
        clean = (p["clean"] if isinstance(p.get("clean"), int) else 0) + 1
        if clean < th["clear_checks"]:
            out[k] = dict(p, clean=clean)
            continue
        if level:
            out[k] = {"level": level, "detail": detail, "clean": 0, "since": local_iso(t)}
        changes.append(("CLEARED", k, old, "now " + ("%s: %s" % (level, detail) if level else "ok")))
    return out, changes


def judge(h, rec, prev, th, t, last=None):
    # one host's entry in the health file, its new state, and the thresholds it crossed since the last check; last
    # is its entry in the file so far
    want = want_gpus(h.get("gpus"))
    levels = prev.get("levels") if isinstance(prev.get("levels"), dict) else {}
    st = {"kernel": prev.get("kernel") if isinstance(prev.get("kernel"), dict) else {}, "unreachable": 0}
    blip = None
    if rec.get("error"):
        # an unreachable host trips only after unreachable_bad_checks failed checks in a row (0: never), so one ssh
        # blip sends nothing; until then the entry keeps its last reading. A host already tripped stays so.
        n = (prev["unreachable"] if isinstance(prev.get("unreachable"), int) else 0) + 1
        need = th["unreachable_bad_checks"]
        st["unreachable"] = n
        why = ("unreachable: " if rec["code"] == UNREACHABLE else "check failed: ") + \
            (rec["error"].strip().splitlines() or ["?"])[-1][:80]
        now, skip = {}, set(levels) - set(["reach"])
        if (need and n >= need) or (isinstance(levels.get("reach"), dict) and levels["reach"].get("level")):
            now["reach"] = ("bad", "%s (%d failed check(s) in a row)" % (why, n))
            entry = dict((k, None) for k in ENTRY)
            entry.update(gpus=want, checked=local_iso(t))
        else:
            skip.add("reach")
            blip = "%s (%d of %d failed checks in a row before it counts)" % (why, n, need) if need else why
            entry = dict((k, last.get(k)) for k in ENTRY) if isinstance(last, dict) and last.get("checked") else \
                dict(dict((k, None) for k in ENTRY), gpus=want, checked=local_iso(t))
    else:
        xids, st["kernel"] = count_xids(rec, st["kernel"])
        now = readings(rec, want, th, xids)
        skip = set() if xids is not None else set(["xid"])
        gpus = rec["gpus"]
        use = [g["util_pct"] for g in gpus if g["util_pct"] is not None]
        temps = [g["temp_c"] for g in gpus if g["temp_c"] is not None]
        entry = {"gpus": len(gpus) if want is None else want, "gpus_visible": len(gpus),
                 "busy": round(sum(use) / len(use) / 100.0, 2) if use else None,
                 "xid_new": None if xids is None else sum(xids.values()),
                 "temp_max_c": int(round(max(temps))) if temps else None, "checked": local_iso(t)}
    st["levels"], changes = hold(levels, now, skip, th, t)
    order = sorted(st["levels"].items(), key=lambda kv: (-RANK[kv[1]["level"]], kv[0]))
    entry["health"] = order[0][1]["level"] if order else "ok"
    entry["notes"] = ["%s %s: %s%s" % (k, v["level"], v["detail"], " (clean for %d of %d checks)"
                                       % (v["clean"], th["clear_checks"]) if v.get("clean") else "")
                      for k, v in order] + ([blip] if blip else [])
    return entry, st, changes


def load_health(ctx):
    # the project's health file and the state behind it, as {host: entry} and {host: state}
    d = os.path.join(ctx.project["root"], ctx.project["pack"], ".memory")
    path, spath = os.path.join(d, HEALTH_FILE), os.path.join(d, STATE_FILE)
    docs = []
    for p in (path, spath):
        try:
            doc = hc.read_json(p)
        except OSError:
            doc = None
        hosts = doc.get("hosts") if isinstance(doc, dict) and isinstance(doc.get("hosts"), dict) else {}
        docs.append(dict((n, v) for n, v in hosts.items() if isinstance(v, dict)))
    return {"file": path, "state_file": spath, "hosts": docs[0], "state": docs[1]}


def write_health(ctx, store, hosts, recs, th, t):
    # the health file guests read and the state behind it, in the project's <pack>/.memory/ (atomic replace)
    out, state = store["hosts"], store["state"]
    # a host not checked this time keeps its entry; with the project's own inventory, one it no longer lists goes
    if not (ctx.hosts_file or ctx.host_names or ctx.local_root):
        names = set(h["name"] for h in ctx.inventory())
        out = dict((n, e) for n, e in out.items() if n in names)
        state = dict((n, s) for n, s in state.items() if n in names)
    changes = []
    for h, r in zip(hosts, recs):
        out[h["name"]], state[h["name"]], crossed = judge(h, r, state.get(h["name"]) or {}, th, t, out.get(h["name"]))
        changes += [{"host": h["name"], "event": e, "what": k, "level": lv, "detail": dt} for e, k, lv, dt in crossed]
    # the health file first: should the state fail to write, the next check reports the same crossings again
    path = store["file"]
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        hc.write_json(path, {"schema": 1, "checked": local_iso(t), "hosts": out})
        hc.write_json(store["state_file"], {"schema": 1, "hosts": state})
    except OSError as e:
        raise hc.Fail("cannot write %s: %s" % (e.filename or path, e.strerror or e))
    return {"file": path, "hosts": out, "changes": changes}


def health_lines(ctx, hosts, health):
    lines = ["%s %s %s %s: %s" % (c["event"], c["host"], c["what"], c["level"], c["detail"]) for c in health["changes"]]
    n = {}
    for e in health["hosts"].values():
        n[e.get("health")] = n.get(e.get("health"), 0) + 1
    lines.append("health file %s: %d host(s): %s; %s" % (
        health["file"], len(health["hosts"]), ", ".join("%d %s" % (n[k], k) for k in ("ok", "warn", "bad") if n.get(k))
        or "none", "%d threshold(s) crossed" % len(health["changes"]) if health["changes"]
        else "no threshold crossed since the last check"))
    me = ctx.project
    owned = [h["name"] for h in hosts if h.get("owner") == me["slug"]
             and any(c["host"] == h["name"] for c in health["changes"])]
    if owned and me["share_with"]:
        lines.append("send the TRIPPED and CLEARED lines of %s to the projects you share them with (%s); they read the "
                     "rest in the health file" % (", ".join(owned), ", ".join("every project" if s == "*" else s
                                                                               for s in me["share_with"])))
    return lines


def is_stale(age, th):
    # an owner's reading too old to trust (stale_h 0: never), or of no known age
    return age is None or bool(th["stale_h"]) and age > th["stale_h"] * 3600


def shared_health(ctx, th, t):
    # a guest's view: what each owner's health file says of the hosts it shares with this project (no ssh)
    me = ctx.project
    sroot = hc.solaris_root_of(me["root"]) if me else None
    if not sroot:
        raise hc.Fail("--shared reads the health files of the projects that share hosts with this one: run it in a "
                      "project of a Solaris tree (or pass --project)", USAGE)
    peers, notes = hc.peer_hosts(me)
    files, out, lines = {}, {}, []
    for h in peers:
        folder = h["project_folder"]
        if folder not in files:
            root = os.path.join(sroot, folder)
            try:
                pack = hc.pack_of(root)
                doc = hc.read_json(os.path.join(root, pack, ".memory", HEALTH_FILE)) if pack else None
            except (hc.Fail, OSError) as e:
                pack, doc = None, None
                notes.append("cannot read the health file of %s: %s" % (h["project"], getattr(e, "strerror", e)))
            files[folder] = (os.path.join(root, pack, ".memory", HEALTH_FILE) if pack else None,
                             doc.get("hosts") if isinstance(doc, dict) and isinstance(doc.get("hosts"), dict) else {})
        path, got = files[folder]
        e = got.get(h["name"]) if isinstance(got.get(h["name"]), dict) else None
        label = "%s [owner %s]" % (h["name"], h["owner"])
        if e is None:
            out[h["shared_key"]] = {"owner": h["owner"], "file": path, "health": None, "stale": True}
            lines.append("%s: no reading in its owner's health file: ask %s to run hosthealth.py --write with its "
                         "hourly checks" % (label, h["owner"]))
            continue
        try:
            age = t - hc.parse_iso(e.get("checked"))
        except ValueError:
            age = None
        stale = is_stale(age, th)
        out[h["shared_key"]] = dict(e, owner=h["owner"], file=path, age_s=age, stale=stale)
        busy = num(e.get("busy"))
        lines.append("%s: %s, checked %s ago%s; %s of %s GPUs visible, busy %s, hottest %s, %s new Xid" % (
            label, e.get("health"), hc.fmt_age(age), ": stale, its owner's checks may have stopped (ask it)"
            if stale else "", fmt(num(e.get("gpus_visible"))), fmt(num(e.get("gpus"))),
            fmt(None if busy is None else busy * 100, "%"), fmt(num(e.get("temp_max_c")), " C"),
            fmt(num(e.get("xid_new")))))
        lines += ["  " + str(x) for x in e.get("notes") or []]
    if not peers:
        lines.append("no project shares hosts with %s" % me["slug"])
    lines += ["note: " + n for n in notes]
    bad = any(v.get("stale") or v.get("health") != "ok" for v in out.values())
    return {"hosts": out, "notes": notes}, lines, PROBLEMS if bad else OK


def build_parser():
    ap = argparse.ArgumentParser(prog="hosthealth.py", description="GPU host health over the resource-sharing host "
                                 "inventory; --fix applies the safe performance settings where the sharing rules "
                                 "allow. See the resource-sharing skill.")
    ap.add_argument("--host", action="append", help="inventory host (repeat; default: every host)")
    ap.add_argument("--fix", action="store_true", help="also set persistence mode, the default GPU power limit and "
                    "the performance CPU governor: on hosts this project owns, or on what only its live claims use")
    ap.add_argument("--power", choices=("default", "max"), help="the GPU power limit the check expects and --fix sets: "
                    "default (each GPU's default limit; the default) or max (the most each GPU allows, on hosts this "
                    "project owns; others keep the default); default from the policy's health.power")
    ap.add_argument("--write", action="store_true", help="also keep the health of the hosts checked in the project's "
                    "<pack>/.memory/host-health.json, the file guests read, and print TRIPPED or CLEARED lines when a "
                    "host crosses a threshold (hosthealth.* settings in <pack>/.memory/config.json)")
    ap.add_argument("--shared", action="store_true", help="a guest: print what the owners' health files say of the "
                    "hosts shared with this project (no ssh)")
    ap.add_argument("--json", action="store_true", help="print JSON")
    ap.add_argument("--hosts", help="use exactly this inventory file (*.json, or a path) [{name, target, opts}], or "
                    "only these host names (NAME[,NAME], OWNER/NAME) from the project's inventory or seen list (or "
                    "HOSTCLAIMS_HOSTS)")
    ap.add_argument("--project", help="project folder to act for (default: found from here)")
    ap.add_argument("--local-root", help="check this machine, with the claims folder DIR (no ssh)")
    ap.add_argument("--ssh", help="ssh program (default ssh, or HOSTCLAIMS_SSH)")
    ap.add_argument("--agent", help="who acts: default HOSTCLAIMS_AGENT, else the project's slug")
    ap.add_argument("--as-owner", action="store_true", help="outside a project: confirm that --fix acts for --agent")
    ap.add_argument("--timeout", type=float, help="seconds per host (default 120)")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    health = None
    try:
        if a.timeout is not None and a.timeout <= 0:
            raise hc.Fail("--timeout must be positive", USAGE)
        if a.shared and (a.fix or a.write or a.host or a.power):
            raise hc.Fail("--shared only reads the owners' health files: it takes no --host, --fix, --power or --write",
                          USAGE)
        ctx = hc.Ctx(a)
        th, tnotes = (None, [])
        if a.write or a.shared:
            if not ctx.project:
                raise hc.Fail("--write and --shared need a project (its <pack>/.memory/ holds the health file): run "
                              "inside one or pass --project", USAGE)
            th, tnotes = health_settings(ctx.project)
        if a.shared:
            obj, lines, code = shared_health(ctx, th, time.time())
            obj["notes"] = tnotes + obj["notes"]
            hc.emit(ctx, dict(obj, code=code), lines + ["note: " + n for n in tnotes])
            return code
        power = a.power or (ctx.policy.get("health") or {}).get("power") or "default"
        if power not in ("default", "max"):
            raise hc.Fail("the policy's health.power must be default or max, not %r" % (power,), USAGE)
        hosts = ctx.hosts(a.host)
        ctx.notes += tnotes
        # --fix changes hosts, so it acts as the calling project, like hostclaims' owner actions
        me = ctx.need_owner_identity() if a.fix else ctx.agent
        timeout = a.timeout or 120.0
        # --write reads each host's journal only after where its last check stopped
        store = load_health(ctx) if a.write else None
        kernel = (lambda h: kernel_arg(store["state"].get(h["name"]) or {})) if a.write else (lambda h: None)
        with ThreadPoolExecutor(max_workers=max(1, min(16, len(hosts)))) as ex:
            recs = list(ex.map(lambda h: check_host(ctx, h, me, a.fix, timeout, power, kernel(h)), hosts))
        if a.write:
            health = write_health(ctx, store, hosts, recs, th, time.time())
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
    if health:
        lines += health_lines(ctx, hosts, health)
    lines += ["note: " + n for n in ctx.notes]
    code = exit_code(recs)
    obj = {"hosts": dict((r["host"], r) for r in recs), "notes": ctx.notes, "code": code}
    if health:
        obj["health"] = health
    hc.emit(ctx, obj, lines)
    return code


if __name__ == "__main__":
    sys.exit(main())
