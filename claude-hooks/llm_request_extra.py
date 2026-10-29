"""
Stop hook: records token usage and cost for every API call in the session transcript
since the hook last ran, as llm_request_tokens and llm_request_cost_dollars.

A turn that uses tools makes several API calls, so each run records all of them, not
just the last. One response is written across several transcript lines that repeat
the same usage, so calls are counted once per message id. The last processed line is
kept per session in a state file; on a session's first run, only the current turn is
recorded so earlier history isn't backfilled into the present.
"""

import json
import sys
from pathlib import Path



def find_jsonl(session_id: str) -> Path | None:
    projects_dir = Path.home() / ".claude" / "projects"
    for p in projects_dir.rglob(f"{session_id}.jsonl"):
        return p
    return None


def get_new_calls(jsonl_path: Path, last_processed: int) -> tuple[int, list[dict]]:
    """Return (last line index, new API calls) after line last_processed.

    Each call is the message dict of its last transcript line. With no prior state
    (last_processed == -1), only calls after the last real user prompt are returned.
    """
    calls: dict[str, dict] = {}
    last_idx = last_processed
    with open(jsonl_path) as f:
        for i, line in enumerate(f):
            last_idx = i
            if i <= last_processed:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            message = obj.get("message") or {}
            if last_processed == -1 and obj.get("type") == "user" and isinstance(message.get("content"), str):
                calls = {}  # first run: start over at each real user prompt
            if obj.get("type") == "assistant" and "usage" in message:
                calls[message.get("id") or f"line-{i}"] = message
    return last_idx, list(calls.values())


# Dollars per million tokens: (input, output, cache read). Anthropic API list prices,
# checked 2026-10-24. Cache writes are priced from input: 1.25x for the 5-minute TTL,
# 2x for the 1-hour TTL. Longest matching prefix wins, so "claude-opus-5-5" beats
# "claude-opus-5" and dated or suffixed ids still match.
PRICES = {
    "claude-fable-5-1": (10.00, 50.00, 0.25),
    "claude-mythos-5-1": (10.00, 50.00, 0.25),
    "claude-fable-5": (10.00, 50.00, 1.00),
    "claude-mythos-5": (10.00, 50.00, 1.00),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-opus-4": (5.00, 25.00, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
    "claude-sonnet-4": (3.00, 15.00, 0.30),
    "claude-haiku-4": (1.00, 5.00, 0.10),
}
# Fast mode runs at 2x standard prices on the models that offer it.
FAST_MULTIPLIER = 2.0


def model_prices(model: str) -> tuple[float, float, float] | None:
    matches = [prefix for prefix in PRICES if model.startswith(prefix)]
    return PRICES[max(matches, key=len)] if matches else None


def compute_cost_microdollars(usage: dict, model: str) -> int:
    """Cost of one response in microdollars, at the model's own prices.

    Dollars per million tokens equals microdollars per token, so token counts are
    multiplied by the per-MTok prices directly. Unknown models cost 0.
    """
    prices = model_prices(model)
    if prices is None:
        return 0
    price_in, price_out, price_read = prices
    creation = usage.get("cache_creation") or {}
    write_1h = creation.get("ephemeral_1h_input_tokens", 0)
    # Without the TTL breakdown, treat every cache write as 5-minute.
    write_5m = creation.get("ephemeral_5m_input_tokens", usage.get("cache_creation_input_tokens", 0) - write_1h)
    cost = (
        usage.get("input_tokens", 0) * price_in
        + usage.get("output_tokens", 0) * price_out
        + usage.get("cache_read_input_tokens", 0) * price_read
        + write_5m * price_in * 1.25
        + write_1h * price_in * 2.0
    )
    if usage.get("speed") == "fast":
        cost *= FAST_MULTIPLIER
    return int(cost)




def call_attrs(message: dict) -> dict:
    usage = message["usage"]
    model = message.get("model", "unknown")
    microdollars = compute_cost_microdollars(usage, model)
    attrs: dict = {
        "model": model,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
        "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0),
        "cost_dollars": round(microdollars / 1_000_000, 6),
    }
    thinking_tokens = (usage.get("output_tokens_details") or {}).get("thinking_tokens", 0)
    if thinking_tokens:
        attrs["thinking_tokens"] = thinking_tokens
    return attrs


def emit_metrics(calls: list[dict]) -> None:
    """Record each call's token counts and cost as Prometheus histograms (_sum and _count per series).

    Delta temporality: each hook run is a new process; the collector's
    deltatocumulative processor keeps the running totals.
    """
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.metrics import Counter, Histogram, MeterProvider
    from opentelemetry.sdk.metrics.export import AggregationTemporality, PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource

    # Fixed instance id so every hook run adds to the same series.
    resource = Resource.create({"service.name": "claude_code", "service.instance.id": "llm_extra_hook"})
    exporter = OTLPMetricExporter(
        endpoint="http://localhost:4318/v1/metrics",
        preferred_temporality={Counter: AggregationTemporality.DELTA, Histogram: AggregationTemporality.DELTA},
    )
    mp = MeterProvider(resource=resource, metric_readers=[PeriodicExportingMetricReader(exporter)])
    meter = mp.get_meter("llm_extra_hook")
    tokens = meter.create_histogram("llm_request_tokens", description="Tokens per API call, by type")
    cost = meter.create_histogram("llm_request_cost_dollars", description="Cost per API call in dollars")
    for attrs in calls:
        labels = {"model": attrs["model"]}
        for token_type in ("input", "output", "cache_read", "cache_creation", "thinking"):
            key = f"{token_type}_tokens"
            if key in attrs:
                tokens.record(attrs[key], {**labels, "type": token_type})
        cost.record(attrs["cost_dollars"], labels)
    mp.shutdown()


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

    last_idx, calls = get_new_calls(jsonl_path, last_processed)
    if last_idx <= last_processed:
        sys.exit(0)

    try:
        state_file.write_text(str(last_idx))
    except Exception:
        pass

    if calls:
        try:
            emit_metrics([call_attrs(m) for m in calls])
        except Exception:
            pass
