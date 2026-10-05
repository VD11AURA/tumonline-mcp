#!/bin/sh
# Installiert oder aktualisiert den TUMonline-MCP-Server (macOS/Linux) und richtet ihn ein.
#   curl -LsSf https://raw.githubusercontent.com/VD11AURA/tumonline-mcp/main/install.sh | sh -s -- [TUM-Kennung]
set -eu
SOURCE="https://github.com/VD11AURA/tumonline-mcp/archive/refs/heads/main.zip"

if ! command -v uv >/dev/null 2>&1; then
  echo "Installiere uv …"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  PATH="$HOME/.local/bin:$PATH"
fi

uv tool install --reinstall --quiet "$SOURCE"
uv tool update-shell >/dev/null 2>&1 || true
SETUP="$(uv tool dir --bin)/tumonline-setup"

# Bei `curl | sh` ist stdin das Skript; für die Abfrage der Kennung das Terminal nehmen.
if (exec </dev/tty) 2>/dev/null; then
  "$SETUP" "$@" </dev/tty
else
  "$SETUP" "$@"
fi
