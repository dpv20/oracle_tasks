"""SqlclRunner: thin wrapper around invoking `sql.exe`.

`-S` silences the banner, `-L` makes login failures fail fast (no interactive
password retry). `CREATE_NO_WINDOW` keeps the SQLcl console from flashing.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
_SQLCL_CREATION_FLAGS = _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002


@contextmanager
def _keep_windows_awake():
    """Ask Windows not to sleep or turn off the display while SQLcl is busy."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        active = bool(kernel32.SetThreadExecutionState(
            _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED
        ))
    except (AttributeError, OSError) as e:
        active = False
        log.debug("Could not request Windows awake mode: %s", e)

    try:
        yield
    finally:
        if active:
            try:
                kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
            except OSError as e:
                log.debug("Could not restore Windows execution state: %s", e)


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class SqlclRunner:
    def __init__(self, sqlcl_exe: str | Path):
        self.exe = str(sqlcl_exe)

    def run_query(
        self,
        connection: str,
        sql: str,
        timeout: float = 30.0,
        cancel_event: threading.Event | None = None,
    ) -> RunResult:
        """Run a one-off SQL statement and capture its output.

        `connection` is what SQLcl expects after `-S`: `user[schema]/pass@DB`.
        The SQL is fed via stdin to avoid quoting issues on Windows.
        """
        script = (
            "set heading off\n"
            "set feedback off\n"
            "set pagesize 0\n"
            f"{sql.rstrip().rstrip(';')};\n"
            "exit\n"
        )
        return self._invoke(
            [self.exe, "-S", "-L", connection],
            stdin=script,
            timeout=timeout,
            cancel_event=cancel_event,
        )

    def run_script(
        self,
        connection: str,
        script_path: str | Path,
        args: list[str] | None = None,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> RunResult:
        """Run a SQL script file with optional positional args (`&1`, `&2`, ...)."""
        if not args:
            try:
                script_text = Path(script_path).read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                return RunResult(127, "", f"SQL script not found: {script_path} ({e})")
            return self._invoke(
                [self.exe, "-S", "-L", connection],
                stdin=script_text,
                timeout=timeout,
                cancel_event=cancel_event,
            )

        cmd: list[str] = [self.exe, "-S", "-L", connection, f"@{script_path}"]
        if args:
            cmd.extend(args)
        return self._invoke(cmd, stdin=None, timeout=timeout, cancel_event=cancel_event)

    def _invoke(
        self,
        cmd: list[str],
        stdin: str | None,
        timeout: float | None,
        cancel_event: threading.Event | None = None,
    ) -> RunResult:
        if cancel_event is not None and cancel_event.is_set():
            return RunResult(130, "", "Cancelled")
        with _keep_windows_awake():
            if cancel_event is None:
                try:
                    proc = subprocess.run(
                        cmd,
                        input=stdin,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=timeout,
                        creationflags=_SQLCL_CREATION_FLAGS,
                    )
                except FileNotFoundError as e:
                    log.error("SQLcl binary not found at %s: %s", self.exe, e)
                    return RunResult(127, "", f"SQLcl not found: {self.exe}")
                except subprocess.TimeoutExpired as e:
                    log.error("SQLcl timed out after %ss", timeout)
                    return RunResult(124, e.stdout or "", f"Timed out after {timeout}s")
                return RunResult(proc.returncode, proc.stdout or "", proc.stderr or "")

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE if stdin is not None else None,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=_SQLCL_CREATION_FLAGS,
                )
            except FileNotFoundError as e:
                log.error("SQLcl binary not found at %s: %s", self.exe, e)
                return RunResult(127, "", f"SQLcl not found: {self.exe}")

            deadline = None if timeout is None else time.monotonic() + timeout
            input_data = stdin
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    return self._stop_process(proc, "Cancelled", 130)

                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    log.error("SQLcl timed out after %ss", timeout)
                    return self._stop_process(proc, f"Timed out after {timeout}s", 124)

                try:
                    poll_timeout = 0.2 if remaining is None else min(0.2, remaining)
                    stdout, stderr = proc.communicate(input=input_data, timeout=poll_timeout)
                    return RunResult(proc.returncode, stdout or "", stderr or "")
                except subprocess.TimeoutExpired:
                    input_data = None

    @staticmethod
    def _kill_process_tree(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=5,
                    creationflags=_CREATE_NO_WINDOW,
                )
                return
            except (OSError, subprocess.TimeoutExpired) as e:
                log.debug("Could not taskkill SQLcl process tree %s: %s", proc.pid, e)
        try:
            proc.kill()
        except OSError as e:
            log.debug("Could not kill SQLcl process %s: %s", proc.pid, e)

    @classmethod
    def _stop_process(cls, proc: subprocess.Popen, message: str, code: int) -> RunResult:
        if os.name == "nt":
            cls._kill_process_tree(proc)
        else:
            try:
                proc.terminate()
            except OSError as e:
                log.debug("Could not terminate SQLcl process %s: %s", proc.pid, e)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            cls._kill_process_tree(proc)
            try:
                stdout, stderr = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", message
        except OSError:
            stdout, stderr = "", message
        return RunResult(code, stdout or "", (stderr or message))
