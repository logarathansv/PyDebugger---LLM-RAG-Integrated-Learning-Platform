"""Structured Python diagnostics without changing submitted source line numbers."""

from __future__ import annotations

import json
from typing import Any

from .models import PythonDiagnostic, TracebackFrame

DIAGNOSTIC_PREFIX = "__PYDEBUGGER_DIAGNOSTIC_V1__"

# This bootstrap is passed to ``python -c``. The student's source is compiled with
# its real container filename, so its first line remains line 1. Only frames from
# that filename are returned; the bootstrap itself never appears in learner traces.
PYTHON_RUNNER = r"""
import json
import linecache
import os
import sys
import traceback

workspace = os.path.realpath("/workspace")
entrypoint = os.environ.get("PYDEBUGGER_ENTRYPOINT", "submission.py")
filename = os.path.realpath(os.path.join(workspace, entrypoint))
if os.path.commonpath((workspace, filename)) != workspace:
    raise SystemExit("Invalid entry point")
sys.path.insert(0, workspace)
with open(filename, "r", encoding="utf-8-sig") as source_file:
    source = source_file.read()
linecache.cache[filename] = (
    len(source.encode("utf-8")),
    None,
    source.splitlines(keepends=True),
    filename,
)

try:
    code = compile(source, filename, "exec")
    sys.argv = [filename]
    namespace = {
        "__name__": "__main__",
        "__file__": filename,
        "__builtins__": __builtins__,
        "__cached__": None,
        "__package__": None,
        "__spec__": None,
    }
    exec(code, namespace, namespace)
except SystemExit:
    raise
except BaseException as error:
    frames = []
    for frame in traceback.extract_tb(error.__traceback__):
        frame_path = os.path.realpath(frame.filename)
        if (
            os.path.isabs(frame.filename)
            and os.path.commonpath((workspace, frame_path)) == workspace
        ):
            frames.append(
                {
                    "file_name": os.path.relpath(frame_path, workspace).replace(os.sep, "/"),
                    "line_number": frame.lineno,
                    "function_name": frame.name,
                    "source_line": frame.line,
                }
            )

    if isinstance(error, SyntaxError):
        error_path = os.path.realpath(error.filename or filename)
        file_name = (
            os.path.relpath(error_path, workspace).replace(os.sep, "/")
            if os.path.commonpath((workspace, error_path)) == workspace
            else entrypoint
        )
        message = error.msg
        line_number = error.lineno
        column_number = error.offset
        end_line_number = error.end_lineno
        end_column_number = error.end_offset
        source_line = error.text.rstrip("\r\n") if error.text else None
    else:
        file_name = frames[-1]["file_name"] if frames else entrypoint
        try:
            message = str(error)
        except BaseException:
            message = "<exception message could not be rendered>"
        line_number = frames[-1]["line_number"] if frames else None
        column_number = None
        end_line_number = None
        end_column_number = None
        source_line = frames[-1]["source_line"] if frames else None

    payload = {
        "exception_type": type(error).__name__,
        "message": message,
        "file_name": file_name,
        "line_number": line_number,
        "column_number": column_number,
        "end_line_number": end_line_number,
        "end_column_number": end_column_number,
        "source_line": source_line,
        "frames": frames,
    }
    print(
        "__PYDEBUGGER_DIAGNOSTIC_V1__" + json.dumps(payload, ensure_ascii=False),
        file=sys.stderr,
        flush=True,
    )
    raise SystemExit(1)
""".strip()


def extract_diagnostic(stderr_tail: str) -> PythonDiagnostic | None:
    """Return the last valid runner diagnostic in a bounded stderr tail."""

    for line in reversed(stderr_tail.splitlines()):
        if not line.startswith(DIAGNOSTIC_PREFIX):
            continue
        try:
            payload = json.loads(line.removeprefix(DIAGNOSTIC_PREFIX))
            return _diagnostic_from_payload(payload)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return None


def remove_diagnostic_records(stderr: str) -> str:
    """Hide the runner's transport record while preserving user stderr."""

    lines = [
        line for line in stderr.splitlines() if not line.startswith(DIAGNOSTIC_PREFIX)
    ]
    return "\n".join(lines).rstrip()


def format_traceback(diagnostic: PythonDiagnostic) -> str:
    """Format only submitted-source frames in familiar CPython traceback form."""

    lines: list[str] = []
    if diagnostic.frames:
        lines.append("Traceback (most recent call last):")
        for frame in diagnostic.frames:
            lines.append(
                f'  File "{frame.file_name}", line {frame.line_number}, '
                f"in {frame.function_name}"
            )
            if frame.source_line:
                lines.append(f"    {frame.source_line.strip()}")
    elif diagnostic.line_number is not None:
        lines.append(f'  File "{diagnostic.file_name}", line {diagnostic.line_number}')
        if diagnostic.source_line:
            lines.append(f"    {diagnostic.source_line}")
            if diagnostic.column_number:
                source_prefix = diagnostic.source_line[: diagnostic.column_number - 1]
                width = 1
                if (
                    diagnostic.end_line_number == diagnostic.line_number
                    and diagnostic.end_column_number
                    and diagnostic.end_column_number > diagnostic.column_number
                ):
                    width = diagnostic.end_column_number - diagnostic.column_number
                lines.append(
                    "    " + " " * len(source_prefix.expandtabs()) + "^" * width
                )

    summary = diagnostic.exception_type
    if diagnostic.message:
        summary += f": {diagnostic.message}"
    lines.append(summary)
    return "\n".join(lines)


def _diagnostic_from_payload(payload: Any) -> PythonDiagnostic:
    if not isinstance(payload, dict):
        raise TypeError("diagnostic payload must be an object")
    frames = tuple(_frame_from_payload(item) for item in payload.get("frames", []))
    return PythonDiagnostic(
        exception_type=_required_string(payload, "exception_type"),
        message=_optional_string(payload.get("message")) or "",
        file_name=_optional_string(payload.get("file_name")) or "submission.py",
        line_number=_optional_positive_int(payload.get("line_number")),
        column_number=_optional_positive_int(payload.get("column_number")),
        end_line_number=_optional_positive_int(payload.get("end_line_number")),
        end_column_number=_optional_positive_int(payload.get("end_column_number")),
        source_line=_optional_string(payload.get("source_line")),
        frames=frames,
    )


def _frame_from_payload(payload: Any) -> TracebackFrame:
    if not isinstance(payload, dict):
        raise TypeError("traceback frame must be an object")
    line_number = _optional_positive_int(payload.get("line_number"))
    if line_number is None:
        raise ValueError("frame line number is required")
    return TracebackFrame(
        file_name=_required_string(payload, "file_name"),
        line_number=line_number,
        function_name=_required_string(payload, "function_name"),
        source_line=_optional_string(payload.get("source_line")),
    )


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str) or not value:
        raise TypeError(f"{key} must be a non-empty string")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("value must be a string")
    return value


def _optional_positive_int(value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise TypeError("value must be a positive integer")
    return value
