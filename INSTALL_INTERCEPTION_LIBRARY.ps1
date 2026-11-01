# User-initiated copy of an already obtained official interception.dll.
# Does NOT install any drivers or download third-party binaries.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.MessageBox]::Show('Select the official x64 interception.dll you downloaded from the Interception project. The kernel driver must be installed separately according to upstream instructions.', 'CoopInput prerequisite') | Out-Null
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Filter = 'Interception library (interception.dll)|interception.dll'
$dialog.Title = 'Select official x64 interception.dll'
$dialog.CheckFileExists = $true
if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { exit 1 }
$target = Join-Path $PSScriptRoot 'interception.dll'
if ((Resolve-Path $dialog.FileName).Path -eq $target) {
    [System.Windows.Forms.MessageBox]::Show('Library is already in place.', 'CoopInput') | Out-Null
    exit 0
}
# Validate PE machine 0x8664 (AMD64), without ever executing the candidate DLL.
$b = [System.IO.File]::ReadAllBytes($dialog.FileName)
if ($b.Length -lt 64 -or $b[0] -ne 0x4D -or $b[1] -ne 0x5A) { throw 'Invalid PE file.' }
$peOffset = [BitConverter]::ToInt32($b, 0x3C)
if ($peOffset -lt 0 -or $peOffset + 6 -gt $b.Length) { throw 'Invalid PE header.' }
if ($b[$peOffset] -ne 0x50 -or $b[$peOffset+1] -ne 0x45) { throw 'Invalid PE signature.' }
if ([BitConverter]::ToUInt16($b, $peOffset + 4) -ne 0x8664) { throw 'Select an x64 DLL.' }
Copy-Item -LiteralPath $dialog.FileName -Destination $target -Force
[System.Windows.Forms.MessageBox]::Show('interception.dll copied. Make sure its Windows driver is installed, then launch CoopInput.', 'CoopInput') | Out-Null
