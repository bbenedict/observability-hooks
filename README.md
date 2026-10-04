# observability-hooks

Files for sending Claude Code telemetry to an OpenTelemetry collector (OTLP/HTTP on `localhost:4318`), so it can be viewed in Grafana, Tempo and Prometheus.

The collector and dashboards are set up separately. Follow the README in the [observability project](https://github.com/bbenedict/observability) first.

This repo has three parts:

- **claude-hooks**: global hook scripts and settings that record every Claude Code session.
- **claude-commands**: the `/llm-feedback` command for rating Claude's last response.
- **project-hooks**: scripts a project can use to send its own spans.

## claude-hooks

These scripts run as Claude Code hooks in every project.

- `llm_request_area.py`: classifies each prompt by area (UserPromptSubmit).
- `llm_request_extra.py`: records token usage and cost for each response (Stop).
- `llm_request_feedback.py`: picks up feedback from what you type (UserPromptSubmit, set per project).
- `llm_feedback_emit.py`: sends the scores from `/llm-feedback`.

All four send spans with the service name `claude_code`.

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
      { "hooks": [{ "type": "command", "command": "uv run --project ~/.claude/lib ~/.claude/lib/llm_request_extra.py", "timeout": 30 }] }
    ],
    "UserPromptSubmit": [
      { "hooks": [{ "type": "command", "command": "uv run --project ~/.claude/lib ~/.claude/lib/llm_request_area.py", "timeout": 30 }] }
    ]
  }
}
```

The `env` settings turn on Claude Code's own traces, such as `claude_code.interaction` and `claude_code.llm_request`, under the service name `claude-code`. They must be in `~/.claude/settings.json`; Claude Code ignores them in a project's settings. See [Claude Code monitoring](https://code.claude.com/docs/en/monitoring-usage).

4. To pick up feedback from what you type in a project, add this to that project's `.claude/settings.json`:

```json
"hooks": {
  "UserPromptSubmit": [
    { "hooks": [{ "type": "command", "command": "uv run --project ~/.claude/lib ~/.claude/lib/llm_request_feedback.py", "timeout": 30 }] }
  ]
}
```

5. Restart Claude Code.

## claude-commands

- `llm-feedback.md`: the `/llm-feedback` command. It asks four 1–10 questions (success, correctness, relevance, factual accuracy), works out quality and effectiveness scores, and sends them as an `llm_request_feedback` span.

### Setup

1. Copy `llm-feedback.md` into a project's `.claude/commands/` folder.
2. Add `"Bash(uv run *)"` to `permissions.allow` in that project's `.claude/settings.local.json`, so the command doesn't ask for permission each time.

This command needs the claude-hooks setup, because it runs `~/.claude/lib/llm_feedback_emit.py`.

## project-hooks

Scripts a project can use to send its own spans, such as one at the end of a batch of work.

- `observability.py`: sets up the connection to the collector for traces and metrics.
- `emit.py`: sends one span from the command line.
- `pyproject.toml`: the dependencies these scripts need.

### Setup

1. Copy `observability.py` and `emit.py` into the project's `.claude/lib/` folder. They must be in the same folder.
2. Set `SERVICE_NAME` in `observability.py` to the project's name.
3. Add the dependencies in `pyproject.toml` to the project's own `pyproject.toml` and run `uv sync`. If the project doesn't have one, use this file.

### Usage

Call `emit.py` from a project command (a `.md` file in `.claude/commands/`) where you want to record something:

```
uv run python .claude/lib/emit.py span myproject.task.complete task=import items_processed=42 errors=0
```

Span names follow the pattern `<project>.<operation>`. Attributes are `key=value` pairs. Numbers are sent as int, or as float if the value contains a `.`; everything else is sent as a string.
