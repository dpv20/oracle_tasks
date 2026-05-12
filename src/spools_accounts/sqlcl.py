"""SqlclRunner: thin wrapper around invoking `sql.exe`.

`-S` silences the banner, `-L` makes login failures fail fast (no interactive
password retry). `CREATE_NO_WINDOW` keeps the SQLcl console from flashing.
"""
from __future__ import annotations

import logging
import subprocess
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

    def run_query(self, connection: str, sql: str, timeout: float = 30.0) -> RunResult:
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
        return self._invoke([self.exe, "-S", "-L", connection], stdin=script, timeout=timeout)

    def run_script(
        self,
        connection: str,
        script_path: str | Path,
        args: list[str] | None = None,
        timeout: float = 180.0,
    ) -> RunResult:
        """Run a SQL script file with optional positional args (`&1`, `&2`, ...)."""
        cmd: list[str] = [self.exe, "-S", "-L", connection, f"@{script_path}"]
        if args:
            cmd.extend(args)
        return self._invoke(cmd, stdin=None, timeout=timeout)

    def _invoke(self, cmd: list[str], stdin: str | None, timeout: float) -> RunResult:
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
