# llm-feedback

Collect explicit feedback on the previous Claude response and emit an `llm_request_feedback` span to Tempo.

## Steps

1. Ask the user the following questions **one at a time**, waiting for each answer before asking the next:

   **Success** — "On a scale of 1–10, how successfully did Claude complete the task? (1 = completely failed, 10 = fully accomplished)"

   **Correctness** — "On a scale of 1–10, how correct was Claude's response? (1 = major errors, 10 = entirely correct)"

   **Relevance** — "On a scale of 1–10, how relevant was Claude's response to your actual question? (1 = completely off-topic, 10 = directly on point)"

   **FActScore** — "On a scale of 1–10, how factually accurate was Claude's response? (1 = significant fabrications, 10 = all facts verified and correct)"

2. Validate each answer is an integer between 1 and 10. If not, ask again.

3. Compute:
   - `success` = answer / 10
   - `correctness` = answer / 10
   - `relevance` = answer / 10
   - `factscore` = answer / 10
   - `quality` = round(avg(correctness, relevance, factscore), 4)
   - `effectiveness` = round(avg(success, quality), 4)

4. Read the session ID from the hook input context. If not available, read `.claude/active-user` to get the user ID for context.

5. Emit the span:
   ```
   uv run --project ~/.claude/lib ~/.claude/lib/llm_feedback_emit.py \
     --session-id "<session_id>" \
     --success <success> \
     --correctness <correctness> \
     --relevance <relevance> \
     --factscore <factscore> \
     --quality <quality> \
     --effectiveness <effectiveness>
   ```

6. Confirm: "Feedback recorded. effectiveness: <effectiveness>, quality: <quality>, success: <success>"
