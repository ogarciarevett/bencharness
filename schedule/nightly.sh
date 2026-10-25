#!/bin/sh
# Night run (02:30): in its own worktree at origin/main, list new candidates, run at most one trial
# that passes the checks, then commit any new records to nightly/<date> and open one PR for the
# morning review (schedule/review.sh). Host rules live in bench/guard.py: a busy host means the
# trial waits for the next night. BENCH_REPORT_CMD gets a line on stdin only on a failure or when a
# candidate beats the baseline (schedule/report.sh by default).
set -u
repo=$(cd "$(dirname "$0")/.." && pwd)
tree="$repo/runs/nightly-tree"
mkdir -p "$repo/runs"
exec >> "$repo/runs/nightly.log" 2>&1
echo "== $(date -u +%FT%TZ) nightly"
export BENCH_REPORT_CMD="${BENCH_REPORT_CMD:-$repo/schedule/report.sh}"
report() { printf '%s\n' "$1" | sh -c "$BENCH_REPORT_CMD"; }
fail() { report "bencharness nightly failed: $1"; exit 1; }

git -C "$repo" fetch -q origin main || fail "git fetch"
slug=$(git -C "$repo" remote get-url origin | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')
open=$(gh pr list --repo "$slug" --state open --json headRefName -q '[.[] | select(.headRefName | startswith("nightly/"))] | length') \
  || fail "gh pr list"
# An unmerged night would make the next one run its trial again: stop until the review clears it.
[ "$open" = 0 ] || fail "a nightly PR is still open; skipped tonight"
[ -d "$tree" ] || git -C "$repo" worktree add -q --detach "$tree" origin/main || fail "worktree add"
branch="nightly/$(date -u +%F)"
# Tracked files reset to main; runs/ inside the tree is git-ignored, so trial state survives.
git -C "$tree" reset -q --hard origin/main && git -C "$tree" checkout -q -B "$branch" origin/main || fail "checkout $branch"
cd "$tree" || fail "cd $tree"
python3 -m bench.research || fail "research"
python3 -m bench.run --queue --max 1 || fail "trial runner"
[ -n "$(git status --porcelain -- results queue RESULTS.md README.md docs/charts)" ] || { echo "nothing new"; exit 0; }
git add results queue RESULTS.md README.md docs/charts
git commit -qm "nightly: records and candidates of $(date -u +%F)" || fail "commit"
head=$(git rev-parse HEAD) || fail "read head"
base=$(git rev-parse origin/main) || fail "read base"
evidence=$(python3 -m bench.evidence "$base" "$head" "$tree/runs") || fail "verify records"
printf '%s\n' "$evidence"
git push -q -u origin "$branch" || fail "push $branch"
pr=$(gh pr create --repo "$slug" --head "$branch" --title "nightly: $(date -u +%F)" \
  --body "Records and candidates written by the night run. The morning review checks them against the run files and merges." \
  ) || fail "gh pr create"
gh pr edit "$pr" --repo "$slug" --body "$evidence" || fail "post evidence"
