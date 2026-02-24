"""Flaky Test Analyzer

Part of the quarantine_tools package. Queries ReportPortal for flaky tests
and produces reports in table, JSON, or HTML format. Cross-references against
currently quarantined tests to identify quarantine candidates and
de-quarantine candidates.

Health check mode (--health-check) cross-references quarantined tests
against ReportPortal pass history and Jira ticket status to identify
tests ready for de-quarantine.

Usage:
    quarantine-analyzer --threshold 3 --days 7 --branch main
    quarantine-analyzer --output json > flaky_report.json
    quarantine-analyzer --check-quarantined
    quarantine-analyzer --health-check --days 14
"""

from __future__ import annotations

from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from html import escape as html_escape
from json import dumps as json_dumps
from os import environ
from pathlib import Path
from typing import NamedTuple

from quarantine_tools.dashboard import DashboardStats, TestScanner
from simple_logger.logger import get_logger
from quarantine_tools.reportportal_client import FlakyTestInfo, ReportPortalClient

LOGGER = get_logger(name=__name__)

# Team/category mapping based on first directory under tests/
TEAM_MARKERS: dict[str, list[str]] = {
    "chaos": ["chaos", "deprecated_api"],
    "virt": ["virt", "deprecated_api"],
    "network": ["network", "deprecated_api"],
    "storage": ["storage", "deprecated_api"],
    "iuo": ["install_upgrade_operators", "deprecated_api"],
    "observability": ["observability", "deprecated_api"],
    "infrastructure": ["infrastructure", "deprecated_api"],
    "data_protection": ["data_protection", "deprecated_api"],
}

# Reverse mapping: directory name -> team name
DIRECTORY_TO_TEAM: dict[str, str] = {}
for team_name, directories in TEAM_MARKERS.items():
    for directory in directories:
        # First match wins; deprecated_api appears in multiple teams, skip duplicates
        if directory not in DIRECTORY_TO_TEAM:
            DIRECTORY_TO_TEAM[directory] = team_name

# Maximum test name length for table display
MAX_TEST_NAME_LENGTH: int = 60

# Exit codes
EXIT_SUCCESS: int = 0
EXIT_ERROR: int = 1


class FlakySummary(NamedTuple):
    """Summary of flaky test analysis results.

    Attributes:
        total_flaky: Total number of flaky tests found.
        by_team: Dictionary mapping team name to list of flaky test info.
        trends: Dictionary mapping test name to trend direction.

    """

    total_flaky: int
    by_team: dict[str, list[FlakyTestInfo]]
    trends: dict[str, str]


class CrossReferenceResult(NamedTuple):
    """Result of cross-referencing flaky tests against quarantined tests.

    Attributes:
        quarantine_candidates: Flaky tests not currently quarantined.
        dequarantine_candidates: Quarantined tests now passing consistently.

    """

    quarantine_candidates: list[FlakyTestInfo]
    dequarantine_candidates: list[str]


class DeQuarantineCandidate(NamedTuple):
    """A quarantined test identified as ready for de-quarantine.

    Attributes:
        test_name: The test function name.
        file_path: Path to the test file relative to the repository root.
        jira_ticket: Associated Jira ticket ID (e.g., "CNV-12345").
        team: Team/category owning this test.
        reason: Why this test is flagged: "passing_consistently",
            "jira_resolved", or "both".
        consecutive_pass_count: Number of consecutive PASSED results from
            ReportPortal. Zero when ReportPortal data is unavailable.
        jira_resolved: Whether the linked Jira ticket is resolved/closed.

    """

    test_name: str
    file_path: str
    jira_ticket: str
    team: str
    reason: str
    consecutive_pass_count: int
    jira_resolved: bool


def determine_team_from_test_name(test_name: str) -> str:
    """Determine the team/category for a test based on its name or path.

    Uses the first directory component under tests/ to map to a team.
    Falls back to 'unknown' if no mapping is found.

    Args:
        test_name: The fully qualified test name from ReportPortal.

    Returns:
        Team name string (e.g., 'virt', 'network', 'storage').

    """
    normalized = test_name.replace(".", "/").replace("::", "/")
    parts = normalized.split("/")

    # Look for a known directory name in the path components
    for part in parts:
        if part in DIRECTORY_TO_TEAM:
            return DIRECTORY_TO_TEAM[part]

    return "unknown"


