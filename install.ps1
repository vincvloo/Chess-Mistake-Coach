# Installs Chess Mistake Coach and starts the web app (Windows).
#
#   powershell -ExecutionPolicy Bypass -File install.ps1
#
# It also puts a "Chess Mistake Coach" shortcut on your desktop and in the
# Start menu: double-click it to start the app, and use the Stop button in
# the app to close it. No command line needed after this.
#
# Safe to run again: it skips whatever is already done. Options:
#   -NoLaunch        set everything up but don't start the app
#   -NoShortcut      don't create the desktop / Start menu shortcuts
#   -SkipStockfish   don't try to install Stockfish

param(
    [switch]$NoLaunch,
    [switch]$NoShortcut,
    [switch]$SkipStockfish
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Say($message) { Write-Host "==> $message" -ForegroundColor Cyan }
function Fail($message) { Write-Host "ERROR: $message" -ForegroundColor Red; exit 1 }

# 1. Python 3.10 or newer ---------------------------------------------------
function Find-Python {
    $candidates = @(@("py", "-3"), @("python"), @("python3"))
    foreach ($c in $candidates) {
        if (-not (Get-Command $c[0] -ErrorAction SilentlyContinue)) { continue }
        try {
            $extra = @()
            if ($c.Count -gt 1) { $extra = $c[1..($c.Count - 1)] }
            $ok = & $c[0] @extra -c "import sys; print(sys.version_info >= (3, 10))" 2>$null
            if ($ok -eq "True") { return $c }
        } catch { }
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    Fail "Python 3.10 or newer was not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run this again."
}
$pyExe = $python[0]
$pyArgs = @()
if ($python.Count -gt 1) { $pyArgs = $python[1..($python.Count - 1)] }

# 2. Virtual environment + the app -------------------------------------------
$venv = Join-Path $PSScriptRoot ".venvs\chess"
$venvPython = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Say "Creating a virtual environment in .venvs\chess"
    & $pyExe @pyArgs -m venv $venv
    if ($LASTEXITCODE -ne 0) { Fail "Could not create the virtual environment." }
}

# Installed under its old name (Chess Error Tracker)? Remove that first so the old command doesn't linger.
cmd /c "`"$venvPython`" -m pip uninstall -y chess-error-tracker >nul 2>&1"

Say "Installing Chess Mistake Coach (this can take a minute the first time)"
& $venvPython -m pip install --disable-pip-version-check --quiet -e ".[web]"
if ($LASTEXITCODE -ne 0) { Fail "Installing the app failed. Scroll up for the pip error." }

# 3. Stockfish ----------------------------------------------------------------
function Find-Stockfish {
    $found = & $venvPython -c "from chess_mistake_coach.engine import find_engine; print(find_engine() or '')"
    return "$found".Trim()
}

if (-not $SkipStockfish) {
    if (Find-Stockfish) {
        Say "Stockfish found"
    } elseif (Get-Command winget -ErrorAction SilentlyContinue) {
        Say "Installing Stockfish with winget"
        winget install Stockfish --accept-package-agreements --accept-source-agreements
        if (Find-Stockfish) {
            Say "Stockfish installed"
        } else {
            Write-Host "Stockfish was installed but isn't visible yet. Close this window and run install.ps1 again if the app can't find it." -ForegroundColor Yellow
        }
    } else {
        Write-Host "Stockfish wasn't found and winget isn't available. Download the Windows build from https://stockfishchess.org/download/ and unzip it to C:\Tools\stockfish\ - the app finds it there automatically." -ForegroundColor Yellow
    }
}

# 4. Shortcuts ------------------------------------------------------------------
# pythonw runs the app with no console window; the app opens its own window.
$pythonw = Join-Path $venv "Scripts\pythonw.exe"
$icon = Join-Path $PSScriptRoot "chess_mistake_coach\web\static\icon.ico"

function New-AppShortcut($folder) {
    if (-not $folder -or -not (Test-Path $folder)) { return $null }
    $path = Join-Path $folder "Chess Mistake Coach.lnk"
    $link = (New-Object -ComObject WScript.Shell).CreateShortcut($path)
    $link.TargetPath = $pythonw
    $link.Arguments = "-m chess_mistake_coach serve"
    $link.WorkingDirectory = $PSScriptRoot
    $link.IconLocation = $icon
    $link.Description = "Start Chess Mistake Coach"
    $link.Save()
    return $path
}

if (-not $NoShortcut) {
    $made = @()
    foreach ($folder in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
        try {
            $p = New-AppShortcut $folder
            if ($p) { $made += $p }
        } catch {
            Write-Host "Couldn't create a shortcut in $folder ($($_.Exception.Message))" -ForegroundColor Yellow
        }
    }
    if ($made.Count) { Say "Shortcut created: double-click 'Chess Mistake Coach' on your desktop or in the Start menu" }
}

# 5. Start ----------------------------------------------------------------------
if ($NoLaunch) {
    Say "Done. Start the app any time with the Chess Mistake Coach shortcut."
    exit 0
}

Say "Starting Chess Mistake Coach (it opens in its own window; use its Stop button to close it)"
Start-Process -FilePath $pythonw -ArgumentList "-m", "chess_mistake_coach", "serve" -WorkingDirectory $PSScriptRoot
