# Interception library provenance and license

- DLL: `x64/interception.dll` supplied by the user from their previously working CoopInput v12.9 distribution.
- SHA-256: `ab88164c11b1b48488772d4c3bfaa4509d5b0ae9dbc5a691dc4f96f0260443c8`
- Upstream: https://github.com/oblitum/Interception (Francisco Lopes).
- License: The upstream project states noncommercial distribution rights under LGPL for the library and related binaries under its conditions, and separate commercial licensing terms. Consult the upstream documentation. See `licenses/LGPL-3.0.txt`.
- This library is **not** original CoopInput source under MIT.
- Do not push the DLL to a public repo expecting unrestricted commercial redistribution. `vendor/` is gitignored.
- Windows driver `interception.sys` is **not included**. Installing that driver still requires administrator approval using the upstream installer.
