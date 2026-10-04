"""
Emit spans to Tempo for myproject observability.

Usage:
  uv run python .claude/lib/emit.py span <operation> [key=value ...]

Span operations:
  Any name you choose, in the form <project>.<operation>.
  Attributes are key=value pairs. Numbers are sent as int, or as float if
  the value contains a "."; everything else is sent as a string.

Example:
  uv run python .claude/lib/emit.py span myproject.task.complete task=import items_processed=42 errors=0
"""

import sys
import time
import os

sys.path.insert(0, os.path.dirname(__file__))
from observability import setup_telemetry, get_tracer, shutdown_telemetry


def parse_attrs(args: list[str]) -> dict:
    attrs = {}
    for a in args:
        if "=" in a:
            k, v = a.split("=", 1)
            try:
                attrs[k] = int(v) if "." not in v else float(v)
            except ValueError:
                attrs[k] = v
    return attrs


def emit_span(operation: str, attrs: dict) -> None:
    setup_telemetry()
    with get_tracer().start_as_current_span(operation, attributes=attrs):
        time.sleep(0.05)
    shutdown_telemetry()
    print(f"Span emitted: {operation} {attrs}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    if sys.argv[1] != "span":
        print(f"Unknown mode: {sys.argv[1]}. Use 'span'.")
        sys.exit(1)

    emit_span(sys.argv[2], parse_attrs(sys.argv[3:]))
