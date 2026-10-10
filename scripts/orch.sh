#!/usr/bin/env bash
# Clinova orchestration helper. Coordination happens through git tags on origin:
#   kickoff, gate/contracts, gate/1, gate/2, gate/3, gate/ship, done/<ID>, failed/<ID>
# Usage: scripts/orch.sh <command> [args]   (run with no args for help)
set -uo pipefail

MAIN="$(cd "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || echo .git)")" && pwd)"
TASKS="$MAIN/orchestration/tasks"
mkdir -p "$MAIN/.orch" "$MAIN/.data/logs" 2>/dev/null || true

getenv() { # read KEY from the main clone's .env without sourcing it
  local v
  v="$(grep -E "^$1=" "$MAIN/.env" 2>/dev/null | tail -1 | cut -d= -f2-)"
  v="$(printf '%s' "$v" | sed -E 's/[[:space:]]+#.*$//; s/^[[:space:]]+//; s/[[:space:]]+$//; s/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/')"
  printf '%s' "${v:-${!1:-}}"
}
header() { grep -E "^$2:" "$TASKS/$1.md" 2>/dev/null | head -1 | cut -d: -f2- | sed -E 's/^[[:space:]]+//'; }
remote_has() { git ls-remote --exit-code --tags origin "refs/tags/$1" >/dev/null 2>&1; }
tag_msg() { git fetch -q -f origin "refs/tags/$1:refs/tags/$1" 2>/dev/null; git tag -l --format='%(contents:subject)' "$1"; }

notify() {
  local title="$1" msg="${2:-}" topic
  topic="$(getenv NTFY_TOPIC)"
  echo "[$(date -u +%FT%TZ)] $title: $msg" >> "$MAIN/.orch/notifications.log"
  [ -n "$topic" ] && curl -fsS -m 10 -H "Title: $title" -d "$msg" "https://ntfy.sh/$topic" >/dev/null 2>&1
  if command -v osascript >/dev/null 2>&1; then
    osascript -e "display notification \"${msg//\"/\'}\" with title \"${title//\"/\'}\" sound name \"Glass\"" >/dev/null 2>&1
  elif command -v notify-send >/dev/null 2>&1; then
    notify-send "$title" "$msg" >/dev/null 2>&1
  fi
  printf '\a'; echo "NOTIFIED: $title: $msg"
}

