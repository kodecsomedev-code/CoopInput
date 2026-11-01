# Build CoopInput with the DLL included

This local package already provides `vendor/Interception/x64/interception.dll`.

1. Install Python x64, Microsoft C++ build tools and Inno Setup 6.
2. Open PowerShell in this directory.
3. Run `powershell -ExecutionPolicy Bypass -File .\BUILD_RELEASE.ps1`.
4. Your installer will be in `output/CoopInput-Setup-12.9.0.exe`.
5. The installer includes `coopinput_hook.dll` and `interception.dll`, so users don't need to move DLLs themselves.
6. Install the separate Interception kernel driver with administrator approval, according to `DRIVER_SETUP.md`.

**Licensing:** The included Interception binary is third-party software subject to upstream LGPL noncommercial distribution terms. The entire application stack is not MIT/open-source. For commercial distribution, obtain an appropriate license from upstream. `vendor/` is gitignored so GitHub CI builds do not accidentally redistribute it.

**Windows warning:** The app and installer still need compilation and testing on a Windows computer. This archive contains source/build inputs, not a prebuilt installer.
