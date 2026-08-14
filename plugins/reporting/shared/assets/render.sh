#!/bin/sh
# rev. 2
# Render report HTML sources (reports/html/<slug>.html) to PDFs (reports/<slug>.pdf).
# Run from the project root or from the reports/ dir:
#
#   render.sh                    render every report in reports/html/
#   render.sh 0804-overview      render one (or more) by slug
#   render.sh --live [slug ...]  watch html/ + stylesheet + renderer + theme
#                                and re-render on change (all, or just the
#                                given slugs). Ctrl-C to stop.
#
# Sources are gitignored working files; the tracked deliverables are the PDFs.
# Theme + page furniture are project-owned: reports/theme.css, reports/report.json.
SELF="$(cd "$(dirname "$0")" && pwd)"
if [ -d reports ]; then RDIR=reports; else RDIR=.; fi
HTML="$RDIR/html"
mkdir -p "$HTML"

LIVE=0
SLUGS=""
for a in "$@"; do
  case "$a" in
    --live) LIVE=1 ;;
    *) SLUGS="$SLUGS ${a%.html}" ;;
  esac
done
[ -z "$(echo $SLUGS)" ] && SLUGS=$(ls "$HTML"/*.html 2>/dev/null | sed 's|.*/||; s|\.html$||')
[ -z "$(echo $SLUGS)" ] && { echo "render.sh: no HTML sources in $HTML/"; exit 1; }

FAILED=0
render_one() {
  node "$SELF/render.js" "$HTML/$1.html" "$RDIR/$1.pdf" || FAILED=1
}

render_all() {
  for s in $SLUGS; do
    if [ -e "$HTML/$s.html" ]; then render_one "$s"; else echo "skip: no $HTML/$s.html"; fi
  done
}

render_all
[ "$LIVE" -eq 0 ] && exit $FAILED

# --live: poll mtimes (portable - no fswatch dependency); shared files
# (stylesheet, renderer, project theme/config) trigger a full re-render,
# an HTML file just its own.
stamp() { ls -l -T "$@" 2>/dev/null || ls -l "$@" 2>/dev/null; }
SHARED="$SELF/style.css $SELF/render.js $RDIR/theme.css $RDIR/report.json"
echo "render.sh --live: watching $HTML/ + stylesheet/renderer/theme (Ctrl-C to stop)"
PREV_SHARED=$(stamp $SHARED)
for s in $SLUGS; do eval "PREV_$(echo "$s" | tr -c 'a-zA-Z0-9' '_')=\"\$(stamp "$HTML/$s.html")\""; done
while :; do
  sleep 1
  CUR_SHARED=$(stamp $SHARED)
  if [ "$CUR_SHARED" != "$PREV_SHARED" ]; then
    PREV_SHARED=$CUR_SHARED
    echo "[live] shared file changed - re-rendering all"
    render_all
    continue
  fi
  for s in $SLUGS; do
    v="PREV_$(echo "$s" | tr -c 'a-zA-Z0-9' '_')"
    cur=$(stamp "$HTML/$s.html")
    eval "prev=\$$v"
    if [ "$cur" != "$prev" ]; then
      eval "$v=\"\$cur\""
      echo "[live] $s.html changed - re-rendering"
      render_one "$s"
    fi
  done
done
