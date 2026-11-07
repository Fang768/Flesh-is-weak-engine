#!/usr/bin/env python
"""Force-terminate stuck Windows processes by name (suspended procs survive taskkill)."""
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = wintypes.HANDLE
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
k32.CloseHandle.argtypes = [wintypes.HANDLE]

names = sys.argv[1:] or ["QQ", "NapCatWinBootMain"]
out = subprocess.run(
    [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -in @(%s) } | ForEach-Object { $_.Id }"
        % ",".join("'%s'" % n for n in names),
    ],
    capture_output=True,
    text=True,
)
pids = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
print("pids", pids)
for pid in pids:
    h = k32.OpenProcess(0x0001, False, pid)
    if not h:
        print(pid, "open failed", ctypes.get_last_error())
        continue
    ok = k32.TerminateProcess(h, 1)
    print(pid, "ok" if ok else f"failed {ctypes.get_last_error()}")
    k32.CloseHandle(h)
time.sleep(1)
out = subprocess.run(
    [
        "powershell",
        "-NoProfile",
        "-Command",
        "(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -in @(%s) }).Count"
        % ",".join("'%s'" % n for n in names),
    ],
    capture_output=True,
    text=True,
)
print("remaining", out.stdout.strip())
