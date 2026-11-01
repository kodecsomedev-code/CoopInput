# Open source status and dependency caveats

This repository offers the **original CoopInput source code** under MIT,
**assuming the publisher has rights to license the original code and accepts
contributions under MIT**. Keep third-party copyright notices intact.

It cannot accurately be advertised as a *completely open-source system* while
it requires the Interception driver: its original author says driver/installer
source is not generally provided under the same terms as the user library.
Do **not** put `interception.dll`, drivers, or `install-interception.exe` in
public releases without confirming redistribution rights for your intended use.

To make the whole stack open source, the Interception dependency would need to
be replaced by a suitable open-source and appropriately signed Windows input
filter/driver, which is a separate engineering project.

MinHook is BSD-style licensed; see `minhook/LICENSE.txt`.

This guidance is not legal advice. Obtain permission for any source code you do
not own before publishing it with an MIT license.
