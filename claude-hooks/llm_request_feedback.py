"""
UserPromptSubmit hook: analyzes the user's message for feedback signals on the previous response.
Only emits llm_request_feedback span if BOTH gates pass:
  1. Message is relevant to (responding to) the previous Claude turn
  2. Message contains at least one clear feedback signal
If both pass, attempts to populate the full span shape.
"""

import json
import sys
import time
from pathlib import Path



# Gate 1: signals that the message references the previous Claude response.
# Also passes for short messages (<=6 words) that contain a clear feedback signal —
# a brief "good job" or "wrong" almost always refers to what Claude just said.
RESPONSE_REFERENCE = [
    "that", "this", "it", "you", "your", "the response", "the answer",
    "what you", "you said", "you did", "you just", "that was", "that is",
    "that's", "this is", "this was", "your response", "your answer",
    "the result", "the output", "what you gave", "what you sent",
]

# Success signals
SUCCESS_POSITIVE = [
    "perfect", "exactly", "yes exactly", "that's right", "that's correct",
    "great", "well done", "that worked", "it worked", "done", "looks good",
    "good job", "nailed it", "spot on", "correct", "that's what i wanted",
    "that's what i needed", "accomplished", "complete", "finished",
    "excellent", "outstanding", "brilliant", "awesome", "nice work",
    "great work", "you got it", "that's it", "that's exactly", "love it",
    "worked perfectly", "worked great", "thank you that", "that's perfect",
    "you nailed", "well executed", "nicely done", "that's exactly what",
    "exactly what i needed", "exactly what i wanted", "task complete",
    "that's correct", "right on", "you're right", "you were right",
]
SUCCESS_NEGATIVE = [
    "no,", "no.", "that's wrong", "that is wrong", "incorrect", "not right",
    "wrong", "undo", "revert", "start over", "that's not", "that is not",
    "don't do that", "stop doing", "you broke", "failed", "didn't work",
    "did not work", "bad job", "poor job", "did a bad", "did poorly",
    "not what i wanted", "not what i needed", "missed it", "try again",
    "do it again", "redo", "no no", "absolutely not", "not even close",
    "completely wrong", "totally wrong", "that's terrible", "awful",
    "you failed", "didn't accomplish", "incomplete", "you missed the point",
    "not acceptable", "that's not right", "that is not right",
]

# Correctness signals
CORRECTNESS_POSITIVE = [
    "correct", "accurate", "right", "exactly right", "that's right",
    "that is correct", "spot on", "no errors", "all correct", "you're right",
    "you were right", "that's accurate", "precisely right", "perfectly correct",
    "error free", "without errors", "no mistakes",
]
CORRECTNESS_NEGATIVE = [
    "incorrect", "wrong", "not right", "that's wrong", "error",
    "mistake", "inaccurate", "made up", "should have known",
    "you should know", "you should have known", "full of errors",
    "made an error", "that's an error", "errors in", "mistakes in",
    "you're wrong", "you were wrong", "that's incorrect", "that is incorrect",
    "not accurate", "completely wrong", "totally wrong", "that's false",
    "that is false", "false information", "wrong information",
]

# Relevance signals
RELEVANCE_POSITIVE = [
    "exactly what i asked", "answered my question", "that answered",
    "right on point", "on topic", "relevant", "that's what i asked",
    "you understood", "understood the question", "addressed the question",
    "answered it", "on point", "directly answered", "right answer to",
    "that's exactly what i asked", "understood what i meant",
    "got what i was asking", "addressed what i asked",
]
RELEVANCE_NEGATIVE = [
    "that's not what i asked", "that is not what i asked", "not what i meant",
    "you didn't answer", "you did not answer", "you missed", "you forgot",
    "again,", "as i said", "as i mentioned", "i already said", "i told you",
    "off topic", "not the question", "answer the question", "not relevant",
    "you misunderstood", "didn't understand", "misunderstood the question",
    "went off on", "tangent", "beside the point", "you ignored",
    "didn't address", "did not address", "not what i was asking",
    "missed the point", "that's not what i meant", "not answering",
    "not an answer", "asked for something else", "asked you to",
]

