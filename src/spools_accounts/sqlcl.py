"""SqlclRunner: thin wrapper around invoking `sql.exe`.

`-S` silences the banner, `-L` makes login failures fail fast (no interactive
password retry). `CREATE_NO_WINDOW` keeps the SQLcl console from flashing.
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


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
        timeout: float = 180.0,
        cancel_event: threading.Event | None = None,
    ) -> RunResult:
        """Run a SQL script file with optional positional args (`&1`, `&2`, ...)."""
        cmd: list[str] = [self.exe, "-S", "-L", connection, f"@{script_path}"]
        if args:
            cmd.extend(args)
        return self._invoke(cmd, stdin=None, timeout=timeout, cancel_event=cancel_event)

    def _invoke(
        self,
        cmd: list[str],
        stdin: str | None,
        timeout: float,
        cancel_event: threading.Event | None = None,
    ) -> RunResult:
        if cancel_event is not None and cancel_event.is_set():
            return RunResult(130, "", "Cancelled")
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
                    creationflags=_CREATE_NO_WINDOW,
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
                creationflags=_CREATE_NO_WINDOW,
            )
        except FileNotFoundError as e:
            log.error("SQLcl binary not found at %s: %s", self.exe, e)
            return RunResult(127, "", f"SQLcl not found: {self.exe}")

        deadline = time.monotonic() + timeout
        input_data = stdin
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return self._stop_process(proc, "Cancelled", 130)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                log.error("SQLcl timed out after %ss", timeout)
                return self._stop_process(proc, f"Timed out after {timeout}s", 124)

            try:
                stdout, stderr = proc.communicate(input=input_data, timeout=min(0.2, remaining))
                return RunResult(proc.returncode, stdout or "", stderr or "")
            except subprocess.TimeoutExpired:
                input_data = None

    @staticmethod
    def _stop_process(proc: subprocess.Popen, message: str, code: int) -> RunResult:
        try:
            proc.terminate()
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
        except OSError:
            stdout, stderr = proc.communicate()
        return RunResult(code, stdout or "", (stderr or message))
