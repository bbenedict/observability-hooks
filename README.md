# observability-hooks

Files for sending Claude Code telemetry to an OpenTelemetry collector (OTLP/HTTP on `localhost:4318`). The hook scripts here send **metrics to Prometheus**, viewed in Grafana. Claude Code's own built-in traces still go to Tempo, which the `env` settings below turn on.

The collector, Prometheus, Tempo and Grafana are set up separately. Follow the README in the [observability project](https://github.com/bbenedict/observability) first. That stack must include the collector's `deltatocumulative` processor, because these scripts send delta metrics (see note at the end).

This repo has four parts:

- **claude-hooks**: global hook scripts that record every Claude Code session as Prometheus metrics, plus feedback and governance hooks.
- **claude-commands**: the `/llm-feedback` command for rating Claude's last response.
- **project-hooks**: scripts a project can use to send its own metrics.
- **dashboards**: a ready-made Grafana dashboard for all of the above.

## claude-hooks

These scripts run as Claude Code hooks in every project. All metrics are sent under the service name `claude_code`.

- `llm_request_area.py` (UserPromptSubmit): classifies each prompt across five areas (project, agentic, feedback, research, other) → counter `llm_request_area_score{area, model}`.
- `llm_request_extra.py` (Stop): token usage and cost for every API call since the hook last ran (a turn that uses tools makes several) → histograms `llm_request_tokens{type, model}` and `llm_request_cost_dollars{model}`. Cost uses each model's own list prices (input, output, cache reads, and 5-minute or 1-hour cache writes; fast mode at 2×), kept in the `PRICES` table at the top of the script.
- `llm_feedback_judge.py` (UserPromptSubmit): scores your feedback on Claude's previous response, on every prompt → histogram `llm_request_feedback_score{score, signal_type, model}`. See its own section below.
- `llm_feedback_emit.py`: sends the scores from `/llm-feedback` (same metric, `signal_type="manual"`).
- `llm_governance.py` (UserPromptSubmit, Stop, PostToolUse/WebFetch): checks prompts, responses and fetched pages for privacy, security, content, bias and misuse → histograms `llm_governance_score{area}` and `llm_governance_flag`. See its own section below.

### Setup

1. Copy the contents of `claude-hooks/` into `~/.claude/lib/`.
2. Run `uv sync` in `~/.claude/lib/`.
3. Add the following to `~/.claude/settings.json`:

```json
{
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
    "OTEL_TRACES_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318"
  },
  "hooks": {
    "Stop": [
      { "hooks": [
        { "type": "command", "command": "uv run --project ~/.claude/lib ~/.claude/lib/llm_request_extra.py", "timeout": 30 },
        { "type": "command", "command": "uv run --project ~/.claude/lib ~/.claude/lib/llm_governance.py", "timeout": 30 }
      ] }
    ],
    "UserPromptSubmit": [
      { "hooks": [
        { "type": "command", "command": "uv run --project ~/.claude/lib ~/.claude/lib/llm_request_area.py", "timeout": 30 },
        { "type": "command", "command": "uv run --project ~/.claude/lib ~/.claude/lib/llm_governance.py", "timeout": 30 },
        { "type": "command", "command": "uv run --project ~/.claude/lib ~/.claude/lib/llm_feedback_judge.py", "timeout": 30 }
      ] }
    ],
    "PostToolUse": [
      { "matcher": "WebFetch", "hooks": [
        { "type": "command", "command": "uv run --project ~/.claude/lib ~/.claude/lib/llm_governance.py", "timeout": 30 }
      ] }
    ]
  }
}
```

The `env` settings turn on Claude Code's own built-in traces (`claude_code.interaction`, `claude_code.llm_request`), which go to Tempo under the service name `claude-code`. They must be in `~/.claude/settings.json`; Claude Code ignores them in a project's settings. See [Claude Code monitoring](https://code.claude.com/docs/en/monitoring-usage).

4. Restart Claude Code.

### Example Grafana queries (Prometheus)

These respect the dashboard time range. Metrics are cumulative counters, so use `increase` over `$__range`:

```
# Prompt area split (pie/bar)
round(sum by (area) (increase(llm_request_area_score_total[$__range])))

# Average effectiveness (stat)
sum(increase(llm_request_feedback_score_sum{score="effectiveness"}[$__range]))
/
sum(increase(llm_request_feedback_score_count{score="effectiveness"}[$__range]))

# Average effectiveness by signal type: default baseline vs judged vs manual (bar)
sum by (signal_type) (increase(llm_request_feedback_score_sum{score="effectiveness"}[$__range]))
/
sum by (signal_type) (increase(llm_request_feedback_score_count{score="effectiveness"}[$__range]))

# Governance findings by area (bar): checks minus the clean (score 0) checks
round(sum by (area) (increase(llm_governance_score_count[$__range])) - sum by (area) (increase(llm_governance_score_bucket{le="0.0"}[$__range])))

# Governance find rate (stat): share of checks with any finding
sum(increase(llm_governance_flag_sum[$__range])) / sum(increase(llm_governance_flag_count[$__range]))
```

## claude-commands

- `llm-feedback.md`: the `/llm-feedback` command. It asks four 1–10 questions (success, correctness, relevance, factual accuracy), works out quality and effectiveness scores, and records them via `llm_feedback_emit.py`.

### Setup

1. Copy `llm-feedback.md` into a project's `.claude/commands/` folder.
2. Add `"Bash(uv run *)"` to `permissions.allow` in that project's `.claude/settings.local.json`, so the command doesn't ask for permission each time.

This command needs the claude-hooks setup, because it runs `~/.claude/lib/llm_feedback_emit.py`.

## Feedback hook

`llm_feedback_judge.py` scores your feedback on Claude's previous response on every prompt, so every response gets scores for **success, correctness, relevance, and factscore**, plus **quality** (average of correctness, relevance, factscore) and **effectiveness** (average of success and quality). The hook hands the work to a background process and exits, so it adds no wait to your prompt.

- **Detect (Haiku):** reads your message plus the end of Claude's previous response and gives, for each score, the probability (0–1) that your message contains meaningful feedback on it. Haiku only detects; it doesn't rate.
- **Default:** scores below 0.4 get **0.85**, recorded as `signal_type="default"`. No recognizable feedback means the session is going well, so the defaults form a baseline and real feedback stands out.
- **Rate (Sonnet):** scores at or above 0.4 are rated by a Claude Sonnet judge, using a rubric with example phrases for each score. Sonnet's rating is the final score, recorded as `signal_type="auto"`. If Sonnet finds no real feedback on a score, it falls back to the default.
- **Manual:** ratings from `/llm-feedback` are extra data points with `signal_type="manual"`.
- **Failures:** an API error, timeout, or refusal falls back to the default.
- **Telemetry:** only the scores are sent; no message content ever leaves the machine.

The first prompt of a session isn't scored (there's no previous response yet), and the last response of a session never gets a score (there's no next prompt). The same requirements as the governance hook apply (below).

## Governance hook

`llm_governance.py` checks prompts, Claude's responses, and WebFetch results for issues in five areas: **privacy, security, content, bias, misuse**. It's warn-only; it never blocks.

- **Fast check (every message, while you wait):** pattern checks (SSNs, Luhn-valid cards, API keys) plus a Claude Haiku screen. If anything scores above 0.25, a warning is shown in the chat and a background deep check starts.
- **Deep check (background):** a Claude Sonnet judge scores each area. The recorded score is `max(pattern, 0.8 × Sonnet + 0.2 × Haiku)`, so Haiku can't record a finding on its own, but slight agreement from Sonnet crosses the line. A safety-classifier refusal is recorded at 1.0 in the mapped area.
- **Telemetry:** every check is recorded, clean or not. Only the area and score are sent; no message content ever leaves the machine.
  - `llm_governance_score{area}`: each area's finding score (above 0.25), or 0 if that area had no finding.
  - `llm_governance_flag`: 1 if any area had a finding, otherwise 0. Its sum ÷ count is the find rate.
  - Clean fast checks are recorded when the fast check ends; flagged checks are recorded after the deep check.

Requirements:
- An `ANTHROPIC_API_KEY` available to the hook. The Haiku and Sonnet calls are billed to that key (your Anthropic API account), not your Claude Code subscription.
- The `anthropic` package, which is in `claude-hooks/pyproject.toml`.

## project-hooks

Scripts a project can use to send its own metrics, such as counts at the end of a batch of work.

- `observability.py`: sets up the metrics connection to the collector.
- `emit.py`: records metrics from the command line.
- `pyproject.toml`: the dependencies these scripts need.

### Setup

1. Copy `observability.py` and `emit.py` into the project's `.claude/lib/` folder. They must be in the same folder.
2. Set `SERVICE_NAME` in `observability.py` to the project's name.
3. Add the dependencies in `pyproject.toml` to the project's own `pyproject.toml` and run `uv sync`. If the project doesn't have one, use this file.

### Usage

Call `emit.py` from a project command (a `.md` file in `.claude/commands/`) where you want to record something:

```
uv run python .claude/lib/emit.py metric myproject.task.complete task=import items_processed=42 errors=0
```

Each numeric attribute becomes a histogram named `<operation>.<attribute>` (for example `myproject_task_complete_items_processed_sum`); string attributes become labels. Numbers are sent as int, or as float if the value contains a `.`; everything else is a string label.

## dashboards

The JSON file in `dashboards/` is the **AI Perf** Grafana dashboard. It shows everything the claude-hooks setup records, plus Claude Code's own traces:

- **AI effectiveness, Task success, Task quality** (stat and trend): feedback scores from `llm_feedback_judge.py` and `/llm-feedback`.
- **Cost dollars**: per-API-call cost from `llm_request_extra.py`.
- **TTFT (ms), Duration (ms), Context tokens, Input tokens, Cache tokens - read, Output tokens**: per-request values from Claude Code's built-in traces in Tempo.
- **Agentic area**: the prompt area split from `llm_request_area.py`.
- **Governance %**: the share of governance checks with no finding, from `llm_governance_flag`.
- **Governance**: confirmed findings per area, from `llm_governance_score`.

It's exported in Grafana's v2 dashboard format, which `grafana/grafana:latest` (Grafana 12 and later) imports directly. Its queries don't name a specific data source, so Grafana uses the default Prometheus and Tempo data sources, which the [observability project](https://github.com/bbenedict/observability) creates automatically.

### Setup

1. Start the observability stack and finish the claude-hooks setup above.
2. In Grafana (http://localhost:3000), go to **Dashboards → New → Import**.
3. Upload the JSON file from `dashboards/` and click **Import**.

Panels fill in as you use Claude Code. Panels for metrics that haven't been recorded yet (for example, governance findings) show "No data" until the first one arrives.

## A note on delta metrics

Every hook runs as a short-lived process, so each one sends its metrics as **deltas**. The collector's `deltatocumulative` processor turns those into the running totals Prometheus expects. Without it, each run would overwrite the last instead of adding to it. The collector config lives in the [observability project](https://github.com/bbenedict/observability).