def calculate_trend(history_first_half: int, history_second_half: int) -> str:
    """Calculate the trend direction based on failure counts in two time windows.

    Compares the first half of the time window to the second half.
    If the second half has fewer failures, the test is improving.

    Args:
        history_first_half: Number of failures in the first half of the time window.
        history_second_half: Number of failures in the second half of the time window.

    Returns:
        One of 'improving', 'worsening', or 'stable'.

    """
    if history_second_half < history_first_half:
        return "improving"
    elif history_second_half > history_first_half:
        return "worsening"
    return "stable"


def analyze_flaky_tests(
    *,
    client: ReportPortalClient,
    threshold: int,
    days: int,
    branch: str | None,
    repo_path: Path,
) -> FlakySummary:
    """Query ReportPortal for flaky tests and analyze results.

    Fetches flaky tests from ReportPortal, groups them by team/category,
    and calculates trend direction by comparing the first half of the time
    window against the second half.

    Args:
        client: Configured ReportPortal client instance.
        threshold: Minimum number of failures to flag a test as flaky.
        days: Number of days to look back for analysis.
        branch: Optional branch name to filter results.
        repo_path: Path to the local repository root.

    Returns:
        FlakySummary containing grouped flaky tests and their trends.

    """
    LOGGER.info(
        "Querying ReportPortal for flaky tests (threshold=%d, days=%d, branch=%s)",
        threshold,
        days,
        branch or "all",
    )

    flaky_tests = client.get_flaky_tests(threshold=threshold, days=days, branch=branch)
    LOGGER.info("Found %d flaky tests", len(flaky_tests))

    by_team: dict[str, list[FlakyTestInfo]] = {}
    trends: dict[str, str] = {}

    for flaky_test in flaky_tests:
        team = determine_team_from_test_name(test_name=flaky_test.test_name)

        if team not in by_team:
            by_team[team] = []
        by_team[team].append(flaky_test)

        # Calculate trend by comparing first half vs second half of time window
        history = client.get_test_history(test_name=flaky_test.test_name, days=days)
        midpoint = len(history) // 2
        first_half_failures = sum(1 for outcome in history[:midpoint] if outcome.status == "FAILED")
        second_half_failures = sum(1 for outcome in history[midpoint:] if outcome.status == "FAILED")

        trends[flaky_test.test_name] = calculate_trend(
            history_first_half=first_half_failures,
            history_second_half=second_half_failures,
        )

    return FlakySummary(
        total_flaky=len(flaky_tests),
        by_team=by_team,
        trends=trends,
    )


def _normalize_test_name(test_name: str) -> str:
    """Extract the short test function name for comparison.

    Strips module path prefixes, keeping only the final test function name
    after the last '::' separator.

    Args:
        test_name: Fully qualified or short test name.

    Returns:
        The short test function name.

    """
    return test_name.split("::")[-1] if "::" in test_name else test_name


def cross_reference_quarantined(
    *,
    flaky_summary: FlakySummary,
    repo_path: Path,
    client: ReportPortalClient,
    days: int,
) -> CrossReferenceResult:
    """Cross-reference flaky tests against currently quarantined tests.

    Scans the local repository for quarantined tests using TestScanner,
    then compares against ReportPortal flaky test data to find:
    - Flaky tests not yet quarantined (quarantine candidates)
    - Quarantined tests now passing consistently (de-quarantine candidates)

    Args:
        flaky_summary: Summary of flaky tests from ReportPortal.
        repo_path: Path to the local repository root.
        client: Configured ReportPortal client instance.
        days: Number of days for failure rate lookback.

    Returns:
        CrossReferenceResult with quarantine and de-quarantine candidates.

    """
    tests_dir = repo_path / "tests"
    if not tests_dir.exists():
        LOGGER.error("Tests directory not found: %s", tests_dir)
        return CrossReferenceResult(quarantine_candidates=[], dequarantine_candidates=[])

    LOGGER.info("Scanning quarantined tests in %s", tests_dir)
    scanner = TestScanner(tests_dir=tests_dir)
    dashboard_stats: DashboardStats = scanner.scan_all_tests()

    quarantined_names: set[str] = {
        _normalize_test_name(test_name=test_info.name) for test_info in dashboard_stats.quarantined_list
    }
    LOGGER.info("Found %d currently quarantined tests", len(quarantined_names))

    # Collect all flaky test names (normalized for comparison)
    all_flaky_names: set[str] = set()
    all_flaky_by_name: dict[str, FlakyTestInfo] = {}
    for team_tests in flaky_summary.by_team.values():
        for flaky_test in team_tests:
            short_name = _normalize_test_name(test_name=flaky_test.test_name)
            all_flaky_names.add(short_name)
            all_flaky_by_name[short_name] = flaky_test

    # Find flaky tests NOT yet quarantined
    quarantine_candidates: list[FlakyTestInfo] = []
    for test_name, flaky_info in all_flaky_by_name.items():
        if test_name not in quarantined_names:
            quarantine_candidates.append(flaky_info)

    # Find quarantined tests now passing consistently
    dequarantine_candidates: list[str] = []
    for quarantined_test in dashboard_stats.quarantined_list:
        failure_rate = client.get_test_failure_rate(test_name=quarantined_test.name, days=days)
        if failure_rate == 0.0:
            dequarantine_candidates.append(quarantined_test.name)
            LOGGER.info("De-quarantine candidate: %s (0%% failure rate)", quarantined_test.name)

    LOGGER.info(
        "Cross-reference complete: %d quarantine candidates, %d de-quarantine candidates",
        len(quarantine_candidates),
        len(dequarantine_candidates),
    )

    return CrossReferenceResult(
        quarantine_candidates=quarantine_candidates,
        dequarantine_candidates=dequarantine_candidates,
    )


