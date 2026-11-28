# =============================================================
#  deploy.ps1 - Dateien auf den Pico W uebertragen via mpremote
#  Voraussetzung: uv sync  (installiert mpremote automatisch)
#
#  Verwendung:
#    .\scripts\deploy.ps1              # auto-Port (ersten gefundenen Pico)
#    .\scripts\deploy.ps1 -Port COM5   # expliziter Port
# =============================================================

param(
    [string]$Port = "auto"
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false

# Ins Projektverzeichnis wechseln
Set-Location (Join-Path $PSScriptRoot "..")

# mpremote finden - verschiedene Methoden fuer Windows/Unix
# Hinweis: "python -m mpremote" statt mpremote.exe, da Some App-Control-Policies
# die generierten .exe-Launcher blockieren (Smart App Control o.ae.)
$uvExe = $null
if (Get-Command uv -ErrorAction SilentlyContinue) {
    $uvExe = "uv"
} elseif (Test-Path "$env:USERPROFILE\.local\bin\uv.exe") {
    $uvExe = "$env:USERPROFILE\.local\bin\uv.exe"
}

if ($uvExe) {
    $script:mpremoteCmd = @($uvExe, "run", "python", "-m", "mpremote")
    $script:mpremoteLabel = "$uvExe run python -m mpremote"
} elseif (Test-Path ".venv\Scripts\python.exe") {
    # Windows venv
    $script:mpremoteCmd = @(".\.venv\Scripts\python.exe", "-m", "mpremote")
    $script:mpremoteLabel = ".venv\Scripts\python.exe -m mpremote"
} elseif (Test-Path ".venv\Scripts\mpremote.exe") {
    # Windows venv (Fallback)
    $script:mpremoteCmd = @(".\.venv\Scripts\mpremote.exe")
    $script:mpremoteLabel = ".venv\Scripts\mpremote.exe"
} elseif (Test-Path ".venv/bin/python") {
    # Unix venv
    $script:mpremoteCmd = @(".\.venv/bin/python", "-m", "mpremote")
    $script:mpremoteLabel = ".venv/bin/python -m mpremote"
} else {
    Write-Error "Fehler: mpremote nicht gefunden!`n  Bitte fuehre zuerst 'uv sync' aus."
    exit 1
}

function Invoke-Mpremote {
    param([Parameter(ValueFromRemainingArguments = $true)]$MpArgs)
    $exe = $script:mpremoteCmd[0]
    $rest = @($script:mpremoteCmd | Select-Object -Skip 1) + @($MpArgs)
    & $exe @rest
}

Write-Host "=== Deploying auf Pico W (Port: $Port) ==="
Write-Host "Verwende: $script:mpremoteLabel"
Write-Host ""

# lib/-Verzeichnis anlegen falls nicht vorhanden
try { Invoke-Mpremote connect $Port mkdir lib 2>$null } catch {}
try { Invoke-Mpremote connect $Port mkdir lib/umqtt 2>$null } catch {}

# Bibliotheken uebertragen
Write-Host "-> lib/umqtt/simple.py"
Invoke-Mpremote connect $Port cp lib/umqtt/simple.py :lib/umqtt/simple.py

Write-Host "-> lib/pn522.py"
Invoke-Mpremote connect $Port cp lib/pn522.py :lib/pn522.py

Write-Host "-> lib/rc522.py"
Invoke-Mpremote connect $Port cp lib/rc522.py :lib/rc522.py

Write-Host "-> lib/mqtt_manager.py"
Invoke-Mpremote connect $Port cp lib/mqtt_manager.py :lib/mqtt_manager.py

Write-Host "-> lib/wifi.py"
Invoke-Mpremote connect $Port cp lib/wifi.py :lib/wifi.py

# Konfiguration und Hauptprogramm
Write-Host "-> config.json"
Invoke-Mpremote connect $Port cp config.json :config.json

Write-Host "-> config.py"
Invoke-Mpremote connect $Port cp config.py :config.py

Write-Host "-> main.py"
Invoke-Mpremote connect $Port cp main.py :main.py

Write-Host ""
Write-Host "Fertig! REPL wird gestartet..."
Write-Host "  Druecke Ctrl+D fuer Soft-Reset (main.py startet)"
Write-Host "  (Strg+X zum Beenden)"
Write-Host ""

# REPL starten
Invoke-Mpremote connect $Port repl
