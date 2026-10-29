"""ADB development helper. Always addresses the selected station explicitly.

Remote scripts are uploaded as files: legacy station adbd can corrupt shell
quoting and does not reliably return remote exit codes or forward stdin EOF.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import hashlib
import re
import shlex
import subprocess
import tarfile
import tempfile
import sys
from uuid import uuid4

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_SERIAL = "192.168.1.79:5555"
DEFAULT_DESTINATION = "/data/midi-chatgpt-voice-refactor"


@dataclass
class Result:
    returncode: int
    output: str


class Device:
    def __init__(self, serial: str = DEFAULT_SERIAL, adb: str = "adb"):
        self.serial = serial
        self.adb = adb

    def command(self, *arguments: str, timeout=30):
        return subprocess.run(
            [self.adb, "-s", self.serial, *arguments],
            capture_output=True,
            timeout=timeout,
        )

    def run(self, script: str, *, timeout: float = 60) -> Result:
        identifier = uuid4().hex
        remote = f"/tmp/midivoice-dev-{identifier}.sh"
        marker = f"__MIDIVOICE_EXIT_{identifier}__="
        wrapper = (
            "#!/bin/sh\n"
            f'trap \'midi_rc=$?; printf "\\n{marker}%s\\n" "$midi_rc"; '
            f"rm -f {remote}' EXIT\n"
            "set -eu\n" + script + "\n"
        )
        with tempfile.TemporaryDirectory(prefix="midi-adb-") as directory:
            local = Path(directory) / "run.sh"
            local.write_text(wrapper, encoding="utf-8", newline="\n")
            pushed = self.command("push", str(local), remote)
            if pushed.returncode:
                raise RuntimeError(pushed.stderr.decode(errors="replace"))
            response = self.command("shell", "sh", remote, timeout=timeout)
        output = (
            (response.stdout + response.stderr)
            .decode("utf-8", errors="replace")
            .replace("\r", "")
        )
        match = re.search(re.escape(marker) + r"(\d+)\n?", output)
        if match is None:
            raise RuntimeError("ADB returned no remote exit status: " + output[-1000:])
        return Result(int(match[1]), output[: match.start()] + output[match.end() :])

    def deploy(self, destination: str = DEFAULT_DESTINATION) -> Result:
        # Deliberately exclude the existing deployment and browser/account state.
        if not re.fullmatch(r"/data/midi-chatgpt-voice-[A-Za-z0-9_-]+", destination):
            raise ValueError("Use a /data/midi-chatgpt-voice-NAME staging directory")
        with tempfile.TemporaryDirectory(prefix="midi-deploy-") as directory:
            archive = Path(directory) / "source.tar"
            with tarfile.open(archive, "w") as bundle:
                for name in (
                    "midivoice",
                    "scripts",
                    "tests",
                    "docs",
                    "tools",
                    "README.md",
                    "pyproject.toml",
                    "scripts-collect-midi.sh",
                ):
                    source = PROJECT / name
                    paths = sorted(source.rglob("*")) if source.is_dir() else [source]
                    for path in paths:
                        if not path.is_file() or "__pycache__" in path.parts:
                            continue
                        info = bundle.gettarinfo(
                            str(path), path.relative_to(PROJECT).as_posix()
                        )
                        info.mode = (
                            0o755
                            if path.suffix == ".sh" or path.name == "midi-chromium"
                            else 0o644
                        )
                        with path.open("rb") as content:
                            bundle.addfile(info, content)
            remote = f"/tmp/midivoice-source-{uuid4().hex}.tar"
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            pushed = self.command("push", str(archive), remote)
            if pushed.returncode:
                raise RuntimeError(pushed.stderr.decode(errors="replace"))
            return self.run(
                f"printf '%s  %s\\n' {digest} {remote} | sha256sum -c -\n"
                f"mkdir -p {shlex.quote(destination)}\n"
                f"tar -xf {remote} -C {shlex.quote(destination)}\n"
                f"rm -f {remote}"
            )


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", default=DEFAULT_SERIAL)
    parser.add_argument("--destination", default=DEFAULT_DESTINATION)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("action", choices=("check", "deploy", "test", "exec"))
    parser.add_argument("script", nargs="?", help="Local shell script for exec")
    args = parser.parse_args()
    device = Device(args.serial)
    if args.action == "deploy":
        result = device.deploy(args.destination)
    elif args.action == "test":
        result = device.run(
            f"cd {shlex.quote(args.destination)}\n"
            "sh scripts/prepare-alpine-runtime.sh\n"
            "python3 -m unittest discover -s tests -v",
            timeout=args.timeout,
        )
    elif args.action == "exec":
        if not args.script:
            parser.error("exec requires a local script file")
        result = device.run(
            Path(args.script).read_text(encoding="utf-8"), timeout=args.timeout
        )
    else:
        result = device.run("id\nuname -a\npython3 --version\ndf -h /data")
    print(result.output, end="")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
