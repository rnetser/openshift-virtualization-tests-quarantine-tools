# ReportPortal Client

REST API client for querying test execution history from ReportPortal. Provides typed data classes and methods for retrieving test results, failure rates, and flaky test information.

**Module:** `quarantine_tools.reportportal_client`

**Used by:** `analyzer`, `metrics`, `dashboard --with-reportportal`

---

## Configuration

The client authenticates to ReportPortal using environment variables. All three variables are **required** unless you pass the equivalent values directly to the constructor.

| Variable | Required | Default | Description |
|---|---|---|---|
| `REPORTPORTAL_URL` | Yes | -- | Base URL of your ReportPortal instance. Include the scheme but omit trailing slashes. Example: `https://reportportal.example.com` |
| `REPORTPORTAL_TOKEN` | Yes | -- | API bearer token for authentication. |
| `REPORTPORTAL_PROJECT` | Yes | -- | Project name as it appears in the ReportPortal URL path. |

### Obtaining an API token

1. Log in to your ReportPortal instance.
2. Click your avatar in the top-right corner and select **Profile**.
3. Scroll to the **API Keys** section.
4. Click **Generate** to create a new key, or copy an existing one.

### Verifying your configuration

Export the variables and run a quick check from a Python shell:

```python
from quarantine_tools.reportportal_client import ReportPortalClient

with ReportPortalClient() as client:
    # Fetch last 24 hours of results for any known test
    outcomes = client.get_test_history(
        test_name="tests.network.test_connectivity.test_ping",
        days=1,
    )
    print(f"Found {len(outcomes)} results")
```

If the variables are missing, the client raises `MissingEnvironmentVariableError` immediately at construction time. If the server is unreachable or the token is invalid, the underlying `requests` library raises an `HTTPError` after exhausting retries.

---

## Data Classes

### TestOutcome

A single test execution result from ReportPortal. Defined as a `NamedTuple`.

| Field | Type | Description |
|---|---|---|
| `test_name` | `str` | Fully qualified test name. |
| `status` | `str` | Execution status: `"PASSED"`, `"FAILED"`, or `"SKIPPED"`. |
| `launch_id` | `str` | ReportPortal launch identifier. |
| `start_time` | `str` | ISO-8601 formatted start timestamp with millisecond precision. |
| `end_time` | `str` | ISO-8601 formatted end timestamp with millisecond precision. |
| `defect_type` | `str` | Defect classification (e.g., `"product_bug"`, `"automation_bug"`, `"system_issue"`, `"to_investigate"`). Empty string if none. |
| `message` | `str` | Failure message if the test did not pass. Empty string otherwise. |

### FlakyTestInfo

Aggregated statistics for a flaky test over a time window. Defined as a `NamedTuple`.

| Field | Type | Description |
|---|---|---|
| `test_name` | `str` | Fully qualified test name. |
| `failure_count` | `int` | Number of failures in the time window. |
| `total_runs` | `int` | Total number of executions in the time window. |
| `failure_rate` | `float` | Failure fraction from `0.0` to `1.0`. |
| `last_failure_message` | `str` | Most recent failure message. |
| `last_failure_time` | `str` | ISO-8601 timestamp of the most recent failure. |

---

## Public API

### ReportPortalClient

```python
from quarantine_tools.reportportal_client import ReportPortalClient
```

#### Constructor

```python
ReportPortalClient(
    url: str | None = None,
    token: str | None = None,
    project: str | None = None,
)
```

All parameters are optional. When omitted, the client reads from the corresponding environment variable (`REPORTPORTAL_URL`, `REPORTPORTAL_TOKEN`, `REPORTPORTAL_PROJECT`). Raises `MissingEnvironmentVariableError` if a parameter is not provided and its environment variable is not set.

#### Context manager usage

The client manages an HTTP session internally. Use it as a context manager to ensure the session is closed when you are done:

```python
with ReportPortalClient() as client:
    outcomes = client.get_test_history(test_name="tests.storage.test_pvc.test_create")
    rate = client.get_test_failure_rate(test_name="tests.storage.test_pvc.test_create")
```

You can also call `client.close()` manually if you prefer not to use a `with` block.

#### get_test_history

```python
get_test_history(test_name: str, days: int = 7) -> list[TestOutcome]
```

Query test outcomes for a specific test over a time window.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `test_name` | `str` | -- | Fully qualified test name to query. |
| `days` | `int` | `7` | Number of days to look back. |

Returns a list of `TestOutcome` records sorted most-recent-first. Returns an empty list if no results are found.

#### get_flaky_tests

```python
get_flaky_tests(
    threshold: int = 3,
    days: int = 7,
    branch: str | None = None,
) -> list[FlakyTestInfo]
```

Find tests exceeding a failure threshold in the given time window.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `threshold` | `int` | `3` | Minimum number of failures to be considered flaky. |
| `days` | `int` | `7` | Number of days to look back. |
| `branch` | `str \| None` | `None` | Optional branch name to filter launches by attribute. |

Returns a list of `FlakyTestInfo` sorted by `failure_count` descending. Returns an empty list if no launches are found or no tests meet the threshold.

#### get_test_failure_rate

```python
get_test_failure_rate(test_name: str, days: int = 30) -> float
```

Calculate the failure rate percentage for a test over the given period.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `test_name` | `str` | -- | Fully qualified test name to query. |
| `days` | `int` | `30` | Number of days to look back. |

Returns the failure rate as a percentage from `0.0` to `100.0`. Returns `0.0` if no test results are found.

#### get_launch_results

```python
get_launch_results(launch_id: str) -> list[TestOutcome]
```

Get all test results for a specific launch.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `launch_id` | `str` | -- | ReportPortal launch identifier. |

Returns a list of `TestOutcome` records for every test in the launch.

---

## Convenience Functions

The module provides two top-level functions that create a `ReportPortalClient` from environment variables, execute a single query, and close the client automatically.

### get_test_history

```python
from quarantine_tools.reportportal_client import get_test_history

outcomes = get_test_history(
    test_name="tests.network.test_connectivity.test_ping",
    days=7,
)
```

### get_flaky_tests

```python
from quarantine_tools.reportportal_client import get_flaky_tests

flaky = get_flaky_tests(threshold=3, days=7, branch="main")
```

Both functions raise `MissingEnvironmentVariableError` if the required environment variables are not set.

---

## Error Handling

| Scenario | Exception | When |
|---|---|---|
| Environment variable not set | `MissingEnvironmentVariableError` | At client construction (or when calling a convenience function). |
| ReportPortal unreachable | `requests.exceptions.ConnectionError` | After exhausting all retries. |
| Authentication failure (401/403) | `requests.exceptions.HTTPError` | Immediately (not in the retry list). |
| Server error (500/502/503/504) | `requests.exceptions.HTTPError` | After exhausting all retries. |
| Rate limited (429) | `requests.exceptions.HTTPError` | After exhausting all retries. |

### Retry behavior

The client uses a `urllib3.Retry` strategy with the following settings:

| Setting | Value |
|---|---|
| Total retries | 3 |
| Backoff factor | 1 second (1s, 2s, 4s between attempts) |
| Retried status codes | 429, 500, 502, 503, 504 |
| Retried HTTP methods | GET, POST, PUT, DELETE |

Status codes outside the retry list (such as 401 Unauthorized) raise an `HTTPError` immediately without retrying.
