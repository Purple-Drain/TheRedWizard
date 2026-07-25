#!/usr/bin/env bash
# SessionStart hook: warn if another worktree of this repo has uncommitted
# changes, so a new session knows about source/working-directory overlap
# before it starts writing. Backs the advisory scope-new-session /
# parallel-tabs-worktree skills with something that actually runs.
set -u

current="$(git rev-parse --show-toplevel 2>/dev/null)"
[ -z "$current" ] && exit 0

warnings=""
wt=""
while IFS= read -r line; do
  case "$line" in
    worktree\ *) wt="${line#worktree }" ;;
    branch\ *)
      br="${line#branch }"
      br="${br#refs/heads/}"
      status="$(git -C "$wt" status --porcelain 2>/dev/null)"
      if [ -n "$status" ]; then
        n="$(printf '%s\n' "$status" | wc -l)"
        label="$wt"
        [ "$wt" = "$current" ] && label="$wt (this session)"
        warnings="${warnings}- ${label} on ${br}: ${n} uncommitted file(s)\n"
      fi
      ;;
  esac
done < <(git worktree list --porcelain 2>/dev/null)

if [ -z "$warnings" ]; then
  echo '{}'
  exit 0
fi

msg="Worktree overlap check: uncommitted changes exist in one or more active worktrees for this repo.\n${warnings}Before writing/switching branches here, consider isolating with parallel-tabs-worktree."
esc="$(printf '%s' "$msg" | sed ':a;N;$!ba;s/\\/\\\\/g;s/"/\\"/g;s/\n/\\n/g')"
printf '{"systemMessage": "%s"}\n' "$esc"
