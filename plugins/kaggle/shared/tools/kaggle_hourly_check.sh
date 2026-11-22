#!/bin/sh
# rev. 1
# kaggle_hourly_check.sh: the guard around the hourly wake's check. It is the command the wake calls.
#
#   sh <plugin-dir>/tools/kaggle_hourly_check.sh <slug> [arguments of kaggle_hourly.py --check]
#
# It runs `python3 kaggle_hourly.py <slug> --check [arguments]`, with the kaggle_hourly.py beside this file,
# and passes its standard output on. Autocode starts no turn when a check exits 1, so this guard exits 1 only
# when that run exited 1 AND its output starts with "quiet:", the first word of a verified quiet pass. In
# every other case it exits 0, which wakes the agent:
#   - the check woke (exit 0, with its reasons);
#   - an exit 1 without the word: Python itself exits 1, with nothing on standard output, for a tool file
#     that does not compile (a cut copy, a merge mark) and for an interpreter that does not start;
#   - any other exit code, a missing python3, no output at all;
#   - a run that passed the time limit below (exit 124, or 137 when it had to be killed).
# For a run that failed or printed nothing it adds the line
#   wake: hourly check failed (exit N); run it by hand: python3 <tool> <slug> --check
# after whatever the run printed. Standard error is passed through: autocode quotes it after standard output.
#
# The time limit: where the `timeout` command exists, the run is stopped (TERM, then KILL 5 seconds later)
# once it has run for the check's own limit (--limit SECONDS, default 240) plus 20 seconds. The check keeps
# its own limit by itself; this one is for an interpreter that hangs. KAGGLE_HOURLY_CHECK_GRACE gives another
# number of seconds than 20 (the tests). Without `timeout` the wake's check_timeout is the only limit left.
#
# Exit codes: 1 a verified quiet pass, 0 everything else. It writes no file. POSIX sh; paths may hold spaces.

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || here=.
tool=$here/kaggle_hourly.py

# the check's own limit, to set this guard's just above it
limit=240
prev=
for arg in "$@"; do
    case $prev in --limit) limit=$arg ;; esac
    case $arg in --limit=*) limit=${arg#--limit=} ;; esac
    prev=$arg
done
limit=${limit%%.*}
case $limit in '' | *[!0-9]*) limit=240 ;; esac
grace=${KAGGLE_HOURLY_CHECK_GRACE:-20}
case $grace in '' | *[!0-9]*) grace=20 ;; esac

# <slug> --check <the other arguments>, each argument kept whole
if [ "$#" -gt 0 ]; then
    slug=$1
    shift
    set -- "$slug" --check "$@"
else
    set -- --check
fi

if command -v timeout >/dev/null 2>&1; then
    out=$(timeout -k 5 "$((limit + grace))" python3 "$tool" "$@")
    rc=$?
else
    out=$(python3 "$tool" "$@")
    rc=$?
fi

case "$rc:$out" in
    1:quiet:*)
        printf '%s\n' "$out"
        exit 1
        ;;
esac
if [ -n "$out" ]; then
    printf '%s\n' "$out"
fi
if [ "$rc" -ne 0 ] || [ -z "$out" ]; then
    printf 'wake: hourly check failed (exit %s); run it by hand: python3 %s %s\n' "$rc" "$tool" "$*"
fi
exit 0
