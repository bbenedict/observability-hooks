"""OpenTelemetry metrics setup for project-level observability.

Metrics → Prometheus (OTLP/HTTP localhost:4318/v1/metrics, via the collector).
Set SERVICE_NAME to your project's name.
"""
from __future__ import annotations

from typing import Optional

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import Counter, Histogram, MeterProvider
from opentelemetry.sdk.metrics.export import AggregationTemporality, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

OTLP_ENDPOINT = "http://localhost:4318"
SERVICE_NAME = "myproject"  # change to your project's name

_meter_provider: Optional[MeterProvider] = None


def setup_telemetry(
    endpoint: str = OTLP_ENDPOINT,
    service_name: str = SERVICE_NAME,
) -> MeterProvider:
    global _meter_provider

    # Fixed instance id so every short-lived run adds to the same metric series.
    resource = Resource.create({"service.name": service_name, "service.instance.id": f"{service_name}_emit"})

    mp = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                # Delta temporality: each run is a new process; the collector's
                # deltatocumulative processor keeps the running totals.
                OTLPMetricExporter(
                    endpoint=f"{endpoint}/v1/metrics",
                    preferred_temporality={
                        Counter: AggregationTemporality.DELTA,
                        Histogram: AggregationTemporality.DELTA,
                    },
                ),
                export_interval_millis=10_000,
            )
        ],
    )
    metrics.set_meter_provider(mp)
    _meter_provider = mp
    return mp


def get_meter(name: str = SERVICE_NAME) -> metrics.Meter:
    return metrics.get_meter(name)


def shutdown_telemetry() -> None:
    if _meter_provider:
        _meter_provider.shutdown()
