# Installiert oder aktualisiert den TUMonline-MCP-Server (Windows) und richtet ihn ein.
#   irm https://raw.githubusercontent.com/VD11AURA/tumonline-mcp/main/install.ps1 | iex
$ErrorActionPreference = "Stop"
$Source = "https://github.com/VD11AURA/tumonline-mcp/archive/refs/heads/main.zip"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installiere uv ..."
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

uv tool install --reinstall --quiet $Source
uv tool update-shell | Out-Null
$Bin = (uv tool dir --bin).Trim()
& (Join-Path $Bin "tumonline-setup.exe") @args
