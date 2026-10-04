"""
CLI helper: emits llm_request_feedback span with manual scores.
Called by the /llm-feedback skill.
"""

import argparse
import json
import sys
import time
from pathlib import Path



def get_last_model(session_id: str) -> str:
    projects_dir = Path.home() / ".claude" / "projects"
    for p in projects_dir.rglob(f"{session_id}.jsonl"):
        last_model = "unknown"
        with open(p) as f:
            for line in f:
                try:
                    obj = json.loads(line)
                    if obj.get("type") == "assistant":
                        m = obj.get("message", {}).get("model")
                        if m:
                            last_model = m
                except Exception:
                    pass
        return last_model
    return "unknown"


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
    tracer = trace.get_tracer("llm_feedback_emit")
    with tracer.start_as_current_span("llm_request_feedback", attributes=attrs):
        time.sleep(0.05)
    tp.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--success", type=float, required=True)
    parser.add_argument("--correctness", type=float, required=True)
    parser.add_argument("--relevance", type=float, required=True)
    parser.add_argument("--factscore", type=float, required=True)
    parser.add_argument("--quality", type=float, required=True)
    parser.add_argument("--effectiveness", type=float, required=True)
    args = parser.parse_args()

    model = get_last_model(args.session_id)

    attrs = {
        "session_id": args.session_id,
        "model": model,
        "signal_type": "manual",
        "success": args.success,
        "correctness": args.correctness,
        "relevance": args.relevance,
        "factscore": args.factscore,
        "quality": args.quality,
        "effectiveness": args.effectiveness,
    }

    try:
        emit_span(attrs)
        print(json.dumps({"status": "ok", "effectiveness": args.effectiveness, "quality": args.quality}))
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        sys.exit(1)
