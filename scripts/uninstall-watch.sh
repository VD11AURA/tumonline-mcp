#!/bin/zsh
# Entfernt den TUM-Wächter (launchd-Job). Gespeicherter Stand in .state/ bleibt erhalten.
LABEL="de.tumonline-mcp.watch"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null && echo "Gestoppt." || echo "War nicht aktiv."
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist" && echo "Entfernt."
