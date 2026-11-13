from __future__ import annotations

import os
import subprocess
import time
import uuid

import pytest

from pydebugger.execution import (
    DockerCodeExecutor,
    DockerExecutionPolicy,
    ExecutionService,
)
from pydebugger.models import ExecutionState


IMAGE = (
    "python:3.13-slim-bookworm@sha256:"
    "a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72"
)


pytestmark = pytest.mark.skipif(
    os.getenv("PYDEBUGGER_RUN_DOCKER_TESTS") != "1",
    reason="set PYDEBUGGER_RUN_DOCKER_TESTS=1 to run Docker integration tests",
)


def executor(*, timeout: float = 2, output_limit: int = 4_096) -> DockerCodeExecutor:
    return DockerCodeExecutor(
        DockerExecutionPolicy(
            docker_binary="docker",
            image=IMAGE,
            runtime=None,
            timeout_seconds=timeout,
            output_limit=output_limit,
            source_limit=100_000,
            memory_mb=64,
            cpus=0.5,
            pids_limit=16,
            tmpfs_mb=8,
        )
    )


def assert_no_execution_containers() -> None:
    result = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=pydebugger-", "--quiet"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert not result.stdout.strip()


def test_docker_executor_isolates_secrets_and_filesystem_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("PYDEBUGGER_TEST_SECRET", "must-not-reach-container")
    runner = executor()
    runner.validate_runtime()

    result = runner.execute(
        "import os\n"
        "print(os.getenv('PYDEBUGGER_TEST_SECRET'))\n"
        "try:\n"
        "    open('/owned', 'w').write('no')\n"
        "except OSError as error:\n"
        "    print(type(error).__name__)\n"
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 53), timeout=0.2)\n"
        "except OSError:\n"
        "    print('network-blocked')\n",
        uuid.uuid4().hex,
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["None", "OSError", "network-blocked"]
    assert_no_execution_containers()


def test_docker_executor_enforces_timeout_and_output_limit():
    timed_out = executor(timeout=0.5).execute("while True: pass", uuid.uuid4().hex)
    assert timed_out.timed_out

    output_limited = executor(output_limit=1_024).execute(
        "while True: print('x' * 200)", uuid.uuid4().hex
    )
    assert output_limited.resource_limit_exceeded
    assert output_limited.truncated
    assert len(output_limited.stdout.encode()) <= 1_024
    assert_no_execution_containers()


def test_docker_executor_reports_syntax_error_at_original_line():
    result = executor().execute(
        "value = 1\nif True print('broken'):\n    pass\n", uuid.uuid4().hex
    )

    assert result.exit_code == 1
    assert result.diagnostic is not None
    assert result.diagnostic.exception_type == "SyntaxError"
    assert result.diagnostic.line_number == 2
    assert result.diagnostic.column_number == 9
    assert 'File "submission.py", line 2' in result.traceback
    assert "SyntaxError: invalid syntax" in result.traceback
    assert "<string>" not in result.traceback
    assert_no_execution_containers()


def test_docker_executor_reports_complete_runtime_trace():
    source = (
        "def divide(value):\n"
        "    return 10 / value\n"
        "\n"
        "def calculate():\n"
        "    return divide(0)\n"
        "\n"
        "print(calculate())\n"
    )
    result = executor().execute(source, uuid.uuid4().hex)

    assert result.exit_code == 1
    assert result.diagnostic is not None
    assert result.diagnostic.exception_type == "ZeroDivisionError"
    assert result.diagnostic.line_number == 2
    assert [frame.line_number for frame in result.diagnostic.frames] == [7, 5, 2]
    assert [frame.function_name for frame in result.diagnostic.frames] == [
        "<module>",
        "calculate",
        "divide",
    ]
    assert "ZeroDivisionError: division by zero" in result.traceback
    assert "<string>" not in result.traceback
    assert_no_execution_containers()


def test_docker_execution_can_be_cancelled():
    service = ExecutionService(executor(timeout=10))
    job = service.submit("while True: pass")
    deadline = time.monotonic() + 5
    while service.get(job.id).state is ExecutionState.QUEUED:
        assert time.monotonic() < deadline
        time.sleep(0.01)

    assert service.cancel(job.id)
    while service.get(job.id).state is not ExecutionState.CANCELLED:
        assert time.monotonic() < deadline
        time.sleep(0.02)

    assert service.get(job.id).result.cancelled
    assert_no_execution_containers()
