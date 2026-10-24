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
python3 -c 'import sys; sys.exit("The installer requires Python 3.10 or newer for patched pip.") if sys.version_info < (3, 10) else None'
mkdir -p "$data" "$bin"
python3 -m venv "$data/venv"
"$data/venv/bin/python" -m pip install "pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70"
"$data/venv/bin/python" -m pip install --upgrade "certifi>=2026.10.19" "$package"
ln -sfn "$data/venv/bin/bench" "$bin/bench"
"$bin/bench" --help
printf '\nInstalled %s/bench. Add %s to PATH if needed.\n' "$bin" "$bin"