def _count_consecutive_passes(
    *,
    client: ReportPortalClient,
    test_name: str,
    days: int,
) -> int:
    """Count the number of consecutive PASSED results from most recent backwards.

    Queries ReportPortal for test history and counts how many of the most
    recent results are PASSED without interruption.

    Args:
        client: Configured ReportPortal client instance.
        test_name: Fully qualified test name to query.
        days: Number of days of history to check.

    Returns:
        Number of consecutive passes from the most recent result. Returns 0
        if there are no results or the most recent result is not PASSED.

    """
    history = client.get_test_history(test_name=test_name, days=days)
    consecutive_passes = 0
    for outcome in history:
        if outcome.status == "PASSED":
            consecutive_passes += 1
        else:
            break
    return consecutive_passes


def _determine_dequarantine_reason(
    *,
    jira_is_resolved: bool,
    passes_consistently: bool,
) -> str | None:
    """Determine the de-quarantine reason based on Jira and ReportPortal signals.

    Args:
        jira_is_resolved: Whether the linked Jira ticket is resolved/closed.
        passes_consistently: Whether the test passes consistently in ReportPortal.

    Returns:
        Reason string ("both", "jira_resolved", "passing_consistently") or
        None if the test is not a de-quarantine candidate.

    """
    if jira_is_resolved and passes_consistently:
        return "both"
    if jira_is_resolved:
        return "jira_resolved"
    if passes_consistently:
        return "passing_consistently"
    return None


def check_quarantine_health(
    repo_path: Path,
    consecutive_passes: int = 5,
    days: int = 14,
) -> list[DeQuarantineCandidate]:
    """Check health of quarantined tests to identify de-quarantine candidates.

    Cross-references quarantined tests against:
    1. ReportPortal nightly regression results -- tests passing consistently.
    2. Jira ticket status -- resolved/closed tickets.

    Args:
        repo_path: Path to the repository root.
        consecutive_passes: Number of consecutive passes required to flag
            for de-quarantine.
        days: Number of days of history to check.

    Returns:
        List of de-quarantine candidates with reasons.

    """
    tests_dir = repo_path / "tests"
    if not tests_dir.exists():
        LOGGER.error("Tests directory not found: %s", tests_dir)
        return []

    LOGGER.info("Scanning quarantined tests in %s", tests_dir)
    scanner = TestScanner(tests_dir=tests_dir)
    dashboard_stats: DashboardStats = scanner.scan_all_tests()

    quarantined_tests = [
        test_info for test_info in dashboard_stats.quarantined_list if test_info.is_quarantined
    ]
    LOGGER.info("Found %d quarantined tests for health check", len(quarantined_tests))

    # Try importing Jira checker -- may not be configured
    jira_checker = None
    try:
        from quarantine_tools.quarantine_jira import check_quarantine_ticket_resolved

        jira_checker = check_quarantine_ticket_resolved
        LOGGER.info("Jira integration available for health check")
    except (ImportError, Exception) as import_error:
        LOGGER.warning("Jira integration unavailable, skipping ticket status checks: %s", import_error)

    # Try creating ReportPortal client -- may not be configured
    rp_client = create_reportportal_client()
    if rp_client:
        LOGGER.info("ReportPortal integration available for health check")
    else:
        LOGGER.warning("ReportPortal not configured, skipping pass-rate checks")

    candidates: list[DeQuarantineCandidate] = []

    for test_info in quarantined_tests:
        jira_ticket = test_info.jira_ticket
        jira_is_resolved = False
        pass_count = 0

        # Check Jira ticket status
        if jira_ticket and jira_checker is not None:
            try:
                jira_is_resolved = jira_checker(ticket_id=jira_ticket)
                LOGGER.info("Jira %s resolved: %s", jira_ticket, jira_is_resolved)
            except Exception as jira_error:
                LOGGER.warning("Failed to check Jira ticket %s: %s", jira_ticket, jira_error)

        # Check ReportPortal pass history
        if rp_client is not None:
            try:
                pass_count = _count_consecutive_passes(
                    client=rp_client,
                    test_name=test_info.name,
                    days=days,
                )
                LOGGER.info(
                    "Test %s has %d consecutive passes",
                    test_info.name,
                    pass_count,
                )
            except Exception as rp_error:
                LOGGER.warning(
                    "Failed to get ReportPortal history for %s: %s",
                    test_info.name,
                    rp_error,
                )

        passes_consistently = pass_count >= consecutive_passes
        reason = _determine_dequarantine_reason(
            jira_is_resolved=jira_is_resolved,
            passes_consistently=passes_consistently,
        )

        if reason is not None:
            candidate = DeQuarantineCandidate(
                test_name=test_info.name,
                file_path=str(test_info.file_path),
                jira_ticket=jira_ticket,
                team=test_info.category,
                reason=reason,
                consecutive_pass_count=pass_count,
                jira_resolved=jira_is_resolved,
            )
            candidates.append(candidate)
            LOGGER.info("De-quarantine candidate: %s (reason: %s)", test_info.name, reason)

    if rp_client is not None:
        rp_client.close()

    LOGGER.info("Health check complete: %d de-quarantine candidates found", len(candidates))
    return candidates


