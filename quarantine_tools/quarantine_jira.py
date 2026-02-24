import os
from re import match as re_match
from typing import NamedTuple

from jira import JIRA

from simple_logger.logger import get_logger
from quarantine_tools.exceptions import MissingEnvironmentVariableError

LOGGER = get_logger(name=__name__)

QUARANTINE_LABEL = "quarantined-test"
DEFAULT_JIRA_SERVER = "https://issues.redhat.com"
DEFAULT_JIRA_PROJECT = "CNV"
# Jira custom field for QE Team assignment (Red Hat internal Jira)
JIRA_QE_TEAM_FIELD = "customfield_12310243"


class QuarantineTicket(NamedTuple):
    """Represents a Jira ticket created for a quarantined test."""

    ticket_id: str
    title: str
    status: str
    test_name: str
    team: str
    created_date: str
    resolved: bool


def get_jira_client() -> JIRA:
    """Create and return a JIRA client instance using environment variables.

    Uses JIRA_TOKEN or PYTEST_JIRA_TOKEN for authentication and
    JIRA_SERVER for the server URL (defaults to https://issues.redhat.com).

    Returns:
        JIRA: Authenticated Jira client instance.

    Raises:
        MissingEnvironmentVariableError: If no Jira token is found in environment variables.
    """
    token = os.environ.get("JIRA_TOKEN") or os.environ.get("PYTEST_JIRA_TOKEN")
    if not token:
        raise MissingEnvironmentVariableError("Please set JIRA_TOKEN or PYTEST_JIRA_TOKEN environment variable")

    server = os.environ.get("JIRA_SERVER", DEFAULT_JIRA_SERVER)
    LOGGER.info(f"Connecting to Jira server: {server}")

    return JIRA(server=server, token_auth=token)


def create_quarantine_ticket(
    test_name: str,
    failure_context: str,
    team: str,
    branch: str,
    gating_pipeline: bool = False,
) -> str:
    """Create a Jira ticket for quarantined test stabilization.

    Args:
        test_name: Fully qualified test name (e.g., "tests.network.test_connectivity.test_ping").
        failure_context: Failure logs and environment details describing the test failure.
        team: Team responsible for the test (used for assignment/tracking).
        branch: Git branch where the failure was observed.
        gating_pipeline: Whether the test is in a gating pipeline (affects priority).

    Returns:
        The created ticket ID (e.g., "CNV-12345").
    """
    project = os.environ.get("JIRA_PROJECT", DEFAULT_JIRA_PROJECT)
    priority = "Critical" if gating_pipeline else "Major"
    title = f"[stabilization] {test_name}"

    description = (
        f"h2. Quarantined Test Stabilization\n\n"
        f"*Test:* {{{{{{test_name}}}}}}\n"
        f"*Team:* {team}\n"
        f"*Branch:* {branch}\n"
        f"*Gating Pipeline:* {'Yes' if gating_pipeline else 'No'}\n\n"
        f"h2. Failure Context\n\n"
        f"{{noformat}}\n{failure_context}\n{{noformat}}\n"
    )

    client = get_jira_client()

    issue_fields = {
        "project": {"key": project},
        "summary": title,
        "description": description,
        "issuetype": {"name": "Bug"},
        "priority": {"name": priority},
        "labels": [QUARANTINE_LABEL],
    }

    LOGGER.info(f"Creating quarantine ticket for test: {test_name}")
    issue = client.create_issue(fields=issue_fields)
    ticket_id = issue.key

    LOGGER.info(f"Created quarantine ticket: {ticket_id}")
    return ticket_id


def get_open_quarantine_tickets() -> list[QuarantineTicket]:
    """Query existing open quarantine tickets from Jira.

    Uses JQL to find all tickets with the quarantined-test label
    that are not in Closed status.

    Returns:
        List of QuarantineTicket entries for all open quarantine tickets.
    """
    project = os.environ.get("JIRA_PROJECT", DEFAULT_JIRA_PROJECT)
    if not re_match(r"^[A-Z][A-Z0-9_-]*$", project):
        raise ValueError(f"Invalid JIRA_PROJECT format: {project!r}")

    jql_query = f"project = {project} AND labels = {QUARANTINE_LABEL} AND status != Closed"

    client = get_jira_client()
    LOGGER.info(f"Querying open quarantine tickets with JQL: {jql_query}")

    issues = client.search_issues(jql_str=jql_query, maxResults=500)
    tickets: list[QuarantineTicket] = []

    for issue in issues:
        fields = issue.fields
        summary = fields.summary
        test_name = summary.removeprefix("[stabilization] ").strip()

        ticket = QuarantineTicket(
            ticket_id=issue.key,
            title=summary,
            status=fields.status.name,
            test_name=test_name,
            team=getattr(fields, JIRA_QE_TEAM_FIELD, "") or "",
            created_date=fields.created,
            resolved=fields.status.name.lower() in ("closed", "resolved", "done"),
        )
        tickets.append(ticket)

    LOGGER.info(f"Found {len(tickets)} open quarantine tickets")
    return tickets


def check_quarantine_ticket_resolved(ticket_id: str) -> bool:
    """Check if a specific quarantine ticket has been resolved or closed.

    Args:
        ticket_id: The Jira ticket ID to check (e.g., "CNV-12345").

    Returns:
        True if the ticket status is Closed or Resolved, False otherwise.
    """
    client = get_jira_client()
    issue = client.issue(id=ticket_id)
    status = issue.fields.status.name.lower()

    LOGGER.info(f"Quarantine ticket {ticket_id} status: {status}")
    return status in ("closed", "resolved")
