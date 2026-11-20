"""Which projects use the machine's CPU right now (#520): sample /proc twice, `secs` apart, and sum each process's
CPU share by the repository its working directory lies in, split into pytest (a gate or a test run) and the rest.

Usage: python cpu_by_project.py [secs] [samples]
Prints only project directory names and CPU numbers, no command lines.
"""

import os
import sys
import time
from collections import defaultdict
from pathlib import Path

HZ = os.sysconf("SC_CLK_TCK")


def snapshot() -> dict[int, tuple[int, str, bool]]:
    out = {}
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            stat = (p / "stat").read_text().rsplit(")", 1)[1].split()
            ticks = int(stat[11]) + int(stat[12])
            cwd = os.readlink(p / "cwd")
            cmd = (p / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except (OSError, IndexError, ValueError):
            continue
        out[int(p.name)] = (ticks, cwd, "pytest" in cmd or "xdist" in cmd or "execnet" in cmd or "exec(eval(" in cmd)
    return out


def project(cwd: str) -> str:
    parts = Path(cwd).parts
    if "GitHub" in parts:
        i = parts.index("GitHub")
        name = parts[i + 1] if len(parts) > i + 1 else "?"
        return name.replace("-worktrees", "")
    return "(other)"


def main() -> None:
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 5
    samples = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    print(f"cores {os.cpu_count()}")
    for _ in range(samples):
        a = snapshot()
        time.sleep(secs)
        b = snapshot()
        use: dict[tuple[str, bool], float] = defaultdict(float)
        for pid, (ticks, cwd, test) in b.items():
            if pid in a:
                use[(project(cwd), test)] += (ticks - a[pid][0]) / HZ / secs
        total = sum(use.values())
        top = sorted(use.items(), key=lambda kv: -kv[1])[:8]
        load = os.getloadavg()[0]
        print(
            f"load1 {load:.0f}, busy cores {total:.1f}: "
            + ", ".join(f"{p}{' tests' if t else ''} {c:.1f}" for (p, t), c in top)
        )


if __name__ == "__main__":
    main()
