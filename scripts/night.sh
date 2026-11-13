#!/bin/sh
# A stream processed with nobody watching: prep, map, shorts cut by Claude Code headless, the queue announced.
#
#   scripts/night.sh <video> [how many shorts, default 3]
#
# The queue should come out full: when fewer shorts than asked for got through QA, the editor gets one more pass to
# repair the failed ones or cut other moments in their place. Run again on the same video, it cuts only what is
# missing: a short that waits or that you approved counts, one you rejected gets a replacement. With 0 shorts it
# cuts none, so `NIGHT_RECAP=1 night.sh <video> 0` adds just the recap.
#
# The recording is untrusted input (a public chat, whatever was on screen), so the editor runs locked down:
#   - tools: Bash, Read, Write, Edit, Glob, Grep. No web tools, no agents, no MCP servers, no user or project settings;
#   - Bash: only the jian commands an edit needs. Not ingest, approve, reject, queue, hook, clean, new or pull;
#   - files: read inside the video folder, write inside its work folder (<video>/jianji/), nowhere else
#     (an Edit(//path/**) rule covers every file-writing tool). The recap's style guide lives outside both (your
#     layer, or jian's own): the script reads it and hands it over with the instructions;
#   - whatever isn't on those lists is denied, never asked.
# Checked on Claude Code 2.1: curl, python, touch, redirects, `jian x && other`, jian commands off the list, and
# reads or writes outside those folders (cat and ls included) are all refused.
# It's a script, not a `jian` command: jian itself never calls a model.
#
# Environment: JIAN, CLAUDE (the commands), NIGHT_RECAP=1 (also the recap, /jian-edit, after the shorts),
# NIGHT_CLAUDE_ARGS (extra arguments for claude, e.g. "--model sonnet"), NIGHT_*_TIMEOUT (seconds per step),
# NIGHT_SHORTS_PASSES (editor passes over the shorts, at most: 2).
set -u
PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
JIAN=${JIAN:-jian}
CLAUDE=${CLAUDE:-claude}
[ $# -ge 1 ] || { echo "usage: night.sh <video> [shorts]" >&2; exit 2; }
video=$1
count=${2:-3}
case $count in *[!0-9]*|"") echo "night: shorts must be a number" >&2; exit 2 ;; esac
here=$(cd "$(dirname "$0")" && pwd)

status=$("$JIAN" status "$video") || exit 1
dir=$(printf '%s\n' "$status" | sed -n '1s/  profile=.*//p')
work=$(printf '%s\n' "$status" | sed -n 's/^work folder: \(.*\)  style guide: .*/\1/p')
style=$(printf '%s\n' "$status" | sed -n 's/^work folder: .*  style guide: \(.*\)/\1/p')
[ -d "$dir" ] && [ -d "$work" ] || { echo "night: can't read the video and work folders from \`jian status\`" >&2; exit 1; }

lock=$work/night.lock  # one night per video: a second run would cut the same shorts twice
if ! mkdir "$lock" 2>/dev/null; then
  other=$(cat "$lock/pid" 2>/dev/null)
  if [ -n "$other" ] && kill -0 "$other" 2>/dev/null; then echo "night: already running for $video (pid $other)" >&2; exit 1; fi
fi
echo $$ > "$lock/pid"
trap 'rm -f "$lock/pid"; rmdir "$lock" 2>/dev/null' EXIT
trap 'exit 130' INT TERM
command -v caffeinate >/dev/null 2>&1 && caffeinate -i -w $$ &  # the Mac stays awake while this runs

