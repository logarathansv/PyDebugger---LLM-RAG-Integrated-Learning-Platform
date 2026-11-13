from __future__ import annotations

import json

from pydebugger.diagnostics import (
    DIAGNOSTIC_PREFIX,
    extract_diagnostic,
    format_traceback,
    remove_diagnostic_records,
)


def record(**overrides) -> str:
    payload = {
        "exception_type": "ZeroDivisionError",
        "message": "division by zero",
        "line_number": 4,
        "column_number": None,
        "end_line_number": None,
        "end_column_number": None,
        "source_line": "return 10 / value",
        "frames": [
            {
                "file_name": "submission.py",
                "line_number": 6,
                "function_name": "<module>",
                "source_line": "print(divide(0))",
            },
            {
                "file_name": "submission.py",
                "line_number": 4,
                "function_name": "divide",
                "source_line": "return 10 / value",
            },
        ],
    }
    payload.update(overrides)
    return DIAGNOSTIC_PREFIX + json.dumps(payload)


def test_runtime_diagnostic_formats_only_submission_frames():
    diagnostic = extract_diagnostic("student warning\n" + record())

    assert diagnostic is not None
    assert diagnostic.line_number == 4
    assert [frame.line_number for frame in diagnostic.frames] == [6, 4]
    assert format_traceback(diagnostic) == (
        "Traceback (most recent call last):\n"
        '  File "submission.py", line 6, in <module>\n'
        "    print(divide(0))\n"
        '  File "submission.py", line 4, in divide\n'
        "    return 10 / value\n"
        "ZeroDivisionError: division by zero"
    )


def test_syntax_diagnostic_preserves_line_and_column():
    diagnostic = extract_diagnostic(
        record(
            exception_type="SyntaxError",
            message="invalid syntax",
            line_number=2,
            column_number=9,
            end_line_number=2,
            end_column_number=10,
            source_line="if True print('broken'):",
            frames=[],
        )
    )

    assert diagnostic is not None
    assert format_traceback(diagnostic) == (
        '  File "submission.py", line 2\n'
        "    if True print('broken'):\n"
        "            ^\n"
        "SyntaxError: invalid syntax"
    )


def test_transport_record_is_removed_without_losing_user_stderr():
    stderr = "student warning\n" + record() + "\n"

    assert remove_diagnostic_records(stderr) == "student warning"
