"""
CLI helper: emits llm_request_feedback metrics with manual scores.
Called by the /llm-feedback skill.
"""

import argparse
import json
import sys
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




def emit_metrics(attrs: dict) -> None:
    """Record each feedback score present as a Prometheus histogram (_sum and _count per score).

    Delta temporality: each run is a new process; the collector's
    deltatocumulative processor keeps the running totals.
    """
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.metrics import Counter, Histogram, MeterProvider
    from opentelemetry.sdk.metrics.export import AggregationTemporality, PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource

    # Fixed instance id so every hook run adds to the same series.
    resource = Resource.create({"service.name": "claude_code", "service.instance.id": "llm_feedback_emit"})
    exporter = OTLPMetricExporter(
        endpoint="http://localhost:4318/v1/metrics",
        preferred_temporality={Counter: AggregationTemporality.DELTA, Histogram: AggregationTemporality.DELTA},
    )
    mp = MeterProvider(resource=resource, metric_readers=[PeriodicExportingMetricReader(exporter)])
    meter = mp.get_meter("llm_feedback_emit")
    labels = {"model": attrs["model"], "signal_type": attrs["signal_type"]}
    scores = meter.create_histogram("llm_request_feedback_score", description="Feedback score (0-1), by score")
    for score in ("success", "correctness", "relevance", "factscore", "quality", "effectiveness"):
        if attrs.get(score) is not None:
            scores.record(attrs[score], {**labels, "score": score})
    mp.shutdown()


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
        emit_metrics(attrs)
        print(json.dumps({"status": "ok", "effectiveness": args.effectiveness, "quality": args.quality}))
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        sys.exit(1)