log=$work/night.log
say() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$log"; }
step() {  # step <name> <seconds> <command...>: logged, and stopped if it runs past its time
  name=$1 limit=$2; shift 2
  say "$name: start"
  "$@" >> "$log" 2>&1 &
  job=$!
  perl -e 'sleep shift; kill "TERM", shift' "$limit" "$job" &
  dog=$!
  wait "$job"; rc=$?
  kill "$dog" 2>/dev/null
  say "$name: $([ $rc -eq 0 ] && echo done || echo "FAILED ($rc)")"
  return $rc
}
body() { awk 'n >= 2; /^---$/ { n++ }' "$here/../.claude/skills/$1/SKILL.md"; }  # a skill without its front matter
editor() {  # editor <skill> <request> [style guide]: Claude Code headless, in the video folder, with the lists above.
  # Only ever run through `step` (in the background): exec makes the job claude itself, so its time limit stops it.
  guide=
  [ -f "${3:-}" ] && guide="

The style guide \`jian status\` names is outside the folders you can read. This is it, whole:

$(cat "$3")"
  cd "$dir" && exec "$CLAUDE" -p "$(body "$1")

$2 Call jian exactly as: $JIAN$guide" \
    --restricted --strict-mcp-config --no-session-persistence --permission-mode dontAsk \
    --tools "Bash,Read,Write,Edit,Glob,Grep" \
    --allowedTools "Bash($JIAN status *)" "Bash($JIAN prep *)" "Bash($JIAN map *)" "Bash($JIAN words *)" \
      "Bash($JIAN sheet *)" "Bash($JIAN compile *)" "Bash($JIAN cut *)" "Bash($JIAN qa *)" "Bash($JIAN at *)" \
      "Bash($JIAN cuts *)" "Bash($JIAN texts *)" "Read" "Glob" "Grep" "Edit(/$work/**)" \
    ${NIGHT_CLAUDE_ARGS:-}
}

say "night: $video ($dir), up to $count shorts"
step prep "${NIGHT_PREP_TIMEOUT:-14400}" "$JIAN" prep "$video" || exit 1
step map "${NIGHT_MAP_TIMEOUT:-7200}" "$JIAN" map "$video" || exit 1
made() {  # this video's shorts that got through QA and you haven't said no to: waiting, or approved
  "$JIAN" status "$video" 2>/dev/null | sed -n 's/^shorts: \(.*\)  (jian status .*/\1/p' | tr ',' '\n' \
    | grep -c -E ' (pending|approved)$'
}
failed=0
# shorts first: if the recap fails or runs out of time, the queue is already there
pass=1
while have=$(made); have=${have:-0}; [ "$have" -lt "$count" ]; do
  if [ "$pass" -gt "${NIGHT_SHORTS_PASSES:-2}" ]; then say "shorts: $have of $count got through QA"; break; fi
  if [ "$have" -eq 0 ] && [ "$pass" -eq 1 ]; then ask="At most $count shorts."
  else ask="$have of the $count shorts wanted are already there (\`$JIAN status\` shows where each one stands): repair the ones that failed QA or cut other moments in their place, $((count - have)) more at most, and leave the rest alone."
  fi
  step shorts "${NIGHT_SHORTS_TIMEOUT:-3600}" editor jian-shorts \
    "Do it now for the video folder $dir (work folder: $work). $ask Nobody is watching: don't ask, finish and report." \
    || { failed=1; "$JIAN" hook error COMMAND=night VIDEO="$(basename "$dir")" ERROR="the shorts step failed: see night.log"; break; }
  pass=$((pass + 1))
done
[ "$pass" -eq 1 ] && [ "$have" -ge "$count" ] && say "shorts: $have already cut, $count wanted: nothing to do"
if [ "${NIGHT_RECAP:-0}" = 1 ]; then
  step recap "${NIGHT_RECAP_TIMEOUT:-7200}" editor jian-edit \
    "Do it now for the video folder $dir (work folder: $work). Nobody is watching: don't ask, finish and report." "$style" \
    || say "recap: left for you (/jian-edit $(basename "$dir"))"
fi
step queue 120 "$JIAN" queue --announce || failed=1
say "night: $([ $failed -eq 0 ] && echo finished || echo "finished with errors")"
exit $failed
