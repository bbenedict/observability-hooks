"""
Record project metrics to Prometheus (via the OTLP collector).

Usage:
  uv run python .claude/lib/emit.py metric <operation> [key=value ...]

Each numeric attribute becomes a histogram named <operation>.<attribute>; string
attributes become labels. In Prometheus each histogram has _sum (total) and
_count (number of runs), e.g. myproject_task_complete_items_sum.

Examples:
  myproject.task.complete   task= items_processed= errors= duration_s=
  myproject.run.summary     run_id= success_rate= total_items=

  uv run python .claude/lib/emit.py metric myproject.task.complete task=import items_processed=42 errors=0
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from observability import setup_telemetry, get_meter, shutdown_telemetry


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


def record_metrics(operation: str, attrs: dict) -> None:
    labels = {k: v for k, v in attrs.items() if isinstance(v, str)}
    meter = get_meter()
    for key, value in attrs.items():
        if isinstance(value, (int, float)):
            meter.create_histogram(f"{operation}.{key}").record(value, labels)


def emit(operation: str, attrs: dict) -> None:
    setup_telemetry()
    record_metrics(operation, attrs)
    shutdown_telemetry()
    print(f"Metric recorded: {operation} {attrs}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    if sys.argv[1] != "metric":
        print(f"Unknown mode: {sys.argv[1]}. Use 'metric'.")
        sys.exit(1)

    emit(sys.argv[2], parse_attrs(sys.argv[3:]))
