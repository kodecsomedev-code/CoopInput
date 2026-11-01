# Third-party notices

## MinHook (bundled source)
The `minhook/` directory is by Tsuda Kageyu and other named contributors.
It has its own permissive license. Preserve `minhook/LICENSE.txt`, including
notices for the Hacker Disassembler Engine components.

## Interception (optional local-build x64 library; kernel driver external)
CoopInput calls Francisco Lopes's Interception library/driver. Its core
driver/installers are not wholly open source, and the author distinguishes
noncommercial and commercial redistribution licenses. The local-build variant includes `vendor/Interception/x64/interception.dll` from the uploader with SHA-256 documented in `vendor/Interception/PROVENANCE.md`, plus LGPL-3.0 license text. The library and driver are separate from the MIT-licensed CoopInput code. `vendor/` is ignored by Git; a clean public source checkout and its CI have no `interception.dll`. The kernel driver and upstream driver installer are not bundled. Noncommercial conditions apply; commercial redistribution requires upstream licensing.
Refer to https://github.com/oblitum/Interception and its licenses.

## PyInstaller (build tool)
Used only to build the standalone application; see
https://pyinstaller.org/en/stable/license.html. Its bundling exception permits
redistribution of generated applications subject to dependency licensing.

## Inno Setup (build tool)
Installer compiler, not bundled into this source distribution.
https://jrsoftware.org/isinfo.php

## Windows system cursors
Loaded at runtime from each user's Windows installation; cursor binaries or
third-party artwork are not distributed with CoopInput.
