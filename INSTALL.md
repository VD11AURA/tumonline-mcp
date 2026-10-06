# Installation

Für Claude Code und Claude Desktop/Cowork, auf macOS, Linux und Windows. Jede Person braucht
ihren eigenen TUMonline-Token. Er wird über die TUM-Kennung angefordert, ein Passwort ist nicht
nötig.

## Mit einem Prompt in Claude Code

> Installiere mir den TUM-MCP-Server nach dieser Anleitung:
> https://raw.githubusercontent.com/VD11AURA/tumonline-mcp/main/INSTALL.md
> Meine TUM-Kennung ist ab12cde.

## Von Hand im Terminal

macOS/Linux:

```sh
curl -LsSf https://raw.githubusercontent.com/VD11AURA/tumonline-mcp/main/install.sh | sh -s -- ab12cde
```

Windows (PowerShell, fragt nach der Kennung):

```powershell
irm https://raw.githubusercontent.com/VD11AURA/tumonline-mcp/main/install.ps1 | iex
```

Danach weiter bei Schritt 3 unten.

## Schritte (Anleitung für Claude)

1. **TUM-Kennung** erfragen, falls nicht genannt (Format wie `ab12cde`). Nicht nach Passwort oder
   Tokens fragen, und den Nutzer nie bitten, Tokens in den Chat zu kopieren.

2. **Installieren und einrichten**:
   - macOS/Linux:
     `curl -LsSf https://raw.githubusercontent.com/VD11AURA/tumonline-mcp/main/install.sh | sh -s -- <KENNUNG>`
   - Windows: falls `uv` fehlt, mit
     `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"` installieren.
     Bei einem Update vorher laufende Server beenden (Windows sperrt die .exe, sonst bricht die
     Installation mittendrin ab):
     `Get-Process | ? { $_.Path -like "*uv\tools\tumonline-mcp*" } | Stop-Process -Force`, dann
     `uv tool install --reinstall https://github.com/VD11AURA/tumonline-mcp/archive/refs/heads/main.zip`
     und `& "$(uv tool dir --bin)\tumonline-setup.exe" <KENNUNG>`.

   Das installiert `uv` (falls nötig) und das Paket, trägt den Server in Claude Code und
   Claude Desktop/Cowork ein, legt `~/.config/tumonline-mcp/.env` an und fordert den Token an.
   Die Ausgabe dem Nutzer kurz zusammenfassen. Mehrfaches Ausführen ist unschädlich.

3. **Token aktivieren** (macht der Nutzer selbst im Browser): https://campus.tum.de → oben rechts
   auf den eigenen Namen (Visitenkarte) → **Token-Management** → Token `Claude MCP <Rechnername>`
   aktivieren und die Rechte für Kalender, Lehrveranstaltungen und Noten freigeben. Warten, bis
   der Nutzer das bestätigt, dann prüfen:
   `"$(uv tool dir --bin)/tumonline-token" --check` (Windows: `tumonline-token.exe`).

4. **Moodle (optional)**: Der Nutzer öffnet ein eigenes Terminal und führt `moodle-login` aus
   (TUM-Kennung und Passwort, Passwort verdeckt; es landet im Schlüsselbund des Betriebssystems).
   Das Passwort nie in den Chat geben lassen. Mobile-Tokens vergibt TUM-Moodle nicht mehr.

5. **Neu starten**: Claude Code neu starten; Claude Desktop ganz beenden und wieder öffnen.
   Danach testen, z. B. mit „Was steht heute in meinem Stundenplan?“ (bei Problemen das Tool
   `status` aufrufen).

## Aktualisieren, Entfernen

- Aktualisieren: denselben Installationsbefehl erneut ausführen (Token und Einstellungen bleiben).
  Wer Moodle bisher mit `moodle-token` angebunden hatte: einmal `moodle-login` ausführen, TUM-Moodle
  nimmt die alten Mobile-Tokens nicht mehr an.
- Entfernen: `uv tool uninstall tumonline-mcp`, `claude mcp remove -s user tumonline`, den Eintrag
  `tumonline` aus `claude_desktop_config.json` löschen und `~/.config/tumonline-mcp` löschen.

Weitere Einstellungen (Standard-Mensa, Studium-Ordner für den Moodle-Abgleich …) stehen in der
`.env`, siehe [.env.example](.env.example).
