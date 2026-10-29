"""Bootstraps Chromium CDP pipe, preserving fds 3/4 across Alpine chroot.

The child stays independent of application imports while remapping descriptors,
entering Alpine and dropping privileges. Stage markers locate launch failures.
"""

from __future__ import annotations
import os
from pathlib import Path
import sys

from .alpine_exec import ALPINE_ENV

CHILD_BOOTSTRAP = r"""
import os,sys
os.write(2,b'[midi-cdp] Python bootstrap entered\n')
read_fd, write_fd = int(sys.argv[1]), int(sys.argv[2])
root, binary = sys.argv[3], sys.argv[4]
r = os.dup(read_fd)
w = os.dup(write_fd)
os.dup2(r, 3, inheritable=True)
os.dup2(w, 4, inheritable=True)
os.set_inheritable(3, True)
os.set_inheritable(4, True)
for fd in {r, w, read_fd, write_fd} - {3, 4}:
    os.close(fd)
os.write(2,b'[midi-cdp] CDP descriptors prepared\n')
if root != '-':
    os.chroot(root)
    os.chdir('/home/chatgpt')
    os.setgroups([])
    os.setgid(1000)
    os.setuid(1000)
    # Match the known-working native VNC launch, not the full Entware env.
    parent = os.environ
    env = __ALPINE_ENV__
    if parent.get('DISPLAY'):
        env['DISPLAY']=parent['DISPLAY']
    if parent.get('LANG'):
        env['LANG']=parent['LANG']
    os.write(2,b'[midi-cdp] Alpine chroot and uid1000 ready; exec Chromium\n')
else:
    env = os.environ.copy()
os.execve(binary,[binary,*sys.argv[5:]],env)
""".replace("__ALPINE_ENV__", repr(ALPINE_ENV))


def build_cdp_launch(
    binary: str,
    flags: list[str],
    read_fd: int,
    write_fd: int,
    profile: Path,
    *,
    root: Path | None = None,
    display: str | None = None,
) -> tuple[list[str], dict[str, str], list[str]]:
    """Return child bootstrap command, environment and verified browser flags."""
    env = os.environ.copy()
    if display is not None:
        env.pop("DISPLAY", None)
        if display:
            env["DISPLAY"] = display
    is_station = Path(binary).name == "midi-chromium"
    if is_station:
        from .config import alpine_root

        root = (root or alpine_root()).resolve()
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            raise RuntimeError(
                "Alpine CDP launch needs root for chroot; Chromium runs as uid 1000"
            )
        if not (root / "usr/bin/chromium").is_file():
            raise RuntimeError("Alpine Chromium is missing")
        if not profile.is_dir():
            raise RuntimeError(
                "ChatGPT profile is missing; use the working VNC login first"
            )
        if profile.stat().st_uid != 1000:
            raise RuntimeError("ChatGPT profile is not owned by Alpine uid 1000")
        from .alpine_exec import mapped_args, check_runtime

        check_runtime(root)
        effective = mapped_args(flags, root)
        root_argument = str(root)
        chromium = "/usr/bin/chromium"
    else:
        effective = flags
        root_argument = "-"
        chromium = binary
    # The bootstrap needs only os/sys; exclude host sitecustomize and package
    # imports while handing over the descriptors.
    cmd = [
        sys.executable,
        "-I",
        "-S",
        "-c",
        CHILD_BOOTSTRAP,
        str(read_fd),
        str(write_fd),
        root_argument,
        chromium,
        *effective,
    ]
    return cmd, env, effective
