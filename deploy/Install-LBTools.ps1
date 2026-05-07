#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Deploys pyRevit + LB Tools to a workstation.

.DESCRIPTION
    1. Installs pyRevit (skips if already installed).
    2. Registers the LB Tools GitHub repo as a pyRevit extension.
    3. Installs required Python packages into pyRevit's CPython engine.
    4. Enables the CPython engine in pyRevit settings.

    Designed to be run silently via Intune, SCCM, or a login GPO script.
    Re-running the script is safe — each step is idempotent.

.PARAMETER PyRevitVersion
    The pyRevit release tag to install (e.g. "4.8.16.24121"). Leave blank to
    install whichever release the installer URL below points to.

.EXAMPLE
    # Interactive — run from an admin PowerShell window:
    .\Install-LBTools.ps1

    # Silent — suitable for Intune / SCCM deployment:
    powershell.exe -ExecutionPolicy Bypass -NonInteractive -File "Install-LBTools.ps1"
#>

[CmdletBinding()]
param(
    [string]$PyRevitVersion = "4.8.16.24121"
)

$ErrorActionPreference = "Stop"

# ── Configuration ─────────────────────────────────────────────────────────────

$LBToolsRepoUrl  = "https://github.com/levittbernstein/PyRevit-Integration.git"
$LBToolsName     = "LB Tools"
$PyRevitCLI      = "$env:APPDATA\pyRevit-Master\bin\pyrevit.exe"
$PyRevitInstaller = "https://github.com/eirannejad/pyRevit/releases/download/$PyRevitVersion/pyRevit_$($PyRevitVersion)_admin_install.exe"

$RequiredPackages = @("openpyxl", "Pillow", "pywin32")

# ── Helper: write a timestamped log line ──────────────────────────────────────

function Write-Step([string]$msg) {
    Write-Host "  [$(Get-Date -f 'HH:mm:ss')] $msg" -ForegroundColor Cyan
}
function Write-OK([string]$msg) {
    Write-Host "  ✓ $msg" -ForegroundColor Green
}

Write-Host ""
Write-Host "=== LB Tools — pyRevit deployment ===" -ForegroundColor White
Write-Host ""

# ── Step 1: Install pyRevit ───────────────────────────────────────────────────

Write-Step "Checking pyRevit installation..."

if (Test-Path $PyRevitCLI) {
    Write-OK "pyRevit already installed — skipping."
} else {
    Write-Step "Downloading pyRevit $PyRevitVersion installer..."
    $installerPath = Join-Path $env:TEMP "pyrevit_installer.exe"
    Invoke-WebRequest -Uri $PyRevitInstaller -OutFile $installerPath -UseBasicParsing

    Write-Step "Running pyRevit installer silently..."
    $proc = Start-Process -FilePath $installerPath -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART" -Wait -PassThru
    Remove-Item $installerPath -ErrorAction SilentlyContinue

    if ($proc.ExitCode -ne 0) {
        throw "pyRevit installer exited with code $($proc.ExitCode)"
    }
    Write-OK "pyRevit installed."
}

# ── Step 2: Register LB Tools extension ───────────────────────────────────────

Write-Step "Checking LB Tools extension registration..."

$extListRaw = & $PyRevitCLI "extensions" "list" 2>&1
if ($extListRaw -match [regex]::Escape($LBToolsRepoUrl)) {
    Write-OK "LB Tools extension already registered — skipping."
} else {
    Write-Step "Registering LB Tools from GitHub..."
    & $PyRevitCLI "extend" "lib" $LBToolsName $LBToolsRepoUrl "--branch=main"
    Write-OK "LB Tools extension registered."
}

# ── Step 3: Locate pyRevit's CPython executable ───────────────────────────────

Write-Step "Locating pyRevit CPython engine..."

$cpythonDir = $null
$searchRoots = @(
    "$env:APPDATA\pyRevit-Master\bin\cengines",
    "$env:APPDATA\pyRevit\bin\cengines"
)
foreach ($root in $searchRoots) {
    if (Test-Path $root) {
        $cpyDir = Get-ChildItem -Path $root -Filter "CPY*" -Directory |
                  Sort-Object Name -Descending | Select-Object -First 1
        if ($cpyDir -and (Test-Path (Join-Path $cpyDir.FullName "python.exe"))) {
            $cpythonDir = $cpyDir.FullName
            break
        }
    }
}

if (-not $cpythonDir) {
    throw "Could not find pyRevit CPython engine. Ensure pyRevit installed correctly."
}
$pythonExe = Join-Path $cpythonDir "python.exe"
Write-OK "CPython found: $pythonExe"

# ── Step 4: Install Python packages ───────────────────────────────────────────

Write-Step "Installing Python packages: $($RequiredPackages -join ', ')..."

foreach ($pkg in $RequiredPackages) {
    Write-Step "  pip install $pkg"
    & $pythonExe -m pip install --quiet --upgrade $pkg
    if ($LASTEXITCODE -ne 0) {
        throw "pip install $pkg failed (exit code $LASTEXITCODE)"
    }
    Write-OK "  $pkg installed / up to date."
}

# ── Step 5: Enable CPython engine in pyRevit settings ─────────────────────────

Write-Step "Enabling CPython engine in pyRevit settings..."
& $PyRevitCLI "settings" "cpython" "enable"
Write-OK "CPython engine enabled."

# ── Done ──────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "=== Deployment complete ===" -ForegroundColor Green
Write-Host "Restart Revit to load LB Tools." -ForegroundColor White
Write-Host ""