def format_health_check_table(*, candidates: list[DeQuarantineCandidate]) -> str:
    """Format health check results as an ASCII table.

    Args:
        candidates: List of de-quarantine candidates.

    Returns:
        Formatted ASCII table string.

    """
    if not candidates:
        return "No de-quarantine candidates found."

    header = (
        f"{'Test Name':<{MAX_TEST_NAME_LENGTH}} "
        f"{'Team':<15} "
        f"{'Jira':<12} "
        f"{'Passes':>8} "
        f"{'Jira OK':>8} "
        f"{'Reason':<25}"
    )
    separator = "-" * len(header)
    lines: list[str] = [
        f"Quarantine Health Check ({len(candidates)} de-quarantine candidates)",
        "",
        header,
        separator,
    ]

    for candidate in sorted(candidates, key=lambda dc: dc.reason):
        display_name = truncate_name(name=candidate.test_name)
        jira_resolved_display = "Yes" if candidate.jira_resolved else "No"
        lines.append(
            f"{display_name:<{MAX_TEST_NAME_LENGTH}} "
            f"{candidate.team:<15} "
            f"{candidate.jira_ticket:<12} "
            f"{candidate.consecutive_pass_count:>8} "
            f"{jira_resolved_display:>8} "
            f"{candidate.reason:<25}"
        )

    lines.append(separator)
    lines.append(f"Total: {len(candidates)} candidates ready for de-quarantine")

    return "\n".join(lines)


def format_health_check_json(*, candidates: list[DeQuarantineCandidate]) -> str:
    """Format health check results as JSON.

    Args:
        candidates: List of de-quarantine candidates.

    Returns:
        JSON formatted string.

    """
    output: dict = {
        "total_candidates": len(candidates),
        "candidates": [
            {
                "test_name": candidate.test_name,
                "file_path": candidate.file_path,
                "jira_ticket": candidate.jira_ticket,
                "team": candidate.team,
                "reason": candidate.reason,
                "consecutive_pass_count": candidate.consecutive_pass_count,
                "jira_resolved": candidate.jira_resolved,
            }
            for candidate in candidates
        ],
    }
    return json_dumps(obj=output, indent=2)


