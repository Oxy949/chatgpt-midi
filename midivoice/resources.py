"""Read-only Linux process memory estimates for embedded feasibility checks."""

from pathlib import Path


def process_tree(root_pid: int, procfs: Path = Path("/proc")) -> set[int]:
    # /proc/PID/task/TID/children requires CONFIG_CHECKPOINT_RESTORE, which
    # the Midi kernel does not enable. PPid also covers worker-thread spawns
    # and lets us scan procfs once rather than opening every Chromium thread.
    children: dict[int, list[int]] = {}
    try:
        processes = list(procfs.iterdir())
    except OSError:
        processes = []
    for process in processes:
        if not process.name.isdecimal():
            continue
        try:
            status = (process / "status").read_text(encoding="utf-8")
            parent = next(
                line for line in status.splitlines() if line.startswith("PPid:")
            )
            parent_pid = int(parent.partition(":")[2])
            children.setdefault(parent_pid, []).append(int(process.name))
        except (OSError, ValueError, StopIteration):
            continue  # Processes may exit or become inaccessible during the scan.
    visited = set()
    pending = [root_pid]
    while pending:
        pid = pending.pop()
        if pid in visited:
            continue
        visited.add(pid)
        pending.extend(children.get(pid, ()))
    return visited


def browser_pss_kb(root_pid: int, procfs: Path = Path("/proc")) -> int | None:
    """Proportional set size (PSS), including Chromium subprocesses."""
    total = 0
    found = False
    for pid in process_tree(root_pid, procfs):
        try:
            for line in (
                (procfs / str(pid) / "smaps_rollup")
                .read_text(encoding="utf-8")
                .splitlines()
            ):
                if line.startswith("Pss:"):
                    total += int(line.split()[1])
                    found = True
                    break
        except (OSError, ValueError):
            pass
    return total if found else None
