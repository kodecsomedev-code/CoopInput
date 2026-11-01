# One-click hook builder. Only downloads MinHook from the official tagged GitHub URL.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Write-Host 'CoopInput v8 | Building Windows x64 game hook' -ForegroundColor Cyan
Set-Location $root
$minhook = Join-Path $root 'minhook'
if (-not (Test-Path (Join-Path $minhook 'include\MinHook.h'))) {
    $url = 'https://github.com/TsudaKageyu/minhook/archive/refs/tags/v1.3.4.zip'
    $temp = Join-Path $env:TEMP 'coopinput-minhook-v1.3.4.zip'
    $extract = Join-Path $env:TEMP 'coopinput-minhook-extract'
    Write-Host "Downloading official MinHook v1.3.4 from $url"
    Invoke-WebRequest -Uri $url -OutFile $temp -UseBasicParsing
    if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
    Expand-Archive -LiteralPath $temp -DestinationPath $extract -Force
    $folder = Get-ChildItem $extract -Directory | Where-Object { $_.Name -like 'minhook-*' } | Select-Object -First 1
    if (-not $folder) { throw 'MinHook archive did not contain the expected directory.' }
    Move-Item -LiteralPath $folder.FullName -Destination $minhook
}
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path $vswhere)) {
    throw 'Install Visual Studio Build Tools 2022 (Desktop development with C++) first: https://visualstudio.microsoft.com/visual-cpp-build-tools/'
}
$vs = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw 'Visual Studio C++ x64 build tools were not found. Install the Desktop development with C++ workload.' }
$vcvars = Join-Path ($vs | Select-Object -First 1) 'VC\Auxiliary\Build\vcvars64.bat'
if (-not (Test-Path $vcvars)) { throw "Could not find vcvars64.bat at $vcvars" }
Write-Host 'Running x64 MSVC compilation...' -ForegroundColor Cyan
$command = 'call "' + $vcvars + '" && call "' + (Join-Path $root 'build_hook.cmd') + '"'
& cmd.exe /d /s /c $command
if ($LASTEXITCODE -ne 0) { throw 'C hook compilation failed.' }
$new = Join-Path $root 'coopinput_hook_v8.dll'
if (-not (Test-Path $new)) { throw 'The compiler did not produce coopinput_hook_v8.dll.' }
$old = Join-Path $root 'coopinput_hook.dll'
if (Test-Path $old) {
    $backup = Join-Path $root 'coopinput_hook_previous.dll.bak'
    Copy-Item $old $backup -Force
}
try { Copy-Item -LiteralPath $new -Destination $old -Force }
catch { throw 'Close CoopInput AND every game it was attached to, then run BUILD_HOOK.bat again. DLL is locked.' }
Write-Host 'SUCCESS: coopinput_hook.dll (v8) is ready.' -ForegroundColor Green
Write-Host 'Restart CoopInput AND the game. Choose RAW mode for Minecraft and test W/S without touching the mouse.'
