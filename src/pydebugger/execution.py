"""Isolated, resource-bounded Python execution.

The supported runner launches each submission in a disposable Docker container.
It never executes student code in the Streamlit/application process.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from threading import BoundedSemaphore, Event, Lock, Thread
from typing import BinaryIO, Protocol

from .config import ConfigurationError, Settings
from .diagnostics import (
    PYTHON_RUNNER,
    extract_diagnostic,
    format_traceback,
    remove_diagnostic_records,
)
from .jobs import JobQueueFullError
from .models import (
    ExecutionJob,
    ExecutionProject,
    ExecutionResult,
    ExecutionState,
    ExecutionTestResult,
    ProjectFile,
)
from .monitoring import MonitoringStore, OperationMetric


class CodeExecutor(Protocol):
    def execute(
        self, source: str | ExecutionProject, execution_id: str
    ) -> ExecutionResult: ...

    def cancel(self, execution_id: str) -> bool: ...

    def validate_runtime(self) -> None: ...


@dataclass(frozen=True)
class DisabledExecutor:
    reason: str = "Code execution is disabled by configuration."

    def execute(
        self, source: str | ExecutionProject, execution_id: str
    ) -> ExecutionResult:
        return ExecutionResult(execution_id=execution_id, disabled_reason=self.reason)

    def cancel(self, execution_id: str) -> bool:
        return False

    def validate_runtime(self) -> None:
        return None


class _BoundedOutput:
    def __init__(self, limit: int, tail_limit: int = 65_536) -> None:
        self.limit = limit
        self.tail_limit = tail_limit
        self._buffers = {"stdout": bytearray(), "stderr": bytearray()}
        self._tails = {"stdout": bytearray(), "stderr": bytearray()}
        self._total = 0
        self._lock = Lock()
        self.exceeded = Event()

    def read_stream(self, stream: BinaryIO, name: str) -> None:
        try:
            while chunk := stream.read(4096):
                with self._lock:
                    tail = self._tails[name]
                    tail.extend(chunk)
                    if len(tail) > self.tail_limit:
                        del tail[: len(tail) - self.tail_limit]
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

    def tail_text(self, name: str) -> str:
        with self._lock:
            value = bytes(self._tails[name])
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
    def from_settings(cls, settings: Settings) -> DockerExecutionPolicy:
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

    def execute(
        self, source: str | ExecutionProject, execution_id: str
    ) -> ExecutionResult:
        project = (
            source
            if isinstance(source, ExecutionProject)
            else ExecutionProject(
                (ProjectFile("submission.py", source),), "submission.py"
            )
        )
        validation_error = self._validate_project(project)
        if validation_error:
            return ExecutionResult(
                execution_id=execution_id,
                stderr=validation_error,
                exit_code=2,
                resource_limit_exceeded="limit" in validation_error.lower(),
            )
        if not project.tests:
            return self._execute_once(project, execution_id, "")

        started = time.monotonic()
        test_results: list[ExecutionTestResult] = []
        first_failure: ExecutionResult | None = None
        for test in project.tests:
            result = self._execute_once(project, execution_id, test.stdin)
            actual = result.stdout.replace("\r\n", "\n").rstrip()
            expected = test.expected_output.replace("\r\n", "\n").rstrip()
            passed = result.exit_code == 0 and actual == expected
            test_results.append(
                ExecutionTestResult(
                    name=test.name,
                    passed=passed,
                    expected_output=test.expected_output,
                    actual_output=result.stdout,
                    stderr=result.stderr,
                    exit_code=result.exit_code,
                    duration_seconds=result.duration_seconds,
                    diagnostic=result.diagnostic,
                )
            )
            if not passed and first_failure is None:
                first_failure = result
            if result.cancelled or result.timed_out or result.resource_limit_exceeded:
                break

        failed_runtime = bool(
            first_failure and first_failure.exit_code not in {None, 0}
        )
        return ExecutionResult(
            execution_id=execution_id,
            stdout=first_failure.stdout if first_failure else "",
            stderr=first_failure.stderr if failed_runtime else "",
            exit_code=first_failure.exit_code if failed_runtime else 0,
            duration_seconds=time.monotonic() - started,
            timed_out=bool(first_failure and first_failure.timed_out),
            cancelled=bool(first_failure and first_failure.cancelled),
            resource_limit_exceeded=bool(
                first_failure and first_failure.resource_limit_exceeded
            ),
            truncated=bool(first_failure and first_failure.truncated),
            diagnostic=first_failure.diagnostic if failed_runtime else None,
            traceback=first_failure.traceback if failed_runtime else "",
            test_results=tuple(test_results),
        )

    def _execute_once(
        self, project: ExecutionProject, execution_id: str, stdin: str
    ) -> ExecutionResult:
        with self._lock:
            if execution_id in self._cancelled:
                self._cancelled.discard(execution_id)
                return ExecutionResult(execution_id=execution_id, cancelled=True)

        started = time.monotonic()
        container_name = f"pydebugger-{execution_id}"
        with tempfile.TemporaryDirectory(prefix="pydebugger-run-") as temporary_dir:
            project_dir = Path(temporary_dir)
            for project_file in project.files:
                destination = project_dir.joinpath(
                    *PurePosixPath(project_file.path).parts
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(project_file.source, encoding="utf-8")
            command = self._build_command(
                project_dir, container_name, project.entrypoint
            )
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
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
            if stdin and process.stdin is not None:
                try:
                    process.stdin.write(stdin.encode("utf-8"))
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass

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

            diagnostic = (
                extract_diagnostic(output.tail_text("stderr"))
                if exit_code not in {0, None}
                else None
            )
            stderr = remove_diagnostic_records(output.text("stderr"))
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
                stderr = self._append_message(
                    stderr, "Execution exceeded its memory limit."
                )
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
                diagnostic=diagnostic,
                traceback=format_traceback(diagnostic) if diagnostic else "",
            )

    def _validate_project(self, project: ExecutionProject) -> str | None:
        if not project.files:
            return "Project must contain at least one Python file."
        if len(project.files) > 20:
            return "Project exceeds the 20-file execution limit."
        if len(project.tests) > 10:
            return "Project exceeds the 10-test execution limit."
        seen: set[str] = set()
        total_bytes = 0
        for project_file in project.files:
            path = PurePosixPath(project_file.path)
            if (
                path.is_absolute()
                or not path.parts
                or any(part in {"", ".", ".."} for part in path.parts)
                or path.suffix != ".py"
                or any(
                    not self._valid_path_part(part, index == len(path.parts) - 1)
                    for index, part in enumerate(path.parts)
                )
            ):
                return f"Unsafe Python project path: {project_file.path!r}."
            normalized = path.as_posix()
            if normalized in seen:
                return f"Duplicate Python project path: {normalized}."
            seen.add(normalized)
            total_bytes += len(project_file.source.encode("utf-8"))
        if project.entrypoint not in seen:
            return "Project entry point must name one of the submitted Python files."
        total_bytes += sum(
            len(test.stdin.encode("utf-8")) + len(test.expected_output.encode("utf-8"))
            for test in project.tests
        )
        if total_bytes > self.policy.source_limit:
            return "Project source and test data exceed the configured execution limit."
        if any(not test.name.strip() for test in project.tests):
            return "Every test case must have a name."
        return None

    @staticmethod
    def _valid_path_part(part: str, is_file: bool) -> bool:
        candidate = part[:-3] if is_file else part
        return bool(candidate) and candidate.isidentifier()

    def cancel(self, execution_id: str) -> bool:
        with self._lock:
            active = self._active.get(execution_id)
            self._cancelled.add(execution_id)
        if active is not None:
            self._stop(active)
        return True

    def build_command(self, script: Path, container_name: str) -> list[str]:
        return self._build_command(script, container_name, "submission.py")

    def _build_command(
        self, source: Path, container_name: str, entrypoint: str
    ) -> list[str]:
        memory = f"{self.policy.memory_mb}m"
        destination = "/workspace" if source.is_dir() else "/workspace/submission.py"
        mount = f"type=bind,src={source.resolve()},dst={destination},readonly"
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
            "--env",
            f"PYDEBUGGER_ENTRYPOINT={entrypoint}",
        ]
        if self.policy.runtime:
            command.extend(["--runtime", self.policy.runtime])
        command.extend([self.policy.image, "python", "-I", "-B", "-c", PYTHON_RUNNER])
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
    error_code: str | None = None
    progress: float = 0.0
    message: str = "Queued"
    attempt: int = 0
    max_attempts: int = 1
    cancel_event: Event = field(default_factory=Event)


class ExecutionService:
    """Manage asynchronous execution jobs and cancellation."""

    def __init__(
        self,
        executor: CodeExecutor,
        *,
        workers: int = 2,
        queue_size: int = 8,
        max_attempts: int = 1,
        monitor: MonitoringStore | None = None,
    ) -> None:
        self.executor = executor
        self._max_attempts = max_attempts
        self._monitor = monitor
        self._pool = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="pydebugger-execution"
        )
        self._capacity = BoundedSemaphore(workers + queue_size)
        self._jobs: dict[str, _MutableJob] = {}
        self._lock = Lock()

    def submit(self, source: str | ExecutionProject) -> ExecutionJob:
        if not self._capacity.acquire(blocking=False):
            raise JobQueueFullError(
                "The execution queue is full. Wait for a running submission to finish."
            )
        execution_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[execution_id] = _MutableJob(
                ExecutionState.QUEUED, max_attempts=self._max_attempts
            )
        try:
            self._pool.submit(self._run, execution_id, source)
        except Exception:
            self._capacity.release()
            with self._lock:
                self._jobs.pop(execution_id, None)
            raise
        return self.get(execution_id)

    def submit_project(self, project: ExecutionProject) -> ExecutionJob:
        return self.submit(project)

    def get(self, execution_id: str) -> ExecutionJob:
        with self._lock:
            job = self._jobs.get(execution_id)
            if job is None:
                raise KeyError(f"Unknown execution job: {execution_id}")
            return ExecutionJob(
                id=execution_id,
                state=job.state,
                result=job.result,
                error=job.error,
                error_code=job.error_code,
                progress=job.progress,
                message=job.message,
                attempt=job.attempt,
                max_attempts=job.max_attempts,
            )

    def cancel(self, execution_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(execution_id)
            if job is None or job.state not in {
                ExecutionState.QUEUED,
                ExecutionState.RUNNING,
                ExecutionState.RETRYING,
            }:
                return False
            job.cancel_event.set()
            if job.state is ExecutionState.QUEUED:
                job.state = ExecutionState.CANCELLED
                job.result = ExecutionResult(
                    execution_id=execution_id,
                    cancelled=True,
                    stderr="Execution was cancelled before it started.",
                )
                job.progress = 1.0
                job.message = "Cancelled before execution started"
                job.error_code = "cancelled"
                return True
            if job.state is ExecutionState.RETRYING:
                job.state = ExecutionState.CANCELLED
                job.progress = 1.0
                job.message = "Cancelled before retry"
                job.error_code = "cancelled"
                return True
        return self.executor.cancel(execution_id)

    def shutdown(self, *, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=True)

    def _run(self, execution_id: str, source: str | ExecutionProject) -> None:
        started = time.perf_counter()
        try:
            for attempt in range(1, self._max_attempts + 1):
                with self._lock:
                    job = self._jobs[execution_id]
                    if job.cancel_event.is_set():
                        job.state = ExecutionState.CANCELLED
                        job.progress = 1.0
                        job.message = "Execution cancelled"
                        job.error_code = "cancelled"
                        return
                    job.state = ExecutionState.RUNNING
                    job.progress = 0.1
                    job.message = "Running in isolated container"
                    job.attempt = attempt
                try:
                    result = self.executor.execute(source, execution_id)
                    state = (
                        ExecutionState.CANCELLED
                        if result.cancelled
                        else ExecutionState.TIMED_OUT
                        if result.timed_out
                        else ExecutionState.FAILED
                        if result.disabled_reason or result.exit_code not in {None, 0}
                        else ExecutionState.COMPLETED
                    )
                    with self._lock:
                        job = self._jobs[execution_id]
                        job.state = state
                        job.result = result
                        job.progress = 1.0
                        job.message = self._result_message(state)
                        if state in {ExecutionState.FAILED, ExecutionState.TIMED_OUT}:
                            job.error_code = (
                                "execution_timeout"
                                if state is ExecutionState.TIMED_OUT
                                else "execution_failed"
                            )
                            job.error = self._result_error(result)
                    return
                except Exception as error:
                    if attempt >= self._max_attempts:
                        with self._lock:
                            job = self._jobs[execution_id]
                            job.state = ExecutionState.FAILED
                            job.progress = 1.0
                            job.error_code = "executor_unavailable"
                            job.error = str(error).strip() or type(error).__name__
                            job.message = job.error
                        return
                    with self._lock:
                        job = self._jobs[execution_id]
                        job.state = ExecutionState.RETRYING
                        job.message = (
                            f"Execution infrastructure failed; retrying "
                            f"({attempt + 1}/{self._max_attempts})"
                        )
                    if job.cancel_event.wait(min(0.25 * attempt, 1.0)):
                        with self._lock:
                            job = self._jobs[execution_id]
                            job.state = ExecutionState.CANCELLED
                            job.progress = 1.0
                            job.message = "Cancelled before retry"
                            job.error_code = "cancelled"
                        return
        finally:
            self._capacity.release()
            if self._monitor is not None:
                with self._lock:
                    job = self._jobs[execution_id]
                    state = job.state
                    error_code = job.error_code
                    attempt = job.attempt
                self._monitor.record(
                    OperationMetric(
                        "execution.background",
                        "success" if state is ExecutionState.COMPLETED else "failure",
                        (time.perf_counter() - started) * 1000,
                        error_code=error_code,
                        metadata={"state": state.value, "attempt": attempt},
                    )
                )

    @staticmethod
    def _result_message(state: ExecutionState) -> str:
        return {
            ExecutionState.COMPLETED: "Execution completed",
            ExecutionState.CANCELLED: "Execution cancelled",
            ExecutionState.TIMED_OUT: "Execution timed out",
            ExecutionState.FAILED: "Execution failed",
        }.get(state, state.value)

    @staticmethod
    def _result_error(result: ExecutionResult) -> str:
        if result.disabled_reason:
            return result.disabled_reason
        if result.timed_out:
            return result.stderr.strip() or "Execution exceeded its configured deadline"
        if result.diagnostic:
            return f"{result.diagnostic.exception_type}: {result.diagnostic.message}"
        return result.stderr.strip() or f"Execution exited with code {result.exit_code}"
