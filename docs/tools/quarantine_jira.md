# Jira Integration

Jira operations for quarantine ticket management. Handles creating stabilization tickets for quarantined tests, querying open tickets, and checking resolution status.

**Module:** `quarantine_tools.quarantine_jira`

**Used by:** `analyzer --health-check`, `helper`

---

## Configuration

The module authenticates to Jira using a personal API token. The token is required; the server and project have defaults suitable for Red Hat internal use.

| Variable | Required | Default | Description |
|---|---|---|---|
| `JIRA_TOKEN` | Yes (see fallback) | -- | Jira API token for authentication. |
| `PYTEST_JIRA_TOKEN` | Fallback | -- | Used if `JIRA_TOKEN` is not set. Allows reuse of the pytest-jira token. |
| `JIRA_SERVER` | No | `https://issues.redhat.com` | Jira server URL. |
| `JIRA_PROJECT` | No | `CNV` | Jira project key for quarantine tickets. Must match the pattern `^[A-Z][A-Z0-9_-]*$`. |

At least one of `JIRA_TOKEN` or `PYTEST_JIRA_TOKEN` must be set. If both are set, `JIRA_TOKEN` takes precedence.

### Obtaining a Jira API token

For Red Hat Jira (`issues.redhat.com`) using personal access tokens:

1. Log in to [https://issues.redhat.com](https://issues.redhat.com).
2. Click your avatar in the top-right corner and select **Profile**.
3. Select **Personal Access Tokens** from the left sidebar.
4. Click **Create token**, give it a name, and copy the generated value.
5. Export the token in your environment:

```bash
export JIRA_TOKEN="your-token-here"
```

### Verifying your configuration

```python
from quarantine_tools.quarantine_jira import get_open_quarantine_tickets

tickets = get_open_quarantine_tickets()
print(f"Found {len(tickets)} open quarantine tickets")
```

If neither `JIRA_TOKEN` nor `PYTEST_JIRA_TOKEN` is set, the function raises `MissingEnvironmentVariableError`. If the server is unreachable or the token is invalid, the `jira` library raises a `JIRAError`.

---

## Data Class

### QuarantineTicket

Represents a Jira ticket created for a quarantined test. Defined as a `NamedTuple`.

| Field | Type | Description |
|---|---|---|
| `ticket_id` | `str` | Jira issue key (e.g., `"CNV-12345"`). |
| `title` | `str` | Issue summary as it appears in Jira. |
| `status` | `str` | Current Jira status name (e.g., `"Open"`, `"In Progress"`). |
| `test_name` | `str` | Fully qualified test name extracted from the title (the `[stabilization]` prefix is stripped). |
| `team` | `str` | QE team assignment (from custom field). Empty string if not set. |
| `created_date` | `str` | ISO-8601 creation timestamp. |
| `resolved` | `bool` | `True` if the status is `"closed"`, `"resolved"`, or `"done"` (case-insensitive). |

---

## Public API

### get_jira_client

```python
from quarantine_tools.quarantine_jira import get_jira_client

client = get_jira_client()
```

Creates and returns an authenticated `jira.JIRA` client instance. Uses `JIRA_TOKEN` (falling back to `PYTEST_JIRA_TOKEN`) for token-based authentication and `JIRA_SERVER` for the server URL.

Raises `MissingEnvironmentVariableError` if no token is found.

### create_quarantine_ticket

```python
from quarantine_tools.quarantine_jira import create_quarantine_ticket

ticket_id = create_quarantine_ticket(
    test_name="tests.network.test_connectivity.test_ping",
    failure_context="SSHException: Error reading SSH protocol banner\n...",
    team="Network QE",
    branch="main",
    gating_pipeline=True,
)
print(f"Created: {ticket_id}")  # e.g., "CNV-12345"
```

Creates a Jira Bug ticket for quarantined test stabilization.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `test_name` | `str` | -- | Fully qualified test name (e.g., `"tests.network.test_connectivity.test_ping"`). |
| `failure_context` | `str` | -- | Failure logs and environment details describing the failure. |
| `team` | `str` | -- | Team responsible for the test. |
| `branch` | `str` | -- | Git branch where the failure was observed. |
| `gating_pipeline` | `bool` | `False` | Whether the test is in a gating pipeline. |

Returns the created ticket ID as a string (e.g., `"CNV-12345"`).

**Ticket fields set automatically:**

| Field | Value |
|---|---|
| Project | Value of `JIRA_PROJECT` (default: `CNV`) |
| Summary | `[stabilization] <test_name>` |
| Issue type | Bug |
| Priority | `Critical` if `gating_pipeline=True`, otherwise `Major` |
| Labels | `quarantined-test` |
| Description | Structured Jira markup with test name, team, branch, gating status, and failure context |

### get_open_quarantine_tickets

```python
from quarantine_tools.quarantine_jira import get_open_quarantine_tickets

tickets = get_open_quarantine_tickets()
for ticket in tickets:
    print(f"{ticket.ticket_id}: {ticket.test_name} ({ticket.status})")
```

Queries all open quarantine tickets from Jira. Returns up to 500 results.

**JQL query used:**

```
project = <JIRA_PROJECT> AND labels = quarantined-test AND status != Closed
```

Returns a list of `QuarantineTicket` entries. Validates the project key format before building the query to prevent JQL injection.

### check_quarantine_ticket_resolved

```python
from quarantine_tools.quarantine_jira import check_quarantine_ticket_resolved

if check_quarantine_ticket_resolved(ticket_id="CNV-12345"):
    print("Ticket is resolved, test can be unquarantined")
```

Checks whether a specific quarantine ticket has been resolved or closed.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `ticket_id` | `str` | -- | Jira ticket ID to check (e.g., `"CNV-12345"`). |

Returns `True` if the ticket status is `"closed"` or `"resolved"` (case-insensitive). Returns `False` for all other statuses including `"done"`.

**Note:** The resolved-status check in `check_quarantine_ticket_resolved` uses a different set of statuses (`"closed"`, `"resolved"`) than the `resolved` field on `QuarantineTicket` (`"closed"`, `"resolved"`, `"done"`). This means a ticket with status `"done"` will have `resolved=True` on the data class but `check_quarantine_ticket_resolved` will return `False`.

---

## Error Handling

| Scenario | Exception | When |
|---|---|---|
| No token in environment | `MissingEnvironmentVariableError` | At client creation (any function that calls `get_jira_client()`). |
| Invalid project key format | `ValueError` | In `get_open_quarantine_tickets()` before the query is sent. |
| Jira unreachable or auth failure | `jira.exceptions.JIRAError` | When the `jira` library cannot connect or authenticate. |
| Ticket not found | `jira.exceptions.JIRAError` | In `check_quarantine_ticket_resolved()` if the ticket ID does not exist. |

### Security: project key validation

The `get_open_quarantine_tickets` function validates `JIRA_PROJECT` against the regex `^[A-Z][A-Z0-9_-]*$` before interpolating it into the JQL query. This prevents JQL injection through a malicious `JIRA_PROJECT` value. If the format is invalid, a `ValueError` is raised.
