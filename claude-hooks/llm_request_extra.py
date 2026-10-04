"""
Stop hook: reads last JSONL token usage for the session, emits llm_request_extra span to Tempo.
Tracks processed entries per session to avoid double-emit.
"""

import json
import sys
import time
from pathlib import Path



def find_jsonl(session_id: str) -> Path | None:
    projects_dir = Path.home() / ".claude" / "projects"
    for p in projects_dir.rglob(f"{session_id}.jsonl"):
        return p
    return None


def get_last_usage(jsonl_path: Path) -> tuple[int, dict] | None:
    last = None
    with open(jsonl_path) as f:
        for i, line in enumerate(f):
            try:
                obj = json.loads(line)
                if obj.get("type") == "assistant" and "usage" in obj.get("message", {}):
                    last = (i, obj)
            except Exception:
                pass
    return last


def compute_cost_microdollars(usage: dict) -> int:
    # Pricing per million tokens (Sonnet family):
    # input $3, output $15, cache_read $0.30, cache_creation $3.75
    # microdollars = dollars * 1_000_000, so per-token: input=3, output=15, etc.
    return int(
        usage.get("input_tokens", 0) * 3
        + usage.get("output_tokens", 0) * 15
        + usage.get("cache_read_input_tokens", 0) * 0.3
        + usage.get("cache_creation_input_tokens", 0) * 3.75
    )


def emit_span(attrs: dict) -> None:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": "claude_code"})
    tp = TracerProvider(resource=resource)
    tp.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint="http://localhost:4318/v1/traces"))
    )
    trace.set_tracer_provider(tp)
    tracer = trace.get_tracer("llm_perf_hook")
    with tracer.start_as_current_span("llm_request_extra", attributes=attrs):
        time.sleep(0.05)
    tp.shutdown()


if __name__ == "__main__":
    try:
        hook_input = json.loads(sys.stdin.read())
        session_id = hook_input.get("session_id", "")
    except Exception:
        sys.exit(0)

    if not session_id:
        sys.exit(0)

    jsonl_path = find_jsonl(session_id)
    if not jsonl_path:
        sys.exit(0)

    state_file = Path.home() / ".claude" / "lib" / f".llm_perf_state_{session_id}"
    last_processed = -1
    if state_file.exists():
        try:
            last_processed = int(state_file.read_text().strip())
        except Exception:
            pass

    result = get_last_usage(jsonl_path)
    if not result:
        sys.exit(0)

    idx, entry = result
    if idx <= last_processed:
        sys.exit(0)

    usage = entry["message"]["usage"]
    model = entry["message"].get("model", "unknown")
    microdollars = compute_cost_microdollars(usage)

    attrs: dict = {
        "model": model,
        "session_id": session_id,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
        "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0),
        "cost_microdollars": microdollars,
        "cost_dollars": round(microdollars / 1_000_000, 6),
    }

    thinking_tokens = usage.get("output_tokens_details", {}).get("thinking_tokens", 0)
    if thinking_tokens:
        attrs["thinking_tokens"] = thinking_tokens

    try:
        emit_span(attrs)
        state_file.write_text(str(idx))
    except Exception:
        pass