def format_health_check_html(*, candidates: list[DeQuarantineCandidate]) -> str:
    """Format health check results as an HTML section.

    Args:
        candidates: List of de-quarantine candidates.

    Returns:
        HTML formatted string.

    """
    html_parts: list[str] = [
        "<div class='health-check-report'>",
        f"<h2>Quarantine Health Check ({len(candidates)} candidates)</h2>",
    ]

    if not candidates:
        html_parts.append("<p>No de-quarantine candidates found.</p>")
        html_parts.append("</div>")
        return "\n".join(html_parts)

    html_parts.extend([
        "<table>",
        "<thead><tr>",
        "<th>Test Name</th><th>Team</th><th>Jira</th>"
        "<th>Consecutive Passes</th><th>Jira Resolved</th><th>Reason</th>",
        "</tr></thead>",
        "<tbody>",
    ])

    for candidate in sorted(candidates, key=lambda dc: dc.reason):
        escaped_name = html_escape(s=candidate.test_name)
        escaped_team = html_escape(s=candidate.team)
        reason_class = f"reason-{candidate.reason.replace('_', '-')}"
        jira_cell = ""
        if candidate.jira_ticket:
            jira_url = f"https://issues.redhat.com/browse/{candidate.jira_ticket}"
            jira_cell = f'<a href="{jira_url}" target="_blank">{html_escape(s=candidate.jira_ticket)}</a>'
        jira_resolved_display = "Yes" if candidate.jira_resolved else "No"

        html_parts.append(
            f"<tr class='{reason_class}'>"
            f"<td title='{escaped_name}'>{html_escape(s=truncate_name(name=candidate.test_name))}</td>"
            f"<td>{escaped_team}</td>"
            f"<td>{jira_cell}</td>"
            f"<td>{candidate.consecutive_pass_count}</td>"
            f"<td>{jira_resolved_display}</td>"
            f"<td>{html_escape(s=candidate.reason)}</td>"
            f"</tr>"
        )

    html_parts.extend([
        "</tbody></table>",
        "</div>",
    ])

    return "\n".join(html_parts)


def _format_health_check_output(
    *,
    output_format: str,
    candidates: list[DeQuarantineCandidate],
) -> str:
    """Route to the appropriate health check output formatter.

    Args:
        output_format: One of 'table', 'json', or 'html'.
        candidates: List of de-quarantine candidates.

    Returns:
        Formatted output string.

    """
    if output_format == "json":
        return format_health_check_json(candidates=candidates)
    if output_format == "html":
        return format_health_check_html(candidates=candidates)
    return format_health_check_table(candidates=candidates)


def truncate_name(name: str, max_length: int = MAX_TEST_NAME_LENGTH) -> str:
    """Truncate a test name to fit within a maximum length.

    If the name exceeds max_length, truncates and appends '...' suffix.

    Args:
        name: The test name to potentially truncate.
        max_length: Maximum allowed length for the display name.

    Returns:
        The original name if it fits, or a truncated version with '...' suffix.

    """
    if len(name) <= max_length:
        return name
    return name[: max_length - 3] + "..."


def format_table_output(*, flaky_summary: FlakySummary) -> str:
    """Format flaky test analysis results as an ASCII table.

    Produces a human-readable table showing test name (truncated), team,
    failure count, failure rate, and trend direction.

    Args:
        flaky_summary: Summary of flaky test analysis.

    Returns:
        Formatted ASCII table string.

    """
    if flaky_summary.total_flaky == 0:
        return "No flaky tests found."

    header = f"{'Test Name':<{MAX_TEST_NAME_LENGTH}} {'Team':<15} {'Failures':>10} {'Rate':>8} {'Trend':<12}"
    separator = "-" * len(header)
    lines: list[str] = [
        f"Flaky Test Report ({flaky_summary.total_flaky} tests found)",
        "",
        header,
        separator,
    ]

    for team_name in sorted(flaky_summary.by_team.keys()):
        team_tests = flaky_summary.by_team[team_name]
        for flaky_test in sorted(team_tests, key=lambda ft: ft.failure_count, reverse=True):
            display_name = truncate_name(name=flaky_test.test_name)
            trend = flaky_summary.trends.get(flaky_test.test_name, "stable")
            trend_indicator = _trend_symbol(trend=trend)

            lines.append(
                f"{display_name:<{MAX_TEST_NAME_LENGTH}} "
                f"{team_name:<15} "
                f"{flaky_test.failure_count:>10} "
                f"{flaky_test.failure_rate:>7.1%} "
                f"{trend_indicator:<12}"
            )

    lines.append(separator)
    lines.append(f"Total: {flaky_summary.total_flaky} flaky tests across {len(flaky_summary.by_team)} teams")

    return "\n".join(lines)


def format_cross_reference_table(*, cross_reference: CrossReferenceResult) -> str:
    """Format cross-reference results as an ASCII table.

    Shows quarantine candidates and de-quarantine candidates in
    separate sections.

    Args:
        cross_reference: Cross-reference analysis results.

    Returns:
        Formatted ASCII table string.

    """
    lines: list[str] = ["Cross-Reference Report", "=" * 60, ""]

    # Quarantine candidates
    lines.append(f"Quarantine Candidates ({len(cross_reference.quarantine_candidates)} tests):")
    lines.append("-" * 60)
    if cross_reference.quarantine_candidates:
        for candidate in cross_reference.quarantine_candidates:
            display_name = truncate_name(name=candidate.test_name)
            lines.append(f"  {display_name}  (failures: {candidate.failure_count}, rate: {candidate.failure_rate:.1%})")
    else:
        lines.append("  None - all flaky tests are already quarantined.")

    lines.append("")

    # De-quarantine candidates
    lines.append(f"De-quarantine Candidates ({len(cross_reference.dequarantine_candidates)} tests):")
    lines.append("-" * 60)
    if cross_reference.dequarantine_candidates:
        for test_name in cross_reference.dequarantine_candidates:
            display_name = truncate_name(name=test_name)
            lines.append(f"  {display_name}  (now passing consistently)")
    else:
        lines.append("  None - all quarantined tests still have failures.")

    return "\n".join(lines)


