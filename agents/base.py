"""Base agent wrapper: LiteLLM agentic loop with tool execution."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import litellm
from litellm import completion_cost

from schemas.state import FailureSource
from tools.logger import log_tool_call
from tools.state import read_state, write_state
from tools.trace import Span, add_span

MODEL = os.environ.get("LLM_MODEL")
if MODEL is None:
    raise ValueError("LLM_MODEL environment variable is not set")

@dataclass
class AgentResult:
    ok: bool
    output: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    tools_called: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)
    failure_source: Optional[str] = None
    failure_reason: Optional[str] = None


def run_agent(
    agent_name: str,
    system_prompt: str,
    user_prompt: str,
    tool_schemas: list[dict],
    tool_handlers: dict[str, Callable[..., Any]],
    run_dir: Path,
    max_tokens: int = 8192,
) -> AgentResult:
    """
    Run an agent with the given prompts and tools.

    Handles the full LiteLLM agentic loop:
    1. Call API
    2. On tool_calls: dispatch to handler, feed result back
    3. Repeat until stop
    4. Update cost in STATE.json
    5. Record span for observability
    """
    run_dir = Path(run_dir)

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    tokens_in_total = 0
    tokens_out_total = 0
    cost_total = 0.0
    tools_called: list[str] = []
    files_read: list[str] = []
    files_written: list[str] = []
    final_text = ""

    start_time = datetime.now(timezone.utc).isoformat()

    try:
        while True:
            response = litellm.completion(
                model=MODEL,
                max_tokens=max_tokens,
                messages=messages,
                tools=tool_schemas,
                reasoning_effort="high",
            )

            tokens_in_total += response.usage.prompt_tokens
            tokens_out_total += response.usage.completion_tokens
            call_cost = completion_cost(completion_response=response)
            cost_total += call_cost

            # Mid-turn budget check
            budget_exceeded = False
            budget_reason = ""
            try:
                _state = read_state(run_dir)
                if _state.cost_spent_usd + cost_total >= _state.cost_budget_usd:
                    budget_exceeded = True
                    budget_reason = (
                        f"Budget exceeded mid-agent: "
                        f"${_state.cost_spent_usd + cost_total:.4f} >= ${_state.cost_budget_usd:.2f}"
                    )
                    _state.failure_source = FailureSource.budget_exceeded
                    _state.last_failure_reason = budget_reason
                    write_state(run_dir, _state)
            except Exception:
                pass
            if budget_exceeded:
                _record_span(
                    run_dir, agent_name, start_time,
                    tokens_in_total, tokens_out_total, cost_total,
                    tools_called, files_read, files_written, "fail",
                )
                _update_cost(run_dir, cost_total)
                return AgentResult(
                    ok=False,
                    output="",
                    tokens_in=tokens_in_total,
                    tokens_out=tokens_out_total,
                    cost_usd=cost_total,
                    failure_source="budget_exceeded",
                    failure_reason=budget_reason,
                )

            # Log to RUN.log
            log_tool_call(
                run_dir=run_dir,
                agent=agent_name,
                tool="api_call",
                args_summary=f"messages={len(messages)}",
                ok=True,
                tokens_in=response.usage.prompt_tokens,
                tokens_out=response.usage.completion_tokens,
                cost_usd=call_cost,
            )

            # Collect text from this response
            msg_content = response.choices[0].message.content
            if isinstance(msg_content, str):
                final_text = msg_content

            # Check finish reason
            finish_reason = response.choices[0].finish_reason
            if finish_reason == "stop":
                break
            if finish_reason != "tool_calls":
                break

            # Process tool calls
            tool_calls = response.choices[0].message.tool_calls or []
            messages.append(response.choices[0].message.model_dump(exclude_none=True))

            for tc in tool_calls:
                tool_name = tc.function.name
                tool_input = json.loads(tc.function.arguments)
                tools_called.append(tool_name)

                handler = tool_handlers.get(tool_name)
                if handler is None:
                    result_content = f"Error: tool '{tool_name}' is not available for this agent."
                else:
                    try:
                        result = handler(**tool_input)
                        # Track file reads/writes
                        if tool_name == "read_file":
                            files_read.append(tool_input.get("path", ""))
                        elif tool_name in ("write_file", "create_file", "append_file"):
                            files_written.append(tool_input.get("path", ""))

                        result_content = result.get("output", "") or result.get("error", "")

                        log_tool_call(
                            run_dir=run_dir,
                            agent=agent_name,
                            tool=tool_name,
                            args_summary=_summarise_args(tool_input),
                            ok=result.get("ok", False),
                        )
                    except Exception as e:
                        result_content = f"Tool execution error: {e}"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tool_name,
                    "content": result_content,
                })

    except litellm.exceptions.APIError as e:
        _record_span(
            run_dir, agent_name, start_time,
            tokens_in_total, tokens_out_total, cost_total,
            tools_called, files_read, files_written, "fail",
        )
        _update_cost(run_dir, cost_total)
        return AgentResult(
            ok=False,
            output="",
            tokens_in=tokens_in_total,
            tokens_out=tokens_out_total,
            cost_usd=cost_total,
            failure_reason=f"API error: {e}",
        )

    _record_span(
        run_dir, agent_name, start_time,
        tokens_in_total, tokens_out_total, cost_total,
        tools_called, files_read, files_written, "pass",
    )
    _update_cost(run_dir, cost_total)

    return AgentResult(
        ok=True,
        output=final_text,
        tokens_in=tokens_in_total,
        tokens_out=tokens_out_total,
        cost_usd=cost_total,
        tools_called=tools_called,
        files_read=files_read,
        files_written=files_written,
    )


def _update_cost(run_dir: Path, cost: float) -> None:
    try:
        state = read_state(run_dir)
        state.cost_spent_usd += cost
        write_state(run_dir, state)
    except Exception:
        pass


def _record_span(
    run_dir: Path,
    agent: str,
    start_time: str,
    tokens_in: int,
    tokens_out: int,
    cost: float,
    tools_called: list[str],
    files_read: list[str],
    files_written: list[str],
    outcome: str,
) -> None:
    span = Span(
        agent=agent,
        start_time=start_time,
        end_time=datetime.now(timezone.utc).isoformat(),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        tools_called=tools_called,
        files_read=files_read,
        files_written=files_written,
        outcome=outcome,
    )
    add_span(run_dir, span)


def _summarise_args(tool_input: dict) -> str:
    parts = []
    for k, v in tool_input.items():
        val = str(v)
        if len(val) > 60:
            val = val[:57] + "..."
        parts.append(f"{k}={val!r}")
    return ", ".join(parts)
