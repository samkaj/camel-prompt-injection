# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project context

This is the research artifact for [CaMeL: Defeating Prompt Injections by Design](https://arxiv.org/abs/2503.18813). It is **not maintained**: the interpreter is known to contain bugs (uncaught exceptions, possibly insecure behavior). Treat it as a reproduction codebase rather than production code — don't rewrite or generalize subsystems beyond what a task asks for.

Dependencies are pinned via `uv` (Python 3.10/3.11). Secrets live in `.env` (copy `.env.example`). Most commands run through `uv run --env-file .env ...`.

## Common commands

```bash
# Run the AgentDojo benchmark with CaMeL defense
uv run --env-file .env main.py <provider>:<model>            # e.g. google:gemini-2.5-pro-preview-06-05
uv run --env-file .env main.py <model> --use-original         # baseline: no CaMeL, native tool-calling
uv run --env-file .env main.py <model> --run-attack           # turn on the `important_instructions` attack
uv run --env-file .env main.py <model> --suites slack         # restrict suites (workspace|banking|travel|slack)
uv run --env-file .env main.py <model> --user-tasks user_task_3   # run a single user task
uv run --env-file .env main.py --help                         # full CLI

# Convenience wrapper (defaults to gemini-2.5-pro on slack if no args)
./run.sh <model> [flags]

# Lint / format / typecheck / test
uv run ruff check --fix
uv run ruff format
uv run pyright
uv run pytest
uv run pytest tests/test_camel_lang/test_expressions.py::TestName::test_case   # single test

# Inspect run results
python3 check_results.py --pipeline <pipeline-dir> --suite slack [--detail]
```

Model strings are `<provider>:<model_name>` (`google:`, `openai:`, `anthropic:`). New models must be registered in `_supported_model_names` (or `_oai_thinking_models` for OpenAI reasoning models) in `src/camel/models.py` — the registered string is what the model returns when asked "what model are you?" and is used by `system_prompt_generator`.

Results are written to `logs/<pipeline_name>/<suite>/user_task_<N>/<injection_task or none>/<attack or none>.json`. Each JSON has `utility`, `security`, `duration`, `error`, and the full `messages` trace. `force_rerun=False` in `main.py` means existing log files are reused — delete them to re-run a task.

## Architecture

CaMeL is a **two-LLM defense** that mediates tool calls through a custom Python interpreter with capability/dependency tracking. The big picture:

```
user query
   │
   ▼
PrivilegedLLM ──► generates Python code (no tool-calling API; code is text)
   │
   ▼
camel.interpreter.parse_and_interpret_code
   │  - walks the AST
   │  - dispatches tool calls into AgentDojo's FunctionsRuntime
   │  - propagates Capabilities (sources + readers) through every value
   │  - calls SecurityPolicyEngine on each tool call to authorize it
   │  - delegates unstructured-data parsing to query_ai_assistant (quarantined LLM)
   │
   ├── error?  ─► feed traceback back to PrivilegedLLM, retry (max_attempts)
   └── ok      ─► return assistant message with model_output + tool_calls
```

The interpreter is intentionally **single-file** (`src/camel/interpreter/interpreter.py`) — the AST walker is mutually recursive across statement/expression kinds, so splitting it would create circular imports.

### Key modules

- **`main.py`** — CLI entrypoint (via `cyclopts`); loops over suites, builds a pipeline per suite, runs `benchmark_suite_with[out]_injections`, prints utility/security averages.
- **`src/camel/models.py`** — pipeline factory `make_tools_pipeline()` and the supported-model registry. Branches on `use_original` (baseline tools-loop), `replay_with_policies` (re-evaluate a previous run with security policies), or default (CaMeL `PrivilegedLLM`).
- **`src/camel/pipeline_elements/privileged_llm.py`** — the core CaMeL element. `query()` runs an outer loop (`max_attempts`, currently 3) of generate-code → interpret → on-error feed traceback back; the inner `attempts=5` loop only handles empty-code responses. Registers `query_ai_assistant` as a tool that the generated code calls when it needs to parse unstructured data.
- **`src/camel/quarantined_llm.py`** — the second LLM, wrapped in `pydantic_ai.Agent` with a `result_type` schema. It only sees the unstructured blob the privileged code chose to give it; it can't issue tool calls and must return typed output (raises `NotEnoughInformationError` if it can't).
- **`src/camel/interpreter/`** — the Python subset interpreter. `value.py` defines `CaMeLValue` wrappers (every runtime value carries metadata); `namespace.py` provides the scope object; `library.py` exposes builtin helpers; `op_protocols.py` defines how operators propagate metadata.
- **`src/camel/capabilities/`** — `Capabilities` = `(sources_set, readers_set, other_metadata)`. `sources.py` defines provenance (which tool/user produced the value); `readers.py` defines who is allowed to read it (`Public`, specific principals, etc.). Operators in the interpreter union/intersect these as values are combined.
- **`src/camel/pipeline_elements/security_policies/`** — per-suite engines (`workspace.py`, `banking.py`, `slack.py`, `travel.py`) subclassing `AgentDojoSecurityPolicyEngine`. They inspect the call args' capabilities and decide whether to allow each tool call. `ADNoSecurityPolicyEngine` is the no-op default used during the initial CaMeL run; the strict engines are applied in the `--replay-with-policies` pass.
- **`src/camel/system_prompt_generator.py`** — builds the privileged-LLM system prompt by introspecting `runtime.functions` and emitting Python signatures the model is expected to call. The class-exclusion set (`datetime`, etc.) varies per environment.
- **`src/camel/pipeline_elements/agentdojo_function.py`** — adapts AgentDojo's `Function` objects into something the interpreter can dispatch.
- **`src/camel/pipeline_elements/replay_privileged_llm.py`** — replays a previously logged run, applying security policies after the fact (used by `--replay-with-policies`).
- **`src/camel/chat_turn.py`**, **`custom_yaml.py`**, **`conditional_cache.py`** — message-history bookkeeping, YAML dumper used to render tool results into the prompt, and an LRU-style cache respectively.

### Three execution modes (set in `make_tools_pipeline`)

1. **CaMeL (default)** — `PrivilegedLLM` with `ADNoSecurityPolicyEngine`. Pipeline name suffix: `+camel`.
2. **Original** (`--use-original`) — vanilla `agent_pipeline.ToolsExecutionLoop` with the model's native tool-calling. Can be combined with AgentDojo defenses via `--ad-defense` (`tool_filter`, `repeat_user_prompt`, `spotlight_with_delimiting`). No suffix.
3. **Replay with policies** (`--replay-with-policies`) — `PrivilegedLLMReplayer` re-walks a logged conversation through the per-suite `SecurityPolicyEngine`. Requires the equivalent CaMeL run to already exist on disk. Suffix: `+camel+secpol[+strict]`.

### Tests

`tests/test_camel_lang/` exercises the interpreter (literals, expressions, control flow, classes, function calls, exceptions, the standard library bindings). `tests/test_pipeline_elements/` covers the AgentDojo function adapter and system-prompt generator. There are no end-to-end benchmark tests — those run via `main.py`.
