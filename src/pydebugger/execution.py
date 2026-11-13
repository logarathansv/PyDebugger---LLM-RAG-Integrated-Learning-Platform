"""Isolated, resource-bounded Python execution.

The supported runner launches each submission in a disposable Docker container.
It never executes student code in the Streamlit/application process.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import tempfile
from threading import Event, Lock, Thread
import time
from typing import BinaryIO, Protocol
import uuid

from .config import ConfigurationError, Settings
from .models import ExecutionJob, ExecutionResult, ExecutionState


class CodeExecutor(Protocol):
    def execute(self, source: str, execution_id: str) -> ExecutionResult: ...

    def cancel(self, execution_id: str) -> bool: ...

    def validate_runtime(self) -> None: ...


@dataclass(frozen=True)
class DisabledExecutor:
    reason: str = "Code execution is disabled by configuration."

    def execute(self, source: str, execution_id: str) -> ExecutionResult:
        return ExecutionResult(execution_id=execution_id, disabled_reason=self.reason)

    def cancel(self, execution_id: str) -> bool:
        return False

    def validate_runtime(self) -> None:
        return None


class _BoundedOutput:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._buffers = {"stdout": bytearray(), "stderr": bytearray()}
        self._total = 0
        self._lock = Lock()
        self.exceeded = Event()

    def read_stream(self, stream: BinaryIO, name: str) -> None:
        try:
            while chunk := stream.read(4096):
                with self._lock:
                    remaining = self.limit - self._total
                    if remaining > 0:
                        accepted = chunk[:remaining]
                        self._buffers[name].extend(accepted)
                        self._total += len(accepted)
                    if len(chunk) > max(remaining, 0):
                        self.exceeded.set()
        finally:
            stream.close()

    def text(self, name: str) -> str:
        with self._lock:
            value = bytes(self._buffers[name])
        return value.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class DockerExecutionPolicy:
    docker_binary: str
    image: str
    runtime: str | None
    timeout_seconds: float
    output_limit: int
    source_limit: int
    memory_mb: int
    cpus: float
    pids_limit: int
    tmpfs_mb: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "DockerExecutionPolicy":
        return cls(
            docker_binary=settings.docker_binary,
            image=settings.docker_image,
            runtime=settings.docker_runtime,
            timeout_seconds=settings.execution_timeout_seconds,
            output_limit=settings.execution_output_limit,
            source_limit=settings.execution_source_limit,
            memory_mb=settings.execution_memory_mb,
            cpus=settings.execution_cpus,
            pids_limit=settings.execution_pids_limit,
            tmpfs_mb=settings.execution_tmpfs_mb,
        )


@dataclass
class _ActiveExecution:
    container_name: str
    process: subprocess.Popen[bytes]


class DockerCodeExecutor:
    """Run one submission per hardened, disposable Linux container."""

    def __init__(self, policy: DockerExecutionPolicy) -> None:
        self.policy = policy
        self._active: dict[str, _ActiveExecution] = {}
        self._cancelled: set[str] = set()
        self._lock = Lock()

    def validate_runtime(self) -> None:
        version = self._docker_command(
            ["version", "--format", "{{.Server.Version}}"], timeout=5
        )
        if version.returncode != 0 or not version.stdout.strip():
            detail = version.stderr.strip() or "Docker daemon is unavailable"
            raise ConfigurationError(f"Docker execution backend is not ready: {detail}")
        image = self._docker_command(
            ["image", "inspect", self.policy.image], timeout=10
        )
        if image.returncode != 0:
            raise ConfigurationError(
                "The pinned execution image is not installed. Run: "
                f"docker pull {self.policy.image}"
            )

    def execute(self, source: str, execution_id: str) -> ExecutionResult:
        with self._lock:
            if execution_id in self._cancelled:
                self._cancelled.discard(execution_id)
                return ExecutionResult(execution_id=execution_id, cancelled=True)

        if len(source.encode("utf-8")) > self.policy.source_limit:
            return ExecutionResult(
                execution_id=execution_id,
                stderr="Source exceeds the configured execution limit.",
                exit_code=2,
                resource_limit_exceeded=True,
            )

        started = time.monotonic()
        container_name = f"pydebugger-{execution_id}"
        with tempfile.TemporaryDirectory(prefix="pydebugger-run-") as temporary_dir:
            script = Path(temporary_dir) / "submission.py"
            script.write_text(source, encoding="utf-8")
            command = self.build_command(script, container_name)
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=self._docker_client_environment(),
                )
            except OSError as error:
                return ExecutionResult(
                    execution_id=execution_id,
                    stderr=f"Could not start Docker: {error}",
                    exit_code=125,
                )

            active = _ActiveExecution(container_name, process)
            with self._lock:
                self._active[execution_id] = active
                cancellation_requested = execution_id in self._cancelled
            if cancellation_requested:
                self._stop(active)

            output = _BoundedOutput(self.policy.output_limit)
            stdout_thread = Thread(
                target=output.read_stream,
                args=(process.stdout, "stdout"),
                daemon=True,
            )
            stderr_thread = Thread(
                target=output.read_stream,
                args=(process.stderr, "stderr"),
                daemon=True,
            )
            stdout_thread.start()
            stderr_thread.start()

            timed_out = False
            output_exceeded = False
            deadline = started + self.policy.timeout_seconds
            try:
                while process.poll() is None:
                    if output.exceeded.is_set():
                        output_exceeded = True
                        self._stop(active)
                        break
                    if time.monotonic() >= deadline:
                        timed_out = True
                        self._stop(active)
                        break
                    time.sleep(0.02)
                try:
                    exit_code = process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    exit_code = process.wait(timeout=2)
                stdout_thread.join(timeout=2)
                stderr_thread.join(timeout=2)
                oom_killed = self._was_oom_killed(container_name)
            finally:
                self._remove_container(container_name)
                with self._lock:
                    self._active.pop(execution_id, None)
                    cancelled = execution_id in self._cancelled
                    self._cancelled.discard(execution_id)

            stderr = output.text("stderr")
            if timed_out:
                stderr = self._append_message(
                    stderr,
                    f"Execution exceeded {self.policy.timeout_seconds:g} seconds.",
                )
            if output_exceeded:
                stderr = self._append_message(
                    stderr,
                    f"Execution exceeded the {self.policy.output_limit} byte output limit.",
                )
            if oom_killed:
                stderr = self._append_message(stderr, "Execution exceeded its memory limit.")
            if cancelled:
                stderr = self._append_message(stderr, "Execution was cancelled.")

            return ExecutionResult(
                execution_id=execution_id,
                stdout=output.text("stdout"),
                stderr=stderr,
                exit_code=exit_code,
                duration_seconds=time.monotonic() - started,
                timed_out=timed_out,
                cancelled=cancelled,
                resource_limit_exceeded=output_exceeded or oom_killed,
                truncated=output_exceeded,
            )

    def cancel(self, execution_id: str) -> bool:
        with self._lock:
            active = self._active.get(execution_id)
            self._cancelled.add(execution_id)
        if active is not None:
            self._stop(active)
        return True

    def build_command(self, script: Path, container_name: str) -> list[str]:
        memory = f"{self.policy.memory_mb}m"
        mount = f"type=bind,src={script.resolve()},dst=/workspace/submission.py,readonly"
        command = [
            self.policy.docker_binary,
            "run",
            "--name",
            container_name,
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--user",
            "65534:65534",
            "--pids-limit",
            str(self.policy.pids_limit),
            "--memory",
            memory,
            "--memory-swap",
            memory,
            "--cpus",
            str(self.policy.cpus),
            "--ulimit",
            "nofile=64:64",
            "--ulimit",
            "fsize=1048576:1048576",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,nodev,size={self.policy.tmpfs_mb}m",
            "--mount",
            mount,
            "--workdir",
            "/workspace",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
        ]
        if self.policy.runtime:
            command.extend(["--runtime", self.policy.runtime])
        command.extend(
            [self.policy.image, "python", "-I", "-B", "/workspace/submission.py"]
        )
        return command

    def _stop(self, active: _ActiveExecution) -> None:
        self._remove_container(active.container_name)
        if active.process.poll() is None:
            active.process.terminate()

    def _remove_container(self, container_name: str) -> None:
        self._docker_command(["rm", "--force", container_name], timeout=5)

    def _was_oom_killed(self, container_name: str) -> bool:
        result = self._docker_command(
            ["inspect", "--format", "{{.State.OOMKilled}}", container_name],
            timeout=3,
        )
        return result.returncode == 0 and result.stdout.strip().lower() == "true"

    def _docker_command(
        self, arguments: list[str], timeout: float
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self.policy.docker_binary, *arguments],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                env=self._docker_client_environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return subprocess.CompletedProcess(
                [self.policy.docker_binary, *arguments],
                returncode=125,
                stdout="",
                stderr=str(error),
            )

    @staticmethod
    def _docker_client_environment() -> dict[str, str]:
        allowed = (
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "WINDIR",
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
        )
        return {name: os.environ[name] for name in allowed if name in os.environ}

    @staticmethod
    def _append_message(current: str, message: str) -> str:
        return f"{current.rstrip()}\n{message}".lstrip()


@dataclass
class _MutableJob:
    state: ExecutionState
    result: ExecutionResult | None = None
    error: str | None = None


class ExecutionService:
    """Manage asynchronous execution jobs and cancellation."""

    def __init__(self, executor: CodeExecutor) -> None:
        self.executor = executor
        self._jobs: dict[str, _MutableJob] = {}
        self._lock = Lock()

    def submit(self, source: str) -> ExecutionJob:
        execution_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[execution_id] = _MutableJob(ExecutionState.QUEUED)
        Thread(
            target=self._run,
            args=(execution_id, source),
            name=f"pydebugger-execution-{execution_id}",
            daemon=True,
        ).start()
        return self.get(execution_id)

    def get(self, execution_id: str) -> ExecutionJob:
        with self._lock:
            job = self._jobs.get(execution_id)
            if job is None:
                raise KeyError(f"Unknown execution job: {execution_id}")
            return ExecutionJob(execution_id, job.state, job.result, job.error)

    def cancel(self, execution_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(execution_id)
            if job is None or job.state not in {
                ExecutionState.QUEUED,
                ExecutionState.RUNNING,
            }:
                return False
            if job.state is ExecutionState.QUEUED:
                job.state = ExecutionState.CANCELLED
                job.result = ExecutionResult(
                    execution_id=execution_id,
                    cancelled=True,
                    stderr="Execution was cancelled before it started.",
                )
                return True
        return self.executor.cancel(execution_id)

    def _run(self, execution_id: str, source: str) -> None:
        with self._lock:
            if self._jobs[execution_id].state is ExecutionState.CANCELLED:
                return
            self._jobs[execution_id].state = ExecutionState.RUNNING
        try:
            result = self.executor.execute(source, execution_id)
            state = (
                ExecutionState.CANCELLED
                if result.cancelled
                else ExecutionState.FAILED
                if result.disabled_reason
                else ExecutionState.COMPLETED
            )
            with self._lock:
                job = self._jobs[execution_id]
                job.state = state
                job.result = result
        except Exception as error:
            with self._lock:
                job = self._jobs[execution_id]
                job.state = ExecutionState.FAILED
                job.error = str(error)
