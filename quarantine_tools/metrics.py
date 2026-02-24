"""Quarantine Metrics Prometheus Exporter

Part of the quarantine_tools package. Exports quarantine statistics in
Prometheus text exposition format. Designed to be called as a Jenkins
post-build step or to push metrics to a Prometheus Pushgateway.

Usage:
    quarantine-metrics --branch main
    quarantine-metrics --output-file /tmp/metrics.prom
    quarantine-metrics --push-gateway http://pushgateway:9091
"""

from __future__ import annotations

import sys
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from os import environ
from pathlib import Path
from typing import NamedTuple

from requests import RequestException
from requests import post as http_post
from quarantine_tools.dashboard import DashboardStats, TestScanner
from simple_logger.logger import get_logger
from quarantine_tools.reportportal_client import (
    REPORTPORTAL_PROJECT_ENV,
    REPORTPORTAL_TOKEN_ENV,
    REPORTPORTAL_URL_ENV,
)

LOGGER = get_logger(name=__name__)


class PrometheusMetric(NamedTuple):
    """A single Prometheus metric with its metadata.

    Attributes:
        name: Metric name (e.g., "cnv_tests_total").
        labels: Dict of label key-value pairs (e.g., {"branch": "main", "team": "virt"}).
        value: Numeric metric value.
        help_text: Description of the metric for the HELP comment.
        metric_type: Prometheus metric type (e.g., "gauge", "counter").

    """

    name: str
    labels: dict[str, str]
    value: float
    help_text: str
    metric_type: str


def _escape_label_value(value: str) -> str:
    """Escape special characters in a Prometheus label value.

    Prometheus label values must have backslashes, double quotes, and newlines escaped.

    Args:
        value: The raw label value string.

    Returns:
        The escaped label value safe for Prometheus exposition format.

    """
    escaped = value.replace("\\", "\\\\")
    escaped = escaped.replace('"', '\\"')
    escaped = escaped.replace("\n", "\\n")
    return escaped


def collect_metrics(repo_path: Path, branch: str, include_flaky: bool = False) -> list[PrometheusMetric]:
    """Collect quarantine metrics from the test repository.

    Scans the test directory using TestScanner and produces per-team Prometheus
    metrics including total tests, quarantined tests, health percentage, and
    average quarantine age.

    Args:
        repo_path: Path to the local repository root.
        branch: Branch name to use as a Prometheus label.
        include_flaky: If True, attempt to query ReportPortal for flaky test
            candidate counts. Requires REPORTPORTAL_URL, REPORTPORTAL_TOKEN,
            and REPORTPORTAL_PROJECT environment variables.

    Returns:
        List of PrometheusMetric objects ready for formatting.

    Raises:
        SystemExit: If the tests/ directory does not exist under repo_path.

    """
    tests_dir = repo_path / "tests"
    if not tests_dir.is_dir():
        LOGGER.error("Tests directory not found: %s", tests_dir)
        sys.exit(1)

    scanner = TestScanner(tests_dir=tests_dir)
    stats = scanner.scan_all_tests()

    metrics: list[PrometheusMetric] = []

    # Per-team metrics from category breakdown
    for team, counts in sorted(stats.category_breakdown.items()):
        total = counts["total"]
        if total == 0:
            continue
        quarantined = counts["quarantined"]
        health_percent = (total - quarantined) / total * 100
        labels = {"branch": branch, "team": team}

        metrics.append(PrometheusMetric(
            name="cnv_tests_total",
            labels=labels,
            value=float(total),
            help_text="Total number of tests",
            metric_type="gauge",
        ))
        metrics.append(PrometheusMetric(
            name="cnv_tests_quarantined",
            labels=labels,
            value=float(quarantined),
            help_text="Number of quarantined tests",
            metric_type="gauge",
        ))
        metrics.append(PrometheusMetric(
            name="cnv_tests_health_percent",
            labels=labels,
            value=round(health_percent, 1),
            help_text="Test health percentage (non-quarantined / total * 100)",
            metric_type="gauge",
        ))

    # Per-team average quarantine age from category breakdown
    for team, counts in sorted(stats.category_breakdown.items()):
        team_avg_age = counts.get("avg_quarantine_age_days", 0.0)
        metrics.append(PrometheusMetric(
            name="cnv_quarantine_avg_age_days",
            labels={"branch": branch, "team": team},
            value=round(float(team_avg_age), 1),
            help_text="Average age of quarantined tests in days",
            metric_type="gauge",
        ))

    # Flaky candidate metrics (optional, requires ReportPortal)
    if include_flaky:
        flaky_metrics = _collect_flaky_metrics(branch=branch, stats=stats)
        metrics.extend(flaky_metrics)

    return metrics


def _collect_flaky_metrics(branch: str, stats: DashboardStats) -> list[PrometheusMetric]:
    """Collect flaky test candidate metrics from ReportPortal.

    Queries ReportPortal for tests with a high flaky rate that are not yet
    quarantined. Requires environment variables REPORTPORTAL_URL,
    REPORTPORTAL_TOKEN, and REPORTPORTAL_PROJECT.

    Args:
        branch: Branch name to use as a Prometheus label.
        stats: DashboardStats with current quarantine information.

    Returns:
        List of PrometheusMetric for flaky candidate counts. Returns empty
        list if ReportPortal is not configured.

    """
    reportportal_url = environ.get(REPORTPORTAL_URL_ENV)
    reportportal_token = environ.get(REPORTPORTAL_TOKEN_ENV)
    reportportal_project = environ.get(REPORTPORTAL_PROJECT_ENV)

    if not all([reportportal_url, reportportal_token, reportportal_project]):
        LOGGER.warning(
            "ReportPortal not configured. Set %s, %s, and %s environment variables to enable flaky metrics.",
            REPORTPORTAL_URL_ENV,
            REPORTPORTAL_TOKEN_ENV,
            REPORTPORTAL_PROJECT_ENV,
        )
        return []

    # Placeholder: ReportPortal integration would query for flaky tests here.
    # For now, emit zero-value metrics to register the metric name.
    metrics: list[PrometheusMetric] = []
    for team in sorted(stats.category_breakdown.keys()):
        metrics.append(PrometheusMetric(
            name="cnv_tests_flaky_candidates",
            labels={"branch": branch, "team": team},
            value=0.0,
            help_text="Number of flaky tests not yet quarantined (requires ReportPortal)",
            metric_type="gauge",
        ))

    return metrics


