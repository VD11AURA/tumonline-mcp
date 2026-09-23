#!/bin/zsh
# Installiert den TUM-Wächter als launchd-Job (läuft stündlich + bei Anmeldung).
# Aufruf: ./scripts/install-watch.sh [Intervall in Minuten, Standard 60]
set -euo pipefail

LABEL="de.tumonline-mcp.watch"
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
UV="$(command -v uv || echo /opt/homebrew/bin/uv)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/tumonline-watch.log"
INTERVAL=$(( ${1:-60} * 60 ))

[[ -x "$UV" ]] || { echo "uv nicht gefunden."; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$UV</string><string>run</string><string>--directory</string><string>$PROJECT</string><string>tum-watch</string>
  </array>
  <key>StartInterval</key><integer>$INTERVAL</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installiert: $PLIST (alle $(( INTERVAL / 60 )) Minuten)"
echo "Erster Lauf startet jetzt, warte auf Ergebnis …"

start_size=$(stat -f%z "$LOG" 2>/dev/null || echo 0)
for _ in {1..60}; do
  if tail -c +$((start_size + 1)) "$LOG" 2>/dev/null | grep -qE "Lauf beendet|Operation not permitted|rror"; then break; fi
  sleep 1
done
echo "--- Log ---"; tail -c +$((start_size + 1)) "$LOG" 2>/dev/null | tail -8

if tail -c +$((start_size + 1)) "$LOG" 2>/dev/null | grep -q "Operation not permitted"; then
  echo
  echo "macOS blockiert den Zugriff des Hintergrundjobs auf den Projektordner (Schreibtisch)."
  echo "Lösung: Projekt aus ~/Desktop verschieben (z. B. nach ~/Projekte) oder Claude bitten, das zu tun."
  exit 1
elif tail -c +$((start_size + 1)) "$LOG" 2>/dev/null | grep -q "Lauf beendet"; then
  echo
  echo "Läuft. Du solltest eine Mitteilung 'TUM-Wächter aktiv' gesehen haben."
  echo "Keine Mitteilung? Systemeinstellungen → Mitteilungen → 'Skripteditor' erlauben."
else
  echo "Noch kein Ergebnis. Log prüfen: tail -f $LOG"
fi
