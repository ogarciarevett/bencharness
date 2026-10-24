#!/bin/sh
# Isolated user install; no sudo or system Python modification.
set -eu
data=${XDG_DATA_HOME:-"$HOME/.local/share"}/bencharness
bin=${BENCHARNESS_BIN_DIR:-"$HOME/.local/bin"}
package=${BENCHARNESS_PACKAGE:-bencharness}
if [ -e "$bin/bench" ] || [ -L "$bin/bench" ]; then
  [ "$(readlink "$bin/bench" || true)" = "$data/venv/bin/bench" ] || {
    echo 'Another bench command exists; choose BENCHARNESS_BIN_DIR.' >&2
    exit 1
  }
fi
mkdir -p "$data" "$bin"
python3 -m venv "$data/venv"
"$data/venv/bin/python" -m pip install --upgrade "$package"
ln -sfn "$data/venv/bin/bench" "$bin/bench"
"$bin/bench" --help
printf '\nInstalled %s/bench. Add %s to PATH if needed.\n' "$bin" "$bin"
