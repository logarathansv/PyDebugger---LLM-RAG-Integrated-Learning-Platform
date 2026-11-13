from __future__ import annotations

from pathlib import Path
from threading import Event
import time

from pydebugger.execution import (
    DockerCodeExecutor,
    DockerExecutionPolicy,
    ExecutionService,
)
from pydebugger.models import ExecutionResult, ExecutionState


def policy() -> DockerExecutionPolicy:
    return DockerExecutionPolicy(
        docker_binary="docker",
        image="python:3.13-slim@sha256:abc",
        runtime=None,
        timeout_seconds=2,
        output_limit=20_000,
        source_limit=100_000,
        memory_mb=128,
        cpus=0.5,
        pids_limit=32,
        tmpfs_mb=16,
    )


def test_docker_command_applies_isolation_and_resource_limits(tmp_path: Path):
    script = tmp_path / "submission.py"
    script.write_text("print('hello')", encoding="utf-8")

    command = DockerCodeExecutor(policy()).build_command(script, "pydebugger-test")
    rendered = " ".join(command)

    assert "--network none" in rendered
    assert "--read-only" in command
    assert "--cap-drop ALL" in rendered
    assert "no-new-privileges=true" in command
    assert "--user 65534:65534" in rendered
    assert "--pids-limit 32" in rendered
    assert "--memory 128m" in rendered
    assert "--memory-swap 128m" in rendered
    assert "--cpus 0.5" in rendered
    assert "readonly" in rendered
    assert "--pull never" in rendered
    assert command[-5:] == [
        "python:3.13-slim@sha256:abc",
        "python",
        "-I",
        "-B",
        "/workspace/submission.py",
    ]


class BlockingExecutor:
    def __init__(self) -> None:
        self.started = Event()
        self.cancelled = Event()

    def validate_runtime(self) -> None:
        return None

    def execute(self, source: str, execution_id: str) -> ExecutionResult:
        self.started.set()
        self.cancelled.wait(timeout=2)
        return ExecutionResult(execution_id=execution_id, cancelled=True)

    def cancel(self, execution_id: str) -> bool:
        self.cancelled.set()
        return True


def test_execution_service_cancels_running_job():
    executor = BlockingExecutor()
    service = ExecutionService(executor)
    job = service.submit("while True: pass")
    assert executor.started.wait(timeout=1)

    assert service.cancel(job.id)
    deadline = time.monotonic() + 2
    while service.get(job.id).state is not ExecutionState.CANCELLED:
        assert time.monotonic() < deadline
        time.sleep(0.01)

    assert service.get(job.id).result.cancelled
