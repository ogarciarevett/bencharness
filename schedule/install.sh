#!/bin/sh
# Install both launchd user agents (no root): night run 02:30, morning review 07:30. Usage:
#   BENCH_PILOT=/path/to/harness2-pilot BENCH_HYPERAG=/path/to/hyperag [BENCH_REPORT_TO=run:<id>] schedule/install.sh
# If runs/nightly.log stays empty after `launchctl kickstart gui/$(id -u)/com.bencharness.nightly`, macOS denied the
# volume: give the launcher it prints Full Disk Access (System Settings, Privacy & Security).
# Roll back: for a in nightly review; do launchctl bootout gui/$(id -u)/com.bencharness.$a; rm -f ~/Library/LaunchAgents/com.bencharness.$a.plist; done; rm -rf ~/Library/Application\ Support/bencharness
set -eu
repo=$(cd "$(dirname "$0")/.." && pwd)
dir="$HOME/Library/Application Support/bencharness"
mkdir -p "$repo/runs" "$HOME/Library/LaunchAgents" "$dir"
install_agent() {  # name script hour minute
  label="com.bencharness.$1" launcher="$dir/bencharness-$1" target="$HOME/Library/LaunchAgents/com.bencharness.$1.plist"
  # Built in the home folder, which launchd jobs may read; rebuilt every install so it never keeps an
  # old repo path (a Full Disk Access grant, if one was ever needed, must then be given again).
  cc -O2 -DNIGHTLY="\"$repo/schedule/$2\"" -o "$launcher" "$repo/schedule/launcher.c"
  sed -e "s#REPLACE_LABEL#$label#g" -e "s#REPLACE_LAUNCHER#$launcher#g" -e "s#REPLACE_HOME#$HOME#g" \
      -e "s#REPLACE_PILOT#$BENCH_PILOT#g" -e "s#REPLACE_HYPERAG#$BENCH_HYPERAG#g" -e "s#REPLACE_REPORT_TO#${BENCH_REPORT_TO:-}#g" \
      -e "s#REPLACE_HOUR#$3#g" -e "s#REPLACE_MINUTE#$4#g" "$repo/schedule/agent.plist" > "$target"
  plutil -lint "$target"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  # bootout can finish asynchronously: retry bootstrap for a few seconds; the last try shows the error.
  for i in 1 2 3 4 5 6 7 8 9 10; do
    [ "$i" = 10 ] && { launchctl bootstrap "gui/$(id -u)" "$target"; break; }
    launchctl bootstrap "gui/$(id -u)" "$target" 2>/dev/null && break
    sleep 1
  done
  launchctl print "gui/$(id -u)/$label" | grep -E "^\s+(state|program) ="
}
install_agent nightly nightly.sh 2 30
install_agent review review.sh 7 30
