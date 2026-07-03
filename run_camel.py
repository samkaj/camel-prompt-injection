"""Standalone CaMeL runner with user-defined tools and policies.

Loads a Python file that exposes:

  TOOLS: list[Callable]                     # your tool functions (need docstring + type hints)
  POLICIES: list[tuple[str, SecurityPolicy]] # optional: fnmatch pattern -> policy fn
  NO_SIDE_EFFECT_TOOLS: set[str]            # optional: tools with no side effects
  SECURITY_POLICY_ENGINE: type              # optional: full-custom engine class

If POLICIES and SECURITY_POLICY_ENGINE are both absent the runner allows every
tool call (like `ADNoSecurityPolicyEngine`). If POLICIES is provided the base
`SecurityPolicyEngine` logic from `camel.security_policy` is used, so unmatched
tools are DENIED by default -- match a broad pattern like `"*"` for a fallback.

Example:
    uv run --env-file .env run_camel.py \
        --tools examples/custom_camel_tools.py \
        --model google:gemini-2.5-flash \
        "search for a mug and send it to my colleague at alice@example.com"

API keys are read from env vars (GOOGLE_API_KEY / OPENAI_API_KEY /
ANTHROPIC_API_KEY), matching the behaviour of `main.py`.
"""

import argparse
import importlib.util
import os
import sys
from pathlib import Path

from agentdojo import agent_pipeline, functions_runtime
from agentdojo import types as ad_types
from agentdojo import task_suite as _ad_task_suite  # noqa: F401  # side-effect: registers default suites so nested imports below don't cycle

import camel.custom_yaml  # noqa: F401  # registers yaml representers used by tool output rendering
from camel.interpreter.interpreter import MetadataEvalMode
from camel.pipeline_elements.privileged_llm import PrivilegedLLM
from camel.security_policy import Allowed, SecurityPolicyEngine


def _load_user_module(path: Path):
    if not path.is_file():
        raise FileNotFoundError(f"Tools file not found: {path}")
    spec = importlib.util.spec_from_file_location(f"_camel_user_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not build import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _build_llm(model: str) -> agent_pipeline.BasePipelineElement:
    if ":" not in model:
        raise ValueError(f"--model must be '<provider>:<name>' (got {model!r})")
    provider, name = model.split(":", 1)
    if provider == "google":
        from google import genai

        client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        llm = agent_pipeline.GoogleLLM(name, client, max_tokens=65535)
    elif provider == "openai":
        import openai

        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        llm = agent_pipeline.OpenAILLM(client, name, None)
    elif provider == "anthropic":
        import anthropic

        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        llm = agent_pipeline.AnthropicLLM(client, name, max_tokens=8192)
    else:
        raise ValueError(f"Unknown provider {provider!r}. Expected google|openai|anthropic.")
    llm.name = name
    return llm


def _build_engine_class(module) -> type[SecurityPolicyEngine]:
    if getattr(module, "SECURITY_POLICY_ENGINE", None) is not None:
        return module.SECURITY_POLICY_ENGINE

    policies = list(getattr(module, "POLICIES", []))
    no_side_effect_tools = set(getattr(module, "NO_SIDE_EFFECT_TOOLS", set()))

    if not policies:
        class _AllowAllEngine(SecurityPolicyEngine):
            def __init__(self, env=None) -> None:
                self.policies = []
                self.no_side_effect_tools = no_side_effect_tools

            def check_policy(self, tool_name, kwargs, dependencies):
                return Allowed()

        return _AllowAllEngine

    class _UserEngine(SecurityPolicyEngine):
        def __init__(self, env=None) -> None:
            self.policies = policies
            self.no_side_effect_tools = no_side_effect_tools

    return _UserEngine


def _final_assistant_text(messages) -> str:
    for msg in reversed(messages):
        if msg["role"] == "assistant" and msg.get("content"):
            return ad_types.get_text_content_as_str(msg["content"])
    return ""


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_camel.py",
        description="Run CaMeL against a user-supplied tools/policies module.",
    )
    parser.add_argument("--tools", required=True, type=Path, help="Path to a Python file defining TOOLS/POLICIES.")
    parser.add_argument("--model", default="google:gemini-2.5-flash", help="Privileged LLM as <provider>:<name>.")
    parser.add_argument("--q-llm", dest="q_llm", default=None, help="Quarantined LLM. Defaults to --model.")
    parser.add_argument(
        "--eval-mode",
        choices=[m.value for m in MetadataEvalMode],
        default=MetadataEvalMode.NORMAL.value,
        help="Metadata propagation mode.",
    )
    parser.add_argument("--max-attempts", type=int, default=3, help="Retries after an interpreter error.")
    parser.add_argument("query", nargs="+", help="The user query.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    module = _load_user_module(args.tools.resolve())

    runtime = functions_runtime.FunctionsRuntime()
    for tool in getattr(module, "TOOLS", []):
        runtime.register_function(tool)

    llm = _build_llm(args.model)
    engine_class = _build_engine_class(module)

    p_llm = PrivilegedLLM(
        llm,
        engine_class,  # type: ignore[arg-type]  # our engine follows the same protocol
        args.q_llm or args.model,  # type: ignore[arg-type]
        eval_mode=MetadataEvalMode(args.eval_mode),
        max_attempts=args.max_attempts,
    )

    _, _, _, messages, _ = p_llm.query(" ".join(args.query), runtime)

    print("\n=== Final assistant output ===")
    print(_final_assistant_text(messages))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
