"""OpenTelemetry setup

Routes:
  Traces → Tempo (OTLP/HTTP localhost:4318/v1/traces)
"""
from __future__ import annotations

from typing import Optional

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

OTLP_ENDPOINT = "http://localhost:4318"
SERVICE_NAME = "myproject"

_tracer_provider: Optional[TracerProvider] = None
_meter_provider: Optional[MeterProvider] = None


def setup_telemetry(
    endpoint: str = OTLP_ENDPOINT,
    service_name: str = SERVICE_NAME,
) -> tuple[TracerProvider, MeterProvider]:
    global _tracer_provider, _meter_provider

    resource = Resource.create({"service.name": service_name})

    tp = TracerProvider(resource=resource)
    tp.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
    )
    trace.set_tracer_provider(tp)
    _tracer_provider = tp

    mp = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics"),
                export_interval_millis=10_000,
            )
        ],
    )
    metrics.set_meter_provider(mp)
    _meter_provider = mp

    return tp, mp


def get_tracer(name: str = "open_next_role") -> trace.Tracer:
    return trace.get_tracer(name)


def get_meter(name: str = "open_next_role") -> metrics.Meter:
    return metrics.get_meter(name)


def shutdown_telemetry() -> None:
    if _tracer_provider:
        _tracer_provider.shutdown()
    if _meter_provider:
        _meter_provider.shutdown()
