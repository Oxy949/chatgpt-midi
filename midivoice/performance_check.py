"""Read-only /proc-based CPU snapshot during live Chromium+ALSA session."""

from __future__ import annotations
import os
from pathlib import Path
import time

NAMES = {
    "chromium",
    "chrome",
    "Xvfb",
    "x11vnc",
    "pulseaudio",
    "sox",
    "arecord",
    "aplay",
    "python3",
    "python",
    "maind",
}


def read_processes():
    result = {}
    for path in Path("/proc").iterdir():
        if not path.name.isdecimal():
            continue
        try:
            stat = (path / "stat").read_text(encoding="utf-8")
            # comm may contain spaces or parentheses: parse after last ')' .
            tail = stat.rsplit(")", 1)[1].strip().split()
            name = (path / "comm").read_text(encoding="utf-8").strip()
            if name not in NAMES:
                continue
            ticks = int(tail[11]) + int(tail[12])  # fields 14,15 = utime,stime
            mem = (path / "status").read_text(encoding="utf-8").splitlines()
            rss = next(
                (int(line.split()[1]) for line in mem if line.startswith("VmRSS:")), 0
            )
            result[int(path.name)] = (name, ticks, rss)
        except (OSError, ValueError, IndexError, StopIteration):
            continue
    return result


def main():
    a = read_processes()
    time.sleep(2)
    b = read_processes()
    clk = os.sysconf("SC_CLK_TCK")
    print(
        "=== Approx CPU per process (100% = one fully occupied core, 2-sec sample) ==="
    )
    rows = []
    for pid, (name, ticks, rss) in b.items():
        before = a.get(pid)
        if not before or before[0] != name:
            continue
        cpu = max(0, ticks - before[1]) * 100 / (2 * clk)
        rows.append((cpu, rss, pid, name))
    for cpu, rss, pid, name in sorted(rows, reverse=True)[:25]:
        print(f"{pid:>7} {name:<14} CPU={cpu:6.1f}%  RSS={rss / 1024:7.1f} MB")
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if line.startswith(("MemAvailable:", "SwapTotal:")):
            print(line)


if __name__ == "__main__":
    main()
