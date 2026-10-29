"""Small process lifecycle helpers shared by the audio bridges."""

from contextlib import contextmanager
import contextlib
import os
import signal
import subprocess
from threading import Event


@contextmanager
def stop_signals():
    stopping = Event()
    previous = {}
    try:
        for number in (signal.SIGTERM, signal.SIGINT):
            previous[number] = signal.signal(number, lambda *_: stopping.set())
        yield stopping
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def terminate(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
    finally:
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()


@contextmanager
def managed_process(command, **kwargs):
    """Own a complete subprocess group, including helpers left after a timeout."""
    proc = subprocess.Popen(command, start_new_session=os.name == "posix", **kwargs)
    try:
        yield proc
    finally:
        if os.name == "posix":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=2)
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
        terminate(proc)
