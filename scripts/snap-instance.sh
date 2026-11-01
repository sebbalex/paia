#!/usr/bin/env bash
# Snapshot the private instance repo (.instance.git): stage the owner's data,
# commit it, push it to the instance remote. The public repo is never touched.
#
# Usage: scripts/snap-instance.sh [message]   (default: "snapshot YYYY-MM-DD HH:MM")
#        SNAP_NO_PUSH=1 scripts/snap-instance.sh   commit only

set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

if [ ! -d .instance.git ]; then
  echo "no .instance.git in $root -- see README, 'Version your own copy'" >&2
  exit 1
fi

git() { command git --git-dir=.instance.git --work-tree="$root" "$@"; }

# Same paths as the `snap` alias. -f because the public .gitignore hides them;
# .instance.git/info/exclude still keeps memory/raw/ out.
paths=()
for p in context memory/wiki routines .instance-gitignore; do
  [ -e "$p" ] && paths+=("$p")
done
git add -f -A -- "${paths[@]}"

if git diff --cached --quiet; then
  echo "nothing to commit"
else
  git status --short
  git commit -q -m "${1:-snapshot $(date '+%F %H:%M')}"
  git log --oneline -1
fi

[ "${SNAP_NO_PUSH:-}" = 1 ] && exit 0

branch="$(git symbolic-ref --short HEAD)"
if ! git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
  remote="$(git remote | head -n1)"
  if [ -z "$remote" ]; then
    echo "no remote configured on .instance.git -- committed locally only" >&2
    exit 0
  fi
  git push -u "$remote" "$branch"
elif [ -n "$(git rev-list '@{u}..HEAD')" ]; then
  git push
else
  echo "remote up to date"
fi
