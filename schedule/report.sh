#!/bin/sh
# Send the text on stdin to the coordinator when BENCH_REPORT_TO names one; always print it.
text=$(cat)
printf '%s\n' "$text"
[ -n "${BENCH_REPORT_TO:-}" ] && orca orchestration send --to "$BENCH_REPORT_TO" --type status --subject "bencharness" --body "$text" >/dev/null
exit 0