def format_prometheus(metrics: list[PrometheusMetric]) -> str:
    """Format metrics in Prometheus text exposition format.

    Groups metrics by name and emits HELP and TYPE comments once per metric
    name, followed by all data points for that metric.

    Args:
        metrics: List of PrometheusMetric objects to format.

    Returns:
        Prometheus exposition format string, including HELP/TYPE comments
        and metric lines with labels.

    """
    if not metrics:
        return ""

    # Group metrics by name to emit HELP/TYPE once per metric
    grouped: dict[str, list[PrometheusMetric]] = {}
    for metric in metrics:
        grouped.setdefault(metric.name, []).append(metric)

    output_lines: list[str] = []

    for metric_name, metric_group in grouped.items():
        first = metric_group[0]
        output_lines.append(f"# HELP {metric_name} {first.help_text}")
        output_lines.append(f"# TYPE {metric_name} {first.metric_type}")

        for metric in metric_group:
            label_pairs = ",".join(
                f'{key}="{_escape_label_value(value=value)}"'
                for key, value in sorted(metric.labels.items())
            )
            # Format value: use integer representation when value is a whole number
            formatted_value = str(int(metric.value)) if metric.value == int(metric.value) else str(metric.value)
            output_lines.append(f"{metric_name}{{{label_pairs}}} {formatted_value}")

        output_lines.append("")  # Blank line between metric groups

    return "\n".join(output_lines)


def push_to_gateway(gateway_url: str, job_name: str, metrics_text: str) -> None:
    """Push metrics to a Prometheus Pushgateway.

    Sends a POST request with the metrics payload to the Pushgateway's
    metrics endpoint. Logs errors but does not raise exceptions on failure.

    Args:
        gateway_url: Base URL of the Pushgateway (e.g., "http://pushgateway:9091").
        job_name: Job name label for the Pushgateway grouping key.
        metrics_text: Pre-formatted Prometheus exposition text to push.

    """
    push_url = f"{gateway_url.rstrip('/')}/metrics/job/{job_name}"
    LOGGER.info("Pushing metrics to Pushgateway: %s", push_url)

    try:
        response = http_post(
            url=push_url,
            data=metrics_text.encode("utf-8"),
            headers={"Content-Type": "text/plain; version=0.0.4; charset=utf-8"},
            timeout=30,
        )
        response.raise_for_status()
        LOGGER.info("Successfully pushed metrics to Pushgateway (status %d)", response.status_code)

    except RequestException as error:
        LOGGER.error("Failed to push metrics to Pushgateway: %s", error)


def _parse_args() -> Namespace:
    """Parse command-line arguments for the metrics exporter.

    Returns:
        Parsed argument namespace with branch, repo_path, output_file,
        push_gateway, job_name, and include_flaky fields.

    """
    parser = ArgumentParser(
        description="Export quarantine metrics in Prometheus text format.",
        formatter_class=RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  quarantine-metrics --branch main\n"
            "  quarantine-metrics --output-file /tmp/metrics.prom\n"
            "  quarantine-metrics --push-gateway http://pushgateway:9091\n"
        ),
    )

    parser.add_argument(
        "--branch",
        type=str,
        default="main",
        help="Branch name to use as Prometheus label (default: main)",
    )
    parser.add_argument(
        "--repo-path",
        type=str,
        default=None,
        help="Path to local repo (default: current directory)",
    )
    parser.add_argument(
        "--output-file",
        type=str,
        default=None,
        help="Write metrics to file instead of stdout",
    )
    parser.add_argument(
        "--push-gateway",
        type=str,
        default=None,
        help="URL to Prometheus Pushgateway to push metrics",
    )
    parser.add_argument(
        "--job-name",
        type=str,
        default="cnv_quarantine_stats",
        help="Job name for pushgateway grouping key (default: cnv_quarantine_stats)",
    )
    parser.add_argument(
        "--include-flaky",
        action="store_true",
        default=False,
        help="Include flaky test candidate metrics (requires ReportPortal env vars)",
    )

    return parser.parse_args()


def main() -> None:
    """Main entry point for the Prometheus metrics exporter."""
    args = _parse_args()

    repo_path = Path(args.repo_path) if args.repo_path else Path.cwd()

    LOGGER.info("Collecting quarantine metrics for branch '%s' from %s", args.branch, repo_path)

    metrics = collect_metrics(
        repo_path=repo_path,
        branch=args.branch,
        include_flaky=args.include_flaky,
    )

    metrics_text = format_prometheus(metrics=metrics)

    if args.output_file:
        output_path = Path(args.output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(data=metrics_text, encoding="utf-8")
        LOGGER.info("Metrics written to %s", output_path)
    else:
        print(metrics_text)

    if args.push_gateway:
        push_to_gateway(
            gateway_url=args.push_gateway,
            job_name=args.job_name,
            metrics_text=metrics_text,
        )


if __name__ == "__main__":
    main()