def _trend_symbol(*, trend: str) -> str:
    """Convert a trend string to a display symbol.

    Args:
        trend: One of 'improving', 'worsening', or 'stable'.

    Returns:
        Human-readable trend indicator string.

    """
    symbols = {
        "improving": "improving",
        "worsening": "WORSENING",
        "stable": "stable",
    }
    return symbols.get(trend, trend)


def _build_flaky_test_dict(*, flaky_test: FlakyTestInfo, trend: str, team: str) -> dict:
    """Build a serializable dictionary for a single flaky test.

    Args:
        flaky_test: The flaky test info from ReportPortal.
        trend: The calculated trend direction.
        team: The team/category assignment.

    Returns:
        Dictionary with test data suitable for JSON serialization.

    """
    return {
        "test_name": flaky_test.test_name,
        "team": team,
        "failure_count": flaky_test.failure_count,
        "failure_rate": flaky_test.failure_rate,
        "trend": trend,
    }


def format_json_output(
    *,
    flaky_summary: FlakySummary,
    cross_reference: CrossReferenceResult | None = None,
) -> str:
    """Format analysis results as JSON.

    Produces a JSON string containing all flaky test data, optionally
    including cross-reference results.

    Args:
        flaky_summary: Summary of flaky test analysis.
        cross_reference: Optional cross-reference results to include.

    Returns:
        JSON formatted string.

    """
    tests_list: list[dict] = []
    for team_name, team_tests in flaky_summary.by_team.items():
        for flaky_test in team_tests:
            trend = flaky_summary.trends.get(flaky_test.test_name, "stable")
            tests_list.append(
                _build_flaky_test_dict(
                    flaky_test=flaky_test,
                    trend=trend,
                    team=team_name,
                )
            )

    output: dict = {
        "total_flaky": flaky_summary.total_flaky,
        "teams": {team: len(tests) for team, tests in flaky_summary.by_team.items()},
        "tests": tests_list,
    }

    if cross_reference is not None:
        output["cross_reference"] = {
            "quarantine_candidates": [
                {
                    "test_name": candidate.test_name,
                    "failure_count": candidate.failure_count,
                    "failure_rate": candidate.failure_rate,
                }
                for candidate in cross_reference.quarantine_candidates
            ],
            "dequarantine_candidates": cross_reference.dequarantine_candidates,
        }

    return json_dumps(obj=output, indent=2)


def format_html_output(
    *,
    flaky_summary: FlakySummary,
    cross_reference: CrossReferenceResult | None = None,
) -> str:
    """Format analysis results as an HTML section.

    Produces an HTML fragment suitable for embedding in the quarantine
    dashboard.

    Args:
        flaky_summary: Summary of flaky test analysis.
        cross_reference: Optional cross-reference results to include.

    Returns:
        HTML formatted string.

    """
    html_parts: list[str] = [
        "<div class='flaky-report'>",
        f"<h2>Flaky Test Report ({flaky_summary.total_flaky} tests)</h2>",
        "<table>",
        "<thead><tr>",
        "<th>Test Name</th><th>Team</th><th>Failures</th><th>Rate</th><th>Trend</th>",
        "</tr></thead>",
        "<tbody>",
    ]

    for team_name in sorted(flaky_summary.by_team.keys()):
        team_tests = flaky_summary.by_team[team_name]
        for flaky_test in sorted(team_tests, key=lambda ft: ft.failure_count, reverse=True):
            trend = flaky_summary.trends.get(flaky_test.test_name, "stable")
            trend_class = f"trend-{trend}"
            escaped_name = html_escape(s=flaky_test.test_name)
            escaped_team = html_escape(s=team_name)

            html_parts.append(
                f"<tr class='{trend_class}'>"
                f"<td title='{escaped_name}'>{html_escape(s=truncate_name(name=flaky_test.test_name))}</td>"
                f"<td>{escaped_team}</td>"
                f"<td>{flaky_test.failure_count}</td>"
                f"<td>{flaky_test.failure_rate:.1%}</td>"
                f"<td>{html_escape(s=_trend_symbol(trend=trend))}</td>"
                f"</tr>"
            )

    html_parts.append("</tbody></table>")

    if cross_reference is not None:
        html_parts.extend(_build_cross_reference_html(cross_reference=cross_reference))

    html_parts.append("</div>")
    return "\n".join(html_parts)


