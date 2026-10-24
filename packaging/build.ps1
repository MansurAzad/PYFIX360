# Build PYFIX 360 into a distributable folder (+ optional installer).
#
# Usage (from the repository root, in PowerShell):
#
#     powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#
# What it does, in order:
#   1. makes sure PyInstaller and the runtime dependencies are present
#   2. regenerates the application icon (deterministic, no design tool needed)
#   3. runs a clean onedir build from packaging\pyfix360.spec
#   4. smoke-tests the frozen CLI (``--version``) so a broken build fails here
#      rather than on the user's machine
#   5. optionally compiles the Inno Setup installer if ISCC.exe is on PATH
#
# Nothing here downloads application code or contacts the network beyond what
# pip already does.

[CmdletBinding()]
param(
    [switch]$SkipInstaller,
    [switch]$SkipTests,
    [string]$Python = "python",
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"

function Write-Step($text) {
    Write-Host ""
    Write-Host "==> $text" -ForegroundColor Cyan
}

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Step "Checking Python"
& $Python -c "import sys; print(sys.version)"
if ($LASTEXITCODE -ne 0) { throw "Python not usable: $Python" }

Write-Step "Installing build dependencies"
& $Python -m pip install --upgrade "pyinstaller>=6.6" "pillow>=10" "PySide6>=6.7" "keyring>=25"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Step "Regenerating the application icon"
& $Python "packaging\make_icon.py"
if ($LASTEXITCODE -ne 0) { throw "icon generation failed" }

if (-not $SkipTests) {
    Write-Step "Running the test suite"
    & $Python -m pytest tests -q
    if ($LASTEXITCODE -ne 0) { throw "tests failed; fix them before shipping" }
}

Write-Step "Building the onedir bundle"
& $Python -m PyInstaller "packaging\pyfix360.spec" --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$dist = Join-Path $root "dist\PYFIX360"
if (-not (Test-Path $dist)) {
    # Fall back to an alternate folder name if the spec was edited.
    $dist = Join-Path $root "dist\PYFIX360-full"
}
if (-not (Test-Path $dist)) { throw "build produced no dist folder" }
Write-Host "Bundle: $dist"

Write-Step "Smoke-testing the frozen CLI"
$cli = Join-Path $dist "PYFIX360-cli.exe"
if (-not (Test-Path $cli)) {
    # older PyInstaller names it exactly as in the spec
    $cli = Join-Path $dist "PYFIX360-cli"
}
if (Test-Path $cli) {
    & $cli --version
    if ($LASTEXITCODE -ne 0) { throw "frozen CLI failed to run" }
} else {
    Write-Warning "No CLI executable found in the bundle; skipping smoke test."
}

if (-not $SkipInstaller) {
    Write-Step "Looking for Inno Setup"
    $iscc = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($null -eq $iscc) {
        $candidates = @(
            "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
            "C:\Program Files\Inno Setup 6\ISCC.exe",
            (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
        )
        foreach ($guess in $candidates) {
            if (Test-Path $guess) { $iscc = Get-Item $guess; break }
        }
    }
    if ($null -ne $iscc) {
        $script = Join-Path $root "packaging\installer.iss"
        $path = if ($iscc -is [System.Management.Automation.CommandInfo]) {
            $iscc.Source
        } else { $iscc.FullName }
        $versionArg = if ($Version) { "/DMyAppVersion=$Version" } else { "" }
        & $path "/DMyDistDir=$dist" $versionArg $script
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
        Write-Host "Installer written to packaging\Output" -ForegroundColor Green
    } else {
        Write-Warning ("Inno Setup (ISCC.exe) not found. The folder build in " +
                       "'dist' is complete and runnable; install Inno Setup 6 " +
                       "to also produce Setup_PYFIX360.exe, then re-run with " +
                       "-SkipTests -SkipInstaller:$false.")
    }
}

Write-Step "Done"
Write-Host "Run the app:  $dist\PYFIX360.exe"
