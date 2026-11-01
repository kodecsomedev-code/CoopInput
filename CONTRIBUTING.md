# Contributing to CoopInput

All code contributions are welcome: bug fixes, tests, accessibility, dark UI,
virtual-pointer compatibility improvements and documentation.

1. Fork the repository and create a feature branch.
2. Modify `.py` files for the UI/controller; C hook changes need MSVC build.
3. Never check in private hardware IDs, `coopinput_config.json`, logs, DLLs,
   debug symbols, or files copied from a Windows installation.
4. Run `python tools/check_release.py` (static checks), then local Windows tests.
5. Open a pull request explaining behavior, testing and compatibility.

By contributing, you agree to submit your own original changes under the MIT
license, while keeping separate licenses of dependencies and other authors.
Please do not redistribute third-party driver binaries without authorization.

For security-sensitive bug reports about injection/input hijacking, avoid
posting exploit details publicly until they can be assessed responsibly.