def _build_cross_reference_html(*, cross_reference: CrossReferenceResult) -> list[str]:
    """Build HTML sections for cross-reference results.

    Args:
        cross_reference: Cross-reference analysis results.

    Returns:
        List of HTML strings for the cross-reference sections.

    """
    html_parts: list[str] = []

    # Quarantine candidates section
    html_parts.append(f"<h3>Quarantine Candidates ({len(cross_reference.quarantine_candidates)})</h3>")
    if cross_reference.quarantine_candidates:
        html_parts.append("<ul>")
        for candidate in cross_reference.quarantine_candidates:
            escaped_name = html_escape(s=candidate.test_name)
            html_parts.append(
                f"<li>{escaped_name} (failures: {candidate.failure_count}, "
                f"rate: {candidate.failure_rate:.1%})</li>"
            )
        html_parts.append("</ul>")
    else:
        html_parts.append("<p>All flaky tests are already quarantined.</p>")

    # De-quarantine candidates section
    html_parts.append(f"<h3>De-quarantine Candidates ({len(cross_reference.dequarantine_candidates)})</h3>")
    if cross_reference.dequarantine_candidates:
        html_parts.append("<ul>")
        for test_name in cross_reference.dequarantine_candidates:
            html_parts.append(f"<li>{html_escape(s=test_name)} (now passing consistently)</li>")
        html_parts.append("</ul>")
    else:
        html_parts.append("<p>All quarantined tests still have failures.</p>")

    return html_parts


def create_reportportal_client(
    *,
    reportportal_url: str | None = None,
    reportportal_token: str | None = None,
    reportportal_project: str | None = None,
) -> ReportPortalClient | None:
    """Create a ReportPortal client from CLI arguments or environment variables.

    CLI arguments take precedence over environment variables.
    Returns None if required configuration is missing.

    Args:
        reportportal_url: Optional URL override (falls back to REPORTPORTAL_URL env var).
        reportportal_token: Optional token override (falls back to REPORTPORTAL_TOKEN env var).
        reportportal_project: Optional project override (falls back to REPORTPORTAL_PROJECT env var).

    Returns:
        Configured ReportPortalClient instance, or None if configuration is incomplete.

    """
    url = reportportal_url or environ.get("REPORTPORTAL_URL")
    token = reportportal_token or environ.get("REPORTPORTAL_TOKEN")
    project = reportportal_project or environ.get("REPORTPORTAL_PROJECT")

    if not url or not token or not project:
        missing: list[str] = []
        if not url:
            missing.append("REPORTPORTAL_URL")
        if not token:
            missing.append("REPORTPORTAL_TOKEN")
        if not project:
            missing.append("REPORTPORTAL_PROJECT")
        LOGGER.warning(
            "ReportPortal not configured. Missing: %s. "
            "Set environment variables or use --reportportal-* flags.",
            ", ".join(missing),
        )
        return None

    return ReportPortalClient(url=url, token=token, project=project)


