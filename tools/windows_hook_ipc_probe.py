"""Check the actual DLL worker's cross-process named mapping handshake.

Unlike the platform-neutral unit tests, this runs on the Windows CI runner
after BUILD_HOOK.ps1. No game or input driver is needed: a fresh child process
loads the DLL, and its worker must open a mapping created by this process.
"""

import mmap
import struct
import subprocess
import sys
import time
from pathlib import Path


def main():
    if sys.platform != "win32":
        raise RuntimeError("This probe requires Windows")
    hook = Path(__file__).resolve().parent.parent / "coopinput_hook.dll"
    if not hook.is_file():
        raise FileNotFoundError(hook)

    child_code = (
        "import ctypes, sys, time; "
        "sys.stdin.readline(); "
        "hook = ctypes.WinDLL(sys.argv[1]); "
        "time.sleep(8)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", child_code, str(hook)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        # Layout: 8 four-byte fields, then 2048 16-byte input events.
        # This is the ABI-8 Shared struct from coopinput_hook.c.
        with mmap.mmap(-1, 32 + 2048 * 16, tagname=f"CoopInput_{process.pid}") as section:
            struct.pack_into("<I", section, 0, 0x504F4F43)
            process.stdin.write(b"go\n")
            process.stdin.flush()
            limit = time.monotonic() + 5
            stage = 0
            while time.monotonic() < limit:
                stage = (struct.unpack_from("<I", section, 28)[0] >> 16) & 0xFF
                if stage:
                    break
                if process.poll() is not None:
                    break
                time.sleep(0.05)
            if not stage:
                detail = process.stderr.read(1000).decode(errors="replace") if process.poll() is not None else ""
                raise AssertionError(f"DLL worker never opened cross-process mapping; pid={process.pid}, {detail}")
            if stage >= 0x80:
                raise AssertionError(f"DLL startup failed at 0x{stage:02X}")
            print(f"PASS: DLL in child pid={process.pid} opened named mapping; boot stage={stage}")
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=5)


if __name__ == "__main__":
    main()
