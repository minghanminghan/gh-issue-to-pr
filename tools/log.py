"""Centralized logging setup and structured JSON-line event logger."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry import trace as otel_trace
from openinference.instrumentation.litellm import LiteLLMInstrumentor

from dotenv import load_dotenv

load_dotenv()

_log_level = getattr(
    logging, os.getenv("LOG_LEVEL", "WARNING").upper(), logging.WARNING
)

logging.basicConfig(
    level=_log_level,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)

# Cap noisy external loggers at INFO regardless of LOG_LEVEL
_external_level = max(_log_level, logging.INFO)
for _name in ("litellm", "LiteLLM", "minisweagent", "agent", "httpx", "httpcore"):
    logging.getLogger(_name).setLevel(_external_level)

# Instrument LiteLLM for tracing if endpoint is configured
_otel_endpoint = os.getenv("OTEL_COLLECTOR_ENDPOINT")
if _otel_endpoint:
    _provider = TracerProvider()
    _provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{_otel_endpoint}/v1/traces"))
    )
    otel_trace.set_tracer_provider(_provider)
    LiteLLMInstrumentor().instrument(tracer_provider=_provider)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_tool_call(
    run_dir: Path,
    agent: str,
    tool: str,
    args_summary: str,
    ok: bool,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "tool": tool,
        "args_summary": args_summary,
        "ok": ok,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost_usd,
    }
    log_path = Path(run_dir) / "RUN.log"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# def log_event( # deprecating and thinking of another way to do this
#     run_dir: Path,
#     event: str,
#     data: dict[str, Any] | None = None,
# ) -> None:
#     entry = {
#         "timestamp": datetime.now(timezone.utc).isoformat(),
#         "event": event,
#         **(data or {}),
#     }
#     log_path = Path(run_dir) / "RUN.log"
#     with open(log_path, "a", encoding="utf-8") as f:
#         f.write(json.dumps(entry) + "\n")
