param(
    [switch]$SkipHookBuild,
    [switch]$SkipInstaller
)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
Write-Host 'CoopInput 12.9.0 — Windows build' -ForegroundColor Cyan
if (-not $IsWindows -and $PSVersionTable.PSEdition -ne 'Desktop') {
    throw 'Run this script on Windows x64 with PowerShell.'
}
$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) { $python = @('py', '-3') }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $python = @('python') }
else { throw 'Python x64 not found. Install Python first.' }
function Invoke-Py([string[]] $argList) {
    if ($python.Count -eq 2) { & py -3 @argList }
    else { & python @argList }
    if ($LASTEXITCODE -ne 0) { throw "Python command failed: $argList" }
}
Invoke-Py -argList @('-c', 'import platform,sys; assert sys.maxsize > 2**32, "Python must be x64"; print(sys.version)')
Invoke-Py -argList @('tools/check_release.py')
if (-not $SkipHookBuild) {
    Write-Host 'Building game hook from C source...' -ForegroundColor Cyan
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'BUILD_HOOK.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Game hook build failed.' }
}
if (-not (Test-Path 'coopinput_hook.dll')) {
    throw 'coopinput_hook.dll missing. Build it from source with BUILD_HOOK.ps1.'
}
# When the user supplies the x64 Interception library locally, bundle it with
# the portable EXE and installer. This is a local, license-conditional build;
# public CI and clean source checkouts remain driver/DLL-free.
$interceptionDll = Join-Path $PSScriptRoot 'vendor/Interception/x64/interception.dll'
$includeInterception = Test-Path -LiteralPath $interceptionDll
if ($includeInterception) {
    Write-Host 'Bundling user-provided x64 interception.dll (noncommercial licensing conditions apply).' -ForegroundColor Cyan
    # Verify the file is a real x64 PE DLL before packaging it.
    $dllBytes = [System.IO.File]::ReadAllBytes($interceptionDll)
    if ($dllBytes.Length -lt 64 -or $dllBytes[0] -ne 0x4D -or $dllBytes[1] -ne 0x5A) { throw 'Bad interception.dll: not a PE binary.' }
    $peOffset = [BitConverter]::ToInt32($dllBytes, 0x3C)
    if ($peOffset -lt 0 -or $peOffset + 6 -gt $dllBytes.Length) { throw 'Bad interception.dll: invalid PE header.' }
    if ($dllBytes[$peOffset] -ne 0x50 -or $dllBytes[$peOffset+1] -ne 0x45 -or $dllBytes[$peOffset+2] -ne 0x00 -or $dllBytes[$peOffset+3] -ne 0x00) { throw 'Bad interception.dll: invalid PE signature.' }
    if ([BitConverter]::ToUInt16($dllBytes, $peOffset + 4) -ne 0x8664) { throw 'Only the x64 interception.dll is supported.' }
}
else {
    Write-Host 'No local interception.dll found; building app without the optional third-party library.' -ForegroundColor Yellow
}
Write-Host 'Installing PyInstaller in the active Python environment...' -ForegroundColor Cyan
Invoke-Py -argList @('-m','pip','install','-r','requirements-build.txt')
Write-Host 'Bundling CoopInput.exe...' -ForegroundColor Cyan
$pyinstallerArgs = @('-m','PyInstaller','--noconfirm','--clean','--onedir','--contents-directory','.','--windowed',
    '--name','CoopInput','--icon','assets/CoopInput.ico','--add-data','assets/CoopInput.ico:.','--add-binary','coopinput_hook.dll:.')
if ($includeInterception) { $pyinstallerArgs += @('--add-binary', "${interceptionDll}:.") }
$pyinstallerArgs += 'coopinput.py'
Invoke-Py -argList $pyinstallerArgs
if (-not (Test-Path 'dist/CoopInput/CoopInput.exe')) { throw 'PyInstaller did not create the expected EXE.' }
if (-not (Test-Path 'dist/CoopInput/coopinput_hook.dll')) { throw 'Game hook DLL missing from distribution.' }
if ($includeInterception -and -not (Test-Path 'dist/CoopInput/interception.dll')) { throw 'Expected bundled interception.dll was not copied into the EXE folder.' }
if (-not $includeInterception -and (Test-Path 'dist/CoopInput/interception.dll')) { throw 'Unexpected Interception DLL in public CI distribution.' }
if (-not $SkipInstaller) {
    $isccPaths = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    $iscc = $isccPaths | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if (-not $iscc) { throw 'Inno Setup 6 not found. Install from https://jrsoftware.org/isinfo.php' }
    Write-Host 'Compiling Inno Setup installer...' -ForegroundColor Cyan
    & $iscc 'installer.iss'
    if ($LASTEXITCODE -ne 0) { throw 'Inno Setup compilation failed.' }
    if (-not (Test-Path 'output/CoopInput-Setup-12.9.0.exe')) { throw 'Installer not produced.' }
    Write-Host 'SUCCESS: output/CoopInput-Setup-12.9.0.exe' -ForegroundColor Green
}
Write-Host 'Standalone app: dist/CoopInput/CoopInput.exe' -ForegroundColor Green
if ($includeInterception) { Write-Host 'Bundled: both CoopInput game hook and Interception user-mode DLL.' -ForegroundColor Green }
else { Write-Host 'External x64 interception.dll is still required.' -ForegroundColor Yellow }
Write-Host 'NOTE: Interception kernel driver still requires separate administrator-approved installation.' -ForegroundColor Yellow
