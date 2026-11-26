<#
.SYNOPSIS
    Builds the Windows standalone app, Portable ZIP, and Inno Setup Installer.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1
#>
param(
    [string]$Version = "1.0.0",
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

if (-not $PythonExe) {
    $VenvPy = Join-Path $RepoRoot "responsa_voice\venv\Scripts\python.exe"
    if (Test-Path $VenvPy) {
        $PythonExe = $VenvPy
    } else {
        $PythonExe = "python"
    }
}

Write-Host "=== [1/5] Generating app icon ===" -ForegroundColor Cyan
& $PythonExe (Join-Path $RepoRoot "installer\make_icon.py")
if ($LASTEXITCODE -ne 0) { throw "Icon generation failed" }

Write-Host "=== [2/5] Building standalone bundle with PyInstaller ===" -ForegroundColor Cyan
& $PythonExe -m PyInstaller --clean --noconfirm (Join-Path $RepoRoot "installer\responsa_voice.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

$DistDir = Join-Path $RepoRoot "dist\ResponsaVoice"
$BuiltExe = Join-Path $DistDir "ResponsaVoice.exe"
if (-not (Test-Path $BuiltExe)) { throw "Expected executable not found: $BuiltExe" }

Write-Host "=== [3/5] Smoke-testing built executable ===" -ForegroundColor Cyan
$proc = Start-Process -FilePath $BuiltExe -ArgumentList "--self-check" -Wait -PassThru -NoNewWindow
if ($proc.ExitCode -ne 0) {
    throw "Self-check failed with exit code $($proc.ExitCode)"
}
Write-Host "Self-check passed!" -ForegroundColor Green

$OutDir = Join-Path $RepoRoot "installer_output"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

Write-Host "=== [4/5] Creating Portable ZIP ===" -ForegroundColor Cyan
$ZipPath = Join-Path $OutDir "ResponsaVoice-Portable-$Version.zip"
if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Compress-Archive -Path "$DistDir\*" -DestinationPath $ZipPath -CompressionLevel Optimal
Write-Host "Created: $ZipPath" -ForegroundColor Green

Write-Host "=== [5/5] Building Windows Installer with Inno Setup ===" -ForegroundColor Cyan
$IsccCandidates = @(
    (Get-Command iscc -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
    "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) }

$Iscc = $IsccCandidates | Select-Object -First 1
if (-not $Iscc) {
    Write-Warning "Inno Setup (ISCC.exe) not found! Install it via: winget install JRSoftware.InnoSetup"
    Write-Host "Portable ZIP is ready at: $ZipPath" -ForegroundColor Yellow
    exit 0
}

& $Iscc "/DMyAppVersion=$Version" (Join-Path $RepoRoot "installer\responsa_voice.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup build failed" }

$SetupExe = Join-Path $OutDir "ResponsaVoice-Setup-$Version.exe"
Write-Host "`nBUILD COMPLETE!" -ForegroundColor Green
Write-Host "  Installer : $SetupExe"
Write-Host "  Portable  : $ZipPath"
