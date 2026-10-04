"""
UserPromptSubmit hook: classifies the user's prompt across five areas and emits
an llm_request_area span. Always fires. All five dimensions sum to 1.0.

Classification is sentence-level: each sentence gets one winning area, tallied
across all sentences and normalized. This avoids fragment collision where common
words like "right" or "search" create false signal outside their intended context.
"""

import json
import re
import sys
import time
from pathlib import Path



# Signals per area. Prefer multi-word phrases — they carry enough context to
# classify a sentence correctly even when individual words are ambiguous.
SIGNALS: dict[str, list[str]] = {
    # The project's own domain work — whatever the project exists to do —
    # as opposed to working on the AI tooling (agentic), asking for outside
    # information (research), or rating Claude (feedback).
    "project": [
        # Status / progress
        "do i have", "where are we", "what's the status", "what is the status",
        "status of", "what's left", "what's next", "next steps", "progress on",
        "how many", "how far", "any open", "still open", "outstanding",
        # Navigating existing work
        "list the", "list all", "list my", "show me the", "show me my",
        "open the", "where is the", "which ones", "the latest",
        # Doing the work
        "continue the", "resume the", "finish the", "pick up where",
        "keep going", "run the", "run another", "start a new", "start the",
        "next batch", "another batch", "process the", "work on",
        "mark as", "mark it", "move it to", "close out",
        # Changing project content
        "update the", "edit the", "fix the", "add to the", "add a new",
        "remove from", "delete the", "create the", "write the", "rename the",
        "in the file", "in the folder", "the data", "the results",
    ],
    "agentic": [
        # Config and tooling
        "hook", "hooks", "skill", "skills", "command", "commands",
        "settings.json", ".claude/settings", "~/.claude",
        "global hook", "project hook", "hook fires", "hook runs",
        # Code and scripts
        "llm_request", "llm_feedback", "llm_request_area",
        "observability", "telemetry", "otel", "opentelemetry",
        "tempo", "grafana", "span", "emit span", "emit a span",
        "pyproject", "venv", "uv run", "uv sync",
        # AI system concepts
        "memory", "agent", "agents", "slash command",
        "configure", "configuration", "setup the", "install the",
        "build it", "build a", "build the", "let's build",
        "add a hook", "add the hook", "wire", "wiring",
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


def split_sentences(text: str) -> list[str]:
    """Split text into sentences on . ? ! — keep short fragments as their own sentence."""
    parts = re.split(r'(?<=[.?!])\s+', text.strip())
    return [p.strip() for p in parts if p.strip()]


def classify_sentence(sentence: str) -> str:
    """Return the best-matching area for a sentence, or 'other' if none match."""
    t = sentence.lower().strip()
    best_area = "other"
    best_score = 0

    for area, signals in SIGNALS.items():
        score = sum(1 for sig in signals if sig in t)
        if score > best_score:
            best_score = score
            best_area = area

    return best_area


def score_prompt(text: str) -> dict[str, float]:
    sentences = split_sentences(text)
    if not sentences:
        return {area: 0.0 for area in SIGNALS} | {"other": 1.0}

    tally: dict[str, int] = {area: 0 for area in SIGNALS}
    tally["other"] = 0

    for sentence in sentences:
        winner = classify_sentence(sentence)
        tally[winner] += 1

    total = len(sentences)
    return {area: round(tally[area] / total, 4) for area in tally}


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
    tracer = trace.get_tracer("llm_area_hook")
    with tracer.start_as_current_span("llm_request_area", attributes=attrs):
        time.sleep(0.05)
    tp.shutdown()


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

    attrs = {
        "session_id": session_id,
        "model": model,
        "project": scores["project"],
        "agentic": scores["agentic"],
        "feedback": scores["feedback"],
        "research": scores["research"],
        "other": scores["other"],
    }

    try:
        emit_span(attrs)
    except Exception:
        pass
