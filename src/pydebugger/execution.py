"""Code-execution boundary.

The local subprocess executor improves failure isolation but is not a security sandbox.
It is disabled by default and must not be exposed to untrusted users in production.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Protocol

from .models import ExecutionResult


class CodeExecutor(Protocol):
    def execute(self, source: str) -> ExecutionResult: ...


@dataclass(frozen=True)
class DisabledExecutor:
    reason: str = "Code execution is disabled. Configure an isolated runner before enabling it."

    def execute(self, source: str) -> ExecutionResult:
        return ExecutionResult(disabled_reason=self.reason)


class LocalSubprocessExecutor:
    """Development-only executor with a deadline and a sanitized environment."""

    def __init__(self, timeout_seconds: float = 5, output_limit: int = 20_000) -> None:
        self.timeout_seconds = timeout_seconds
        self.output_limit = output_limit

    def execute(self, source: str) -> ExecutionResult:
        if len(source.encode("utf-8")) > 100_000:
            return ExecutionResult(stderr="Source exceeds the 100 KB development limit.", exit_code=2)
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="pydebugger-run-") as temporary_dir:
            run_dir = Path(temporary_dir)
            script = run_dir / "submission.py"
            script.write_text(source, encoding="utf-8")
            environment = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
            }
            try:
                completed = subprocess.run(
                    [sys.executable, "-I", str(script)],
                    cwd=run_dir,
                    env=environment,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout_seconds,
                    check=False,
                )
                stdout, stdout_truncated = self._limit(completed.stdout)
                stderr, stderr_truncated = self._limit(completed.stderr)
                return ExecutionResult(
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=completed.returncode,
                    duration_seconds=time.monotonic() - started,
                    truncated=stdout_truncated or stderr_truncated,
                )
            except subprocess.TimeoutExpired as error:
                stdout = self._decode(error.stdout)
                stderr = self._decode(error.stderr)
                stdout, stdout_truncated = self._limit(stdout)
                stderr, stderr_truncated = self._limit(stderr)
                return ExecutionResult(
                    stdout=stdout,
                    stderr=stderr or f"Execution exceeded {self.timeout_seconds:g} seconds.",
                    duration_seconds=time.monotonic() - started,
                    timed_out=True,
                    truncated=stdout_truncated or stderr_truncated,
                )

    def _limit(self, value: str) -> tuple[str, bool]:
        if len(value) <= self.output_limit:
            return value, False
        return value[: self.output_limit] + "\n[output truncated]", True

    @staticmethod
    def _decode(value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value

