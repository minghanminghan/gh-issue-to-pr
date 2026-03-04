"""Base agent wrapper: Claude SDK agentic loop with tool execution."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import anthropic

from schemas.state import FailureSource
from tools.logger import log_tool_call
from tools.state import read_state, write_state
from tools.trace import Span, add_span

# Pricing (USD per token)
_INPUT_COST_PER_TOKEN = 5.0 / 1_000_000   # claude-opus-4-6
_OUTPUT_COST_PER_TOKEN = 25.0 / 1_000_000

MODEL = "claude-opus-4-6"


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

    Handles the full Claude SDK agentic loop:
    1. Call API with streaming
    2. On tool_use: dispatch to handler, feed result back
    3. Repeat until end_turn
    4. Update cost in STATE.json
    5. Record span for observability
    """
    run_dir = Path(run_dir)
    client = anthropic.Anthropic()

    messages: list[dict] = [{"role": "user", "content": user_prompt}]

    tokens_in_total = 0
    tokens_out_total = 0
    tools_called: list[str] = []
    files_read: list[str] = []
    files_written: list[str] = []
    final_text = ""

    start_time = datetime.now(timezone.utc).isoformat()

    try:
        while True:
            with client.messages.stream(
                model=MODEL,
                max_tokens=max_tokens,
                thinking={"type": "adaptive"},
                system=system_prompt,
                tools=tool_schemas if tool_schemas else anthropic.NOT_GIVEN,
                messages=messages,
            ) as stream:
                response = stream.get_final_message()

            tokens_in_total += response.usage.input_tokens
            tokens_out_total += response.usage.output_tokens

            # Mid-stream budget check
            running_cost = _calc_cost(tokens_in_total, tokens_out_total)
            budget_exceeded = False
            budget_reason = ""
            try:
                _state = read_state(run_dir)
                if _state.cost_spent_usd + running_cost >= _state.cost_budget_usd:
                    budget_exceeded = True
                    budget_reason = (
                        f"Budget exceeded mid-agent: "
                        f"${_state.cost_spent_usd + running_cost:.4f} >= ${_state.cost_budget_usd:.2f}"
                    )
                    _state.failure_source = FailureSource.budget_exceeded
                    _state.last_failure_reason = budget_reason
                    write_state(run_dir, _state)
            except Exception:
                pass
            if budget_exceeded:
                _record_span(
                    run_dir, agent_name, start_time,
                    tokens_in_total, tokens_out_total, running_cost,
                    tools_called, files_read, files_written, "fail",
                )
                _update_cost(run_dir, running_cost)
                return AgentResult(
                    ok=False,
                    output="",
                    tokens_in=tokens_in_total,
                    tokens_out=tokens_out_total,
                    cost_usd=running_cost,
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
                tokens_in=response.usage.input_tokens,
                tokens_out=response.usage.output_tokens,
                cost_usd=_calc_cost(response.usage.input_tokens, response.usage.output_tokens),
            )

            # Collect text from this response
            for block in response.content:
                if block.type == "text":
                    final_text = block.text

            # Check stop reason
            if response.stop_reason == "end_turn":
                break

            if response.stop_reason != "tool_use":
                # pause_turn or unexpected — break out
                break

            # Process tool calls
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for tool_block in tool_use_blocks:
                tool_name = tool_block.name
                tool_input = tool_block.input
                tools_called.append(tool_name)

                handler = tool_handlers.get(tool_name)
                if handler is None:
                    result_content = f"Error: tool '{tool_name}' is not available for this agent."
                    is_error = True
                else:
                    try:
                        result = handler(**tool_input)
                        # Track file reads/writes
                        if tool_name == "read_file":
                            files_read.append(tool_input.get("path", ""))
                        elif tool_name in ("write_file", "create_file", "append_file"):
                            files_written.append(tool_input.get("path", ""))

                        result_content = result.get("output", "") or result.get("error", "")
                        is_error = not result.get("ok", False)

                        log_tool_call(
                            run_dir=run_dir,
                            agent=agent_name,
                            tool=tool_name,
                            args_summary=_summarise_args(tool_input),
                            ok=result.get("ok", False),
                        )
                    except Exception as e:
                        result_content = f"Tool execution error: {e}"
                        is_error = True

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_block.id,
                    "content": result_content,
                    "is_error": is_error,
                })

            messages.append({"role": "user", "content": tool_results})

    except anthropic.APIError as e:
        cost = _calc_cost(tokens_in_total, tokens_out_total)
        _record_span(
            run_dir, agent_name, start_time,
            tokens_in_total, tokens_out_total, cost,
            tools_called, files_read, files_written, "fail",
        )
        _update_cost(run_dir, cost)
        return AgentResult(
            ok=False,
            output="",
            tokens_in=tokens_in_total,
            tokens_out=tokens_out_total,
            cost_usd=cost,
            failure_reason=f"API error: {e}",
        )

    cost = _calc_cost(tokens_in_total, tokens_out_total)
    _record_span(
        run_dir, agent_name, start_time,
        tokens_in_total, tokens_out_total, cost,
        tools_called, files_read, files_written, "pass",
    )
    _update_cost(run_dir, cost)

    return AgentResult(
        ok=True,
        output=final_text,
        tokens_in=tokens_in_total,
        tokens_out=tokens_out_total,
        cost_usd=cost,
        tools_called=tools_called,
        files_read=files_read,
        files_written=files_written,
    )


def _calc_cost(tokens_in: int, tokens_out: int) -> float:
    return tokens_in * _INPUT_COST_PER_TOKEN + tokens_out * _OUTPUT_COST_PER_TOKEN


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
