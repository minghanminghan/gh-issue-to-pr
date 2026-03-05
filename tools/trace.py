"""Observability: span model, trace aggregation, and OTLP export."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry import trace as otel_trace
from opentelemetry.trace import SpanKind

# In-memory registry of open traces (keyed by run_dir string)
_open_traces: dict[str, dict] = {}


@dataclass
class Span:
    agent: str
    start_time: str  # ISO-8601
    end_time: str  # ISO-8601
    tokens_in: int
    tokens_out: int
    cost_usd: float
    tools_called: list[str] = field(default_factory=list)


def open_trace(run_dir: Path) -> None:
    """Called by setup step: open a new trace for this run."""
    key = str(run_dir)
    _open_traces[key] = {
        "run_id": Path(run_dir).name,
        "start_time": datetime.now(timezone.utc).isoformat(),
        "spans": [],
    }


def add_span(run_dir: Path, span: Span) -> None:
    """Add a completed span to the open trace."""
    key = str(run_dir)
    if key not in _open_traces:
        # Silently create trace if not opened (e.g. resumed run)
        open_trace(run_dir)
    _open_traces[key]["spans"].append(asdict(span))


def close_trace(run_dir: Path, outcome: str) -> None:
    """
    Called by the Report step. Writes TRACE.json and optionally exports to
    Arize Phoenix via OTLP if PHOENIX_COLLECTOR_ENDPOINT is set.
    """
    run_dir = Path(run_dir)
    key = str(run_dir)
    trace = _open_traces.pop(key, {})

    if not trace:
        trace = {
            "run_id": run_dir.name,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "spans": [],
        }

    # Load STATE.json for additional metadata
    state_data: dict = {}
    state_path = run_dir / "STATE.json"
    if state_path.exists():
        with open(state_path) as f:
            state_data = json.load(f)

    spans = trace.get("spans", [])
    total_cost = sum(s.get("cost_usd", 0.0) for s in spans)
    total_tokens_in = sum(s.get("tokens_in", 0) for s in spans)
    total_tokens_out = sum(s.get("tokens_out", 0) for s in spans)

    trace_json = {
        "run_id": trace["run_id"],
        "issue_url": state_data.get("issue_url", ""),
        "start_time": trace["start_time"],
        "end_time": datetime.now(timezone.utc).isoformat(),
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out,
        "total_cost_usd": total_cost,
        "loop_count": state_data.get("loop_count", 0),
        "outcome": outcome,
        "human_feedback": None,
        "spans": spans,
    }

    (run_dir / "TRACE.json").write_text(json.dumps(trace_json, indent=2))

    # Export to Arize Phoenix if configured
    endpoint = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT")
    if endpoint:
        _export_to_phoenix(trace_json, endpoint)


def _export_to_phoenix(trace_json: dict, endpoint: str) -> None:
    """Export trace to Arize Phoenix via OTLP."""
    try:
        resource = Resource.create({"service.name": "gh-issue-to-pr"})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
        provider.add_span_processor(BatchSpanProcessor(exporter))
        otel_trace.set_tracer_provider(provider)

        tracer = otel_trace.get_tracer("gh-issue-to-pr")

        with tracer.start_as_current_span(
            "pipeline_run",
            kind=SpanKind.INTERNAL,
        ) as root_span:
            root_span.set_attribute("run_id", trace_json["run_id"])
            root_span.set_attribute("issue_url", trace_json["issue_url"])
            root_span.set_attribute("outcome", trace_json["outcome"])
            root_span.set_attribute("total_cost_usd", trace_json["total_cost_usd"])
            root_span.set_attribute("loop_count", trace_json["loop_count"])

            for span_data in trace_json["spans"]:
                with tracer.start_as_current_span(
                    f"agent.{span_data['agent']}",
                    kind=SpanKind.INTERNAL,
                ) as span:
                    span.set_attribute("tokens_in", span_data.get("tokens_in", 0))
                    span.set_attribute("tokens_out", span_data.get("tokens_out", 0))
                    span.set_attribute("cost_usd", span_data.get("cost_usd", 0.0))
                    span.set_attribute("outcome", span_data.get("outcome", "unknown"))

        provider.force_flush()

    except Exception as e:
        print(f"Warning: OTLP export to Phoenix failed: {e}", file=sys.stderr)
