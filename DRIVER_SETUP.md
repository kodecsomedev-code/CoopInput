# Install the required Interception input driver (Windows x64)

**The main CoopInput installer intentionally does not silently install a driver.**
The Interception dependency comes from a separate project with its own license.

1. Get the official Interception distribution from
   https://github.com/oblitum/Interception (follow the upstream release link).
2. Read the upstream license and installation instructions. If you agree,
   install its driver using its own installer **as Administrator**.
   Reboot if the upstream driver installer instructs you to.
3. **Local bundled installer:** if you built with the included `vendor/Interception/x64/interception.dll`, the library is installed beside `CoopInput.exe` automatically. No manual copy is needed. **Clean public CI build:** download the upstream x64 `interception.dll` and use the `INSTALL_INTERCEPTION_LIBRARY.cmd` helper to copy it into the installed application folder. This helper never installs the kernel driver.
4. Launch CoopInput from the Start Menu.

If the DLL cannot be loaded, verify the file is x64, the driver is installed,
and the antivirus has not quarantined it. Do not download unofficial copies
from random DLL sites.

**For developers:** `coopinput_hook.dll` is different: it is built from this
repository's C source using Visual Studio C++ Build Tools + MinHook.