def parse_args() -> Namespace:
    """Parse command line arguments.

    Returns:
        Parsed arguments namespace.

    """
    parser = ArgumentParser(
        description="Flaky Test Analyzer - Query ReportPortal for flaky tests and produce reports",
        formatter_class=RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Basic flaky test analysis
    quarantine-analyzer --threshold 3 --days 7

    # Filter by branch
    quarantine-analyzer --branch main

    # JSON output
    quarantine-analyzer --output json > flaky_report.json

    # Cross-reference with quarantined tests
    quarantine-analyzer --check-quarantined

    # Use custom ReportPortal settings
    quarantine-analyzer --reportportal-url https://rp.example.com

    # Health check: find de-quarantine candidates
    quarantine-analyzer --health-check --days 14
        """,
    )

    parser.add_argument(
        "--threshold",
        type=int,
        default=3,
        help="Minimum number of failures to flag a test as flaky (default: 3)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to look back for analysis (default: 7)",
    )
    parser.add_argument(
        "--branch",
        type=str,
        default=None,
        help="Filter by branch name (default: all branches)",
    )
    parser.add_argument(
        "--output",
        type=str,
        choices=["table", "json", "html"],
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--check-quarantined",
        action="store_true",
        help="Cross-reference flaky tests against currently quarantined tests",
    )
    parser.add_argument(
        "--health-check",
        action="store_true",
        default=False,
        help="Check quarantine health: find tests ready for de-quarantine",
    )
    parser.add_argument(
        "--repo-path",
        type=str,
        default=".",
        help="Path to local repository for scanning quarantined tests (default: current directory)",
    )
    parser.add_argument(
        "--reportportal-url",
        type=str,
        default=None,
        help="ReportPortal URL (overrides REPORTPORTAL_URL env var)",
    )
    parser.add_argument(
        "--reportportal-token",
        type=str,
        default=None,
        help="ReportPortal API token (overrides REPORTPORTAL_TOKEN env var)",
    )
    parser.add_argument(
        "--reportportal-project",
        type=str,
        default=None,
        help="ReportPortal project name (overrides REPORTPORTAL_PROJECT env var)",
    )

    return parser.parse_args()


def validate_repo_path(repo_path: Path) -> bool:
    """Validate that the repository path exists and contains a tests/ directory.

    Args:
        repo_path: Path to the repository root.

    Returns:
        True if the path is valid and contains tests/, False otherwise.

    """
    if not repo_path.exists():
        LOGGER.error("Repository path does not exist: %s", repo_path)
        return False

    tests_dir = repo_path / "tests"
    if not tests_dir.exists():
        LOGGER.error("No tests/ directory found at: %s", tests_dir)
        return False

    return True


def run_analysis(*, args: Namespace) -> int:
    """Execute the flaky test analysis workflow.

    Coordinates ReportPortal queries, optional cross-referencing,
    and output formatting based on CLI arguments.

    Args:
        args: Parsed command line arguments.

    Returns:
        Exit code: 0 on success, 1 on error.

    """
    repo_path = Path(args.repo_path).resolve()

    # Handle health check mode (independent workflow)
    if args.health_check:
        if not validate_repo_path(repo_path=repo_path):
            return EXIT_ERROR

        candidates = check_quarantine_health(
            repo_path=repo_path,
            days=args.days,
        )
        output = _format_health_check_output(
            output_format=args.output,
            candidates=candidates,
        )
        print(output)
        return EXIT_SUCCESS

    # Create ReportPortal client
    client = create_reportportal_client(
        reportportal_url=args.reportportal_url,
        reportportal_token=args.reportportal_token,
        reportportal_project=args.reportportal_project,
    )

    if client is None:
        if args.check_quarantined:
            LOGGER.error(
                "--check-quarantined requires ReportPortal configuration "
                "(REPORTPORTAL_URL, REPORTPORTAL_TOKEN, REPORTPORTAL_PROJECT)"
            )
            return EXIT_ERROR
        LOGGER.warning("ReportPortal not configured. Skipping analysis.")
        return EXIT_SUCCESS

    # Validate repo path if cross-referencing is requested
    if args.check_quarantined and not validate_repo_path(repo_path=repo_path):
        return EXIT_ERROR

    # Run flaky test analysis
    flaky_summary = analyze_flaky_tests(
        client=client,
        threshold=args.threshold,
        days=args.days,
        branch=args.branch,
        repo_path=repo_path,
    )

    # Run cross-reference if requested
    cross_reference: CrossReferenceResult | None = None
    if args.check_quarantined:
        cross_reference = cross_reference_quarantined(
            flaky_summary=flaky_summary,
            repo_path=repo_path,
            client=client,
            days=args.days,
        )

    # Format and output results
    output = _format_output(
        output_format=args.output,
        flaky_summary=flaky_summary,
        cross_reference=cross_reference,
    )
    print(output)

    return EXIT_SUCCESS


def _format_output(
    *,
    output_format: str,
    flaky_summary: FlakySummary,
    cross_reference: CrossReferenceResult | None,
) -> str:
    """Route to the appropriate output formatter.

    Args:
        output_format: One of 'table', 'json', or 'html'.
        flaky_summary: Summary of flaky test analysis.
        cross_reference: Optional cross-reference results.

    Returns:
        Formatted output string.

    """
    if output_format == "json":
        return format_json_output(
            flaky_summary=flaky_summary,
            cross_reference=cross_reference,
        )

    if output_format == "html":
        return format_html_output(
            flaky_summary=flaky_summary,
            cross_reference=cross_reference,
        )

    # Default: table output
    result = format_table_output(flaky_summary=flaky_summary)
    if cross_reference is not None:
        result += "\n\n" + format_cross_reference_table(cross_reference=cross_reference)
    return result


def main() -> None:
    """Main entry point for the flaky test analyzer CLI."""
    args = parse_args()
    exit_code = run_analysis(args=args)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