# FActScore signals
FACTSCORE_POSITIVE = [
    "that's accurate", "that is accurate", "confirmed", "verified",
    "factually correct", "all correct", "well researched", "fact checked",
    "facts are right", "facts are correct", "accurate information",
    "all facts", "no hallucination", "all true", "that's true",
    "that is true", "factually sound",
]
FACTSCORE_NEGATIVE = [
    "you made that up", "made that up", "made up", "that doesn't exist",
    "that does not exist", "that's not real", "that is not real",
    "fabricated", "hallucinated", "not a real", "doesn't exist",
    "you invented", "not real", "that's a lie", "that is a lie",
    "factually wrong", "factually incorrect", "wrong fact", "bad fact",
    "that never happened", "never existed", "that's false", "that is false",
    "false information", "misinformation", "that's made up", "you're hallucinating",
    "you hallucinated", "wrong information", "invent", "invented that",
    "not true", "untrue", "that's not a thing", "not a thing",
]


def normalize(text: str) -> str:
    return text.lower().strip()


def gate1_references_previous(text: str) -> bool:
    t = normalize(text)
    for sig in RESPONSE_REFERENCE:
        if sig in t:
            return True
    # Short messages (<=6 words) with a clear feedback signal almost always
    # refer to the previous response even without an explicit reference word
    if len(t.split()) <= 6:
        all_signals = (
            SUCCESS_POSITIVE + SUCCESS_NEGATIVE
            + CORRECTNESS_POSITIVE + CORRECTNESS_NEGATIVE
            + RELEVANCE_POSITIVE + RELEVANCE_NEGATIVE
            + FACTSCORE_POSITIVE + FACTSCORE_NEGATIVE
        )
        for sig in all_signals:
            if sig in t:
                return True
    return False


def detect_signal(text: str, positives: list, negatives: list) -> float | None:
    t = normalize(text)
    for sig in positives:
        if sig in t:
            return 0.9
    for sig in negatives:
        if sig in t:
            return 0.1
    return None


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
    tracer = trace.get_tracer("llm_feedback_hook")
    with tracer.start_as_current_span("llm_request_feedback", attributes=attrs):
        time.sleep(0.05)
    tp.shutdown()


if __name__ == "__main__":
    try:
        hook_input = json.loads(sys.stdin.read())
        session_id = hook_input.get("session_id", "")
        message = hook_input.get("prompt", "")
    except Exception:
        sys.exit(0)

    if not session_id or not message:
        sys.exit(0)

    # Gate 1: must reference the previous response
    if not gate1_references_previous(message):
        sys.exit(0)

    # Detect all signals
    success = detect_signal(message, SUCCESS_POSITIVE, SUCCESS_NEGATIVE)
    correctness = detect_signal(message, CORRECTNESS_POSITIVE, CORRECTNESS_NEGATIVE)
    relevance = detect_signal(message, RELEVANCE_POSITIVE, RELEVANCE_NEGATIVE)
    factscore = detect_signal(message, FACTSCORE_POSITIVE, FACTSCORE_NEGATIVE)

    # Gate 2: at least one feedback signal must be present
    if all(v is None for v in [success, correctness, relevance, factscore]):
        sys.exit(0)

    # Compute quality and effectiveness from whatever signals we have
    quality_inputs = [v for v in [correctness, relevance, factscore] if v is not None]
    quality = round(sum(quality_inputs) / len(quality_inputs), 4) if quality_inputs else None

    effectiveness_inputs = [v for v in [success, quality] if v is not None]
    effectiveness = round(sum(effectiveness_inputs) / len(effectiveness_inputs), 4) if effectiveness_inputs else None

    model = get_last_model(session_id)
    attrs: dict = {
        "session_id": session_id,
        "model": model,
        "signal_type": "auto",
    }
    if success is not None:
        attrs["success"] = success
    if correctness is not None:
        attrs["correctness"] = correctness
    if relevance is not None:
        attrs["relevance"] = relevance
    if factscore is not None:
        attrs["factscore"] = factscore
    if quality is not None:
        attrs["quality"] = quality
    if effectiveness is not None:
        attrs["effectiveness"] = effectiveness

    try:
        emit_span(attrs)
    except Exception:
        pass