cmd="${1:-help}"; shift || true
case "$cmd" in
  main) echo "$MAIN" ;;

  preflight)
    fail=0
    ok(){ echo "OK    $*"; }; bad(){ echo "FAIL  $*"; fail=1; }; warn(){ echo "WARN  $*"; }
    git -C "$MAIN" ls-remote origin >/dev/null 2>&1 && ok "git remote reachable" || bad "cannot reach git remote 'origin'"
    [ -n "$(git -C "$MAIN" config user.email)" ] && ok "git identity set" || bad "set git user.name and user.email"
    git -C "$MAIN" push -q --dry-run origin HEAD:refs/heads/orch-preflight >/dev/null 2>&1 && ok "git push allowed" || bad "git push to origin fails from here (auth or sandbox); see orchestration/SETUP.md"
    [ -f "$MAIN/.env" ] && ok ".env present" || bad ".env missing in $MAIN"
    if git -C "$MAIN" ls-files --error-unmatch .env >/dev/null 2>&1; then bad "SECURITY: .env is tracked by git. git rm --cached .env, push, and rotate the Gemini key and Tiger password"; else ok ".env not tracked"; fi
    [ -n "$(git -C "$MAIN" log --all --oneline -- .env 2>/dev/null)" ] && warn "SECURITY: .env appears in git history; rotate the Gemini key and Tiger password"
    for k in GEMINI_API_KEY DATABASE_URL; do [ -n "$(getenv $k)" ] && ok "$k set" || bad "$k empty in .env"; done
    [ -n "$(getenv NTFY_TOPIC)" ] && ok "NTFY_TOPIC set" || warn "NTFY_TOPIC empty: no phone notifications"
    command -v uv >/dev/null 2>&1 && ok "uv $(uv --version | cut -d' ' -f2)" || bad "uv not installed"
    if command -v uv >/dev/null 2>&1; then
      DATABASE_URL="$(getenv DATABASE_URL)" uv run -q --no-project --with 'psycopg[binary]' python -c \
        "import os,psycopg; c=psycopg.connect(os.environ['DATABASE_URL'],connect_timeout=10); print(c.execute('select version()').fetchone()[0][:40]); c.close()" \
        >/dev/null 2>&1 && ok "Tiger Cloud reachable" || bad "cannot connect to DATABASE_URL"
    fi
    docker info >/dev/null 2>&1 && ok "docker running" || warn "docker not running (needed by T16, T31, G3, G4 only)"
    exit $fail ;;

  kickoff)
    if remote_has kickoff; then echo "kickoff exists: $(tag_msg kickoff)"; else
      git tag -a kickoff -m "kickoff $(date -u +%FT%TZ)" HEAD && git push -q origin refs/tags/kickoff && echo "kickoff tagged"; fi ;;

  hour)
    git fetch -q -f origin refs/tags/kickoff:refs/tags/kickoff 2>/dev/null
    t0="$(git for-each-ref --format='%(taggerdate:unix)' refs/tags/kickoff)"
    [ -z "$t0" ] && { echo "0.00"; exit 0; }
    awk -v n="$(date +%s)" -v t="$t0" 'BEGIN{printf "%.2f\n",(n-t)/3600}' ;;

  start) # start <ID>: prepare the task's branch and directory; prints the directory to work in
    id="$1"; br="$(header "$id" branch)"; [ -z "$br" ] && { echo "unknown task $id" >&2; exit 1; }
    git fetch -q origin --tags
    if [ "$(git rev-parse --git-dir)" != "$(git rev-parse --git-common-dir)" ]; then
      dir="$(git rev-parse --show-toplevel)"   # already inside a linked worktree (Claude isolation)
      if git ls-remote --exit-code --heads origin "$br" >/dev/null 2>&1; then git switch -q -C "$br" "origin/$br"; else git switch -q -C "$br" origin/main; fi
    else
      dir="$MAIN/.worktrees/$id"               # main clone (Codex): make a worktree inside the workspace
      if [ ! -d "$dir" ]; then
        if git ls-remote --exit-code --heads origin "$br" >/dev/null 2>&1; then git -C "$MAIN" worktree add -q -B "$br" "$dir" "origin/$br"
        else git -C "$MAIN" worktree add -q -B "$br" "$dir" origin/main; fi
      fi
    fi
    ln -sf "$MAIN/.env" "$dir/.env"
    echo "$dir" ;;

  done) # done <ID> <ok|partial|cut> "<one line>"   (run inside the task's directory)
    id="$1"; st="${2:-ok}"; msg="${3:-}"
    git add -A && { git diff --cached --quiet || git commit -q -m "$id: $msg"; }
    git push -q -u origin HEAD || { echo "push failed" >&2; exit 1; }
    git tag -f -a "done/$id" -m "$st: $msg" HEAD >/dev/null && git push -q -f origin "refs/tags/done/$id" && echo "done/$id pushed ($st)" ;;

  failed) # failed <ID> "<reason>"
    id="$1"; msg="${2:-}"
    git add -A && { git diff --cached --quiet || git commit -q -m "$id: WIP before failure"; }
    git push -q -u origin HEAD 2>/dev/null
    git tag -f -a "failed/$id" -m "$msg" HEAD >/dev/null && git push -q -f origin "refs/tags/failed/$id" && echo "failed/$id pushed" ;;

  is-done) remote_has "done/$1" ;;
  msg) tag_msg "$1" ;;

  wait) # wait [--timeout-min N] <tag>...   exit 0 when all exist, 2 on timeout
    to=5; [ "${1:-}" = "--timeout-min" ] && { to="$2"; shift 2; }
    end=$(( $(date +%s) + to*60 ))
    while :; do
      missing=(); for t in "$@"; do remote_has "$t" || missing+=("$t"); done
      [ ${#missing[@]} -eq 0 ] && { echo "ready: $*"; exit 0; }
      [ "$(date +%s)" -ge "$end" ] && { echo "still waiting for: ${missing[*]} (hour $("$0" hour))"; exit 2; }
      sleep 45
    done ;;

  gate) # gate <contracts|1|2|3|ship> "<summary>"   (integrator only, on main)
    git tag -f -a "gate/$1" -m "${2:-}" HEAD >/dev/null && git push -q -f origin "refs/tags/gate/$1" && echo "gate/$1 pushed" ;;

  notify) notify "${1:-Clinova}" "${2:-}" ;;

  status)
    git fetch -q -f origin '+refs/tags/*:refs/tags/*' 2>/dev/null
    echo "hour $("$0" hour)"
    for g in contracts 1 2 3 ship; do git rev-parse -q --verify "refs/tags/gate/$g" >/dev/null && echo "gate/$g  $(git tag -l --format='%(contents:subject)' gate/$g)"; done
    for f in "$TASKS"/*.md; do id="$(basename "$f" .md)"; s="pending"
      git rev-parse -q --verify "refs/tags/failed/$id" >/dev/null && s="FAILED: $(git tag -l --format='%(contents:subject)' failed/$id)"
      git rev-parse -q --verify "refs/tags/done/$id" >/dev/null && s="$(git tag -l --format='%(contents:subject)' done/$id)"
      printf '%-4s %-9s %s\n' "$id" "$(header "$id" owner)" "$s"; done ;;

  *) sed -n '2,4p' "$0"; echo "commands: main preflight kickoff hour start done failed is-done msg wait gate notify status" ;;
esac
