"""
UserPromptSubmit hook: classifies the user's prompt across five areas and emits
llm_request_area metrics. Always fires. All five dimensions sum to 1.0.

Classification is sentence-level: each sentence gets one winning area, tallied
across all sentences and normalized.

Design: "project" (the project's own domain work) is the DEFAULT — most prompts
in any project are project work, and its vocabulary is unknowable in advance. The
other areas are the identifiable exceptions, each with high-precision signals:
  agentic  — working on Claude Code itself (.claude config, hooks, commands,
             skills, subagents, memory, the telemetry tooling)
  feedback — rating Claude's previous response
  research — asking for outside information
A sentence matching none of those is project work.
"""

import json
import re
import sys
from pathlib import Path


# Signals for the three identifiable areas. "project" has no list — it is the
# default. Prefer specific, unambiguous phrases: bare words like "hook", "skill",
# "command", or "agent" also occur in ordinary software work (React hooks, a CLI
# command parser, an app's skills screen) and would misread project work as
# agentic, so they are deliberately excluded in favor of Claude-Code-specific
# markers like ".claude", "settings.json", "slash command", and "subagent".
SIGNALS: dict[str, list[str]] = {
    "agentic": [
        # Claude Code config locations and files
        ".claude", "claude code", "claude-code", "claude.md",
        "settings.json", "settings.local.json", "~/.claude",
        "agents.md", "claude's config", "claude config",
        # Claude Code artifacts (specific phrasings)
        "slash command", "slash commands", "subagent", "subagents",
        "global hook", "project hook", "hook fires", "hook runs",
        "add a hook", "add the hook", "the hook in", "a stop hook",
        "userpromptsubmit", "posttooluse", "pretooluse", "sessionstart",
        "add a skill", "a claude skill", "the skill file",
        "slash-command", "output style", "status line", "statusline",
        "mcp server", "mcp tool", "mcp config",
        # Auto memory
        "auto memory", "memory file", "memory.md", "claude's memory",
        # The telemetry / observability tooling itself
        "llm_request", "llm_feedback", "llm_governance",
        "observability", "telemetry", "opentelemetry", "otel",
        "prometheus", "grafana", "tempo", "otlp", "collector config",
        "emit metric", "emit a metric", "emit span", "deltatocumulative",
        # The hook toolchain
        "uv run", "uv sync", "pyproject", "the venv",
    ],
    "feedback": [
        # Explicit praise/criticism
        "good job", "great job", "bad job", "nice job", "well done",
        "nicely done", "you nailed", "you failed", "you missed",
        "you were wrong", "you were right", "you were correct",
        "you made an error", "you made up", "you hallucinated",
        "you added things", "you should have known",
        # Response quality judgments
        "that worked", "that didn't work", "that was wrong",
        "that was right", "that was correct", "that was incorrect",
        "that's not what i asked", "not what i meant",
        "you didn't answer", "you missed the point",
        # Feedback actions
        "/llm-feedback", "give you feedback", "provide feedback",
        "rate your", "rating your", "score your",
    ],
    "research": [
        # Information requests
        "any news", "is there news", "what's happening", "what is happening",
        "tell me about", "what do you know about", "explain the",
        "how does", "what does", "what is a", "who is",
        "look up", "look into", "search for information",
        "find out", "find information", "investigate",
        "background on", "overview of", "summarize",
        "what are the", "is there any", "are there any",
        "in the country", "in the market", "industry news",
        "market trends", "latest on", "best practices", "compare the",
        "pros and cons", "difference between",
    ],
}

# project is the default; other is retained for output compatibility (now rare).
AREAS = ["project", "agentic", "feedback", "research", "other"]


def split_sentences(text: str) -> list[str]:
    """Split text into sentences on . ? ! — keep short fragments as their own sentence."""
    parts = re.split(r'(?<=[.?!])\s+', text.strip())
    return [p.strip() for p in parts if p.strip()]


def classify_sentence(sentence: str) -> str:
    """Priority gate. Almost everything is project or agentic; feedback and
    research override only when clearly signalled (they generally are). 'other'
    is reserved for a sentence with nothing to decide on.

      1. clearly feedback?  -> feedback
      2. clearly research?  -> research
      3. a Claude Code marker present?  -> agentic
      4. otherwise           -> project   (the default half of the main pair)
    """
    t = sentence.lower().strip()
    if not t:
        return "other"
    if any(sig in t for sig in SIGNALS["feedback"]):
        return "feedback"
    if any(sig in t for sig in SIGNALS["research"]):
        return "research"
    if any(sig in t for sig in SIGNALS["agentic"]):
        return "agentic"
    return "project"


def score_prompt(text: str) -> dict[str, float]:
    sentences = split_sentences(text)
    if not sentences:
        # Nothing to decide on.
        return {area: (1.0 if area == "other" else 0.0) for area in AREAS}

    tally: dict[str, int] = {area: 0 for area in AREAS}
    for sentence in sentences:
        tally[classify_sentence(sentence)] += 1

    total = len(sentences)
    return {area: round(tally[area] / total, 4) for area in AREAS}


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


def emit_metrics(scores: dict[str, float], model: str) -> None:
    """Add each area's score to a counter labeled by area, for Prometheus.

    Delta temporality: each hook run is a new process, so cumulative counters
    would restart at 0 every time. The collector accumulates the deltas.
    """
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.metrics import Counter, MeterProvider
    from opentelemetry.sdk.metrics.export import AggregationTemporality, PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource

    # Fixed instance id so every hook run adds to the same series per area.
    resource = Resource.create({"service.name": "claude_code", "service.instance.id": "llm_area_hook"})
    exporter = OTLPMetricExporter(
        endpoint="http://localhost:4318/v1/metrics",
        preferred_temporality={Counter: AggregationTemporality.DELTA},
    )
    mp = MeterProvider(resource=resource, metric_readers=[PeriodicExportingMetricReader(exporter)])
    counter = mp.get_meter("llm_area_hook").create_counter(
        "llm_request_area_score", description="Prompt area score, summed per area"
    )
    for area, score in scores.items():
        counter.add(score, {"area": area, "model": model})
    mp.shutdown()


if __name__ == "__main__":
    try:
        hook_input = json.loads(sys.stdin.read())
        session_id = hook_input.get("session_id", "")
        prompt = hook_input.get("prompt", "")
    except Exception:
        sys.exit(0)

    if not session_id or not prompt:
        sys.exit(0)

    scores = score_prompt(prompt)
    model = get_last_model(session_id)

    try:
        emit_metrics(scores, model)
    except Exception:
        pass
