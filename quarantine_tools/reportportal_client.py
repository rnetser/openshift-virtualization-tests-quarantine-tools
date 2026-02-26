"""Thin wrapper around the ReportPortal REST API for querying test history data.

Provides typed data classes and a client for retrieving test results,
failure rates, and flaky test information from ReportPortal. Used by
quarantine analysis scripts to make data-driven quarantine decisions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from os import environ
from typing import Any, NamedTuple

from requests import Session
from requests.adapters import HTTPAdapter
from simple_logger.logger import get_logger
from urllib3.util.retry import Retry

from quarantine_tools.exceptions import MissingEnvironmentVariableError

LOGGER = get_logger(name=__name__)

REPORTPORTAL_URL_ENV = "REPORTPORTAL_URL"
REPORTPORTAL_TOKEN_ENV = "REPORTPORTAL_TOKEN"
REPORTPORTAL_PROJECT_ENV = "REPORTPORTAL_PROJECT"

DEFAULT_PAGE_SIZE = 300


class TestOutcome(NamedTuple):
    """A single test execution result from ReportPortal.

    Attributes:
        test_name: Fully qualified test name.
        status: Execution status ("PASSED", "FAILED", "SKIPPED").
        launch_id: ReportPortal launch identifier.
        start_time: ISO-8601 formatted start timestamp.
        end_time: ISO-8601 formatted end timestamp.
        defect_type: Defect classification (e.g., "product_bug", "automation_bug",
            "system_issue", "to_investigate").
        message: Failure message if the test did not pass, empty string otherwise.
    """

    test_name: str
    status: str
    launch_id: str
    start_time: str
    end_time: str
    defect_type: str
    message: str


class FlakyTestInfo(NamedTuple):
    """Aggregated flaky test statistics over a time window.

    Attributes:
        test_name: Fully qualified test name.
        failure_count: Number of failures in the time window.
        total_runs: Total number of executions in the time window.
        failure_rate: Failure fraction (0.0 to 1.0).
        last_failure_message: Most recent failure message.
        last_failure_time: ISO-8601 timestamp of the most recent failure.
    """

    test_name: str
    failure_count: int
    total_runs: int
    failure_rate: float
    last_failure_message: str
    last_failure_time: str


def _get_required_env_var(name: str) -> str:
    """Retrieve a required environment variable or raise an error.

    Args:
        name: The environment variable name.

    Returns:
        The environment variable value.

    Raises:
        MissingEnvironmentVariableError: If the variable is not set.
    """
    value = environ.get(name)
    if not value:
        raise MissingEnvironmentVariableError(f"Required environment variable '{name}' is not set")
    return value


class ReportPortalClient:
    """Client for querying test results from ReportPortal REST API.

    Provides methods to retrieve test history, identify flaky tests, and
    calculate failure rates using the ReportPortal v1 API.
    """

    def __init__(self, url: str | None = None, token: str | None = None, project: str | None = None) -> None:
        """Initialize the client. Falls back to environment variables if params not provided.

        Args:
            url: ReportPortal server endpoint. Falls back to REPORTPORTAL_URL env var.
            token: API bearer token. Falls back to REPORTPORTAL_TOKEN env var.
            project: Project name in ReportPortal. Falls back to REPORTPORTAL_PROJECT env var.

        Raises:
            MissingEnvironmentVariableError: If a required parameter is not provided
                and its corresponding environment variable is not set.
        """
        self.url = (url or _get_required_env_var(name=REPORTPORTAL_URL_ENV)).rstrip("/")
        self.token = token or _get_required_env_var(name=REPORTPORTAL_TOKEN_ENV)
        self.project = project or _get_required_env_var(name=REPORTPORTAL_PROJECT_ENV)
        self._base_url = f"{self.url}/api/v1/{self.project}"
        self._session = Session()
        self._session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        })
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST", "PUT", "DELETE"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def close(self) -> None:
        """Close the HTTP session."""
        self._session.close()

    def __enter__(self) -> ReportPortalClient:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: object) -> None:
        self.close()

    def get_test_history(self, test_name: str, days: int = 7) -> list[TestOutcome]:
        """Query test outcomes for a specific test over a time window.

        Uses ReportPortal's item history endpoint to retrieve execution results
        for the named test within the specified number of days.

        Args:
            test_name: Fully qualified test name to query.
            days: Number of days to look back. Defaults to 7.

        Returns:
            List of TestOutcome records, most recent first.
        """
        LOGGER.info(f"Fetching test history for '{test_name}' over the last {days} days")
        since_date = _format_timestamp(timestamp=datetime.now(tz=UTC) - timedelta(days=days))

        items = self._get_test_items(test_name=test_name, since_date=since_date)
        outcomes: list[TestOutcome] = []

        for item in items:
            outcome = _parse_test_item(item=item, test_name=test_name)
            outcomes.append(outcome)

        outcomes.sort(key=lambda outcome: outcome.start_time, reverse=True)
        LOGGER.info(f"Found {len(outcomes)} results for '{test_name}'")
        return outcomes

    def get_flaky_tests(
        self, threshold: int = 3, days: int = 7, branch: str | None = None
    ) -> list[FlakyTestInfo]:
        """Find tests exceeding a failure threshold in the given time window.

        Queries all test items within the time window and identifies tests with
        failure counts at or above the specified threshold.

        Args:
            threshold: Minimum number of failures to be considered flaky. Defaults to 3.
            days: Number of days to look back. Defaults to 7.
            branch: Optional branch name to filter launches by attribute.

        Returns:
            List of FlakyTestInfo sorted by failure_count descending.
        """
        LOGGER.info(f"Searching for flaky tests (threshold={threshold}, days={days}, branch={branch})")
        since_date = _format_timestamp(timestamp=datetime.now(tz=UTC) - timedelta(days=days))

        launch_ids = self._get_launch_ids(since_date=since_date, branch=branch)
        if not launch_ids:
            LOGGER.info("No launches found in the specified time window")
            return []

        test_results: dict[str, list[TestOutcome]] = {}
        for launch_id in launch_ids:
            items = self._get_launch_items(launch_id=launch_id)
            for item in items:
                outcome = _parse_test_item(item=item, test_name=item.get("name", ""))
                results_list = test_results.setdefault(outcome.test_name, [])
                results_list.append(outcome)

        flaky_tests: list[FlakyTestInfo] = []
        for name, outcomes in test_results.items():
            failures = [outcome for outcome in outcomes if outcome.status == "FAILED"]
            failure_count = len(failures)
            if failure_count >= threshold:
                total_runs = len(outcomes)
                failure_rate = failure_count / total_runs
                failures.sort(key=lambda outcome: outcome.start_time, reverse=True)
                last_failure = failures[0]
                flaky_tests.append(FlakyTestInfo(
                    test_name=name,
                    failure_count=failure_count,
                    total_runs=total_runs,
                    failure_rate=failure_rate,
                    last_failure_message=last_failure.message,
                    last_failure_time=last_failure.start_time,
                ))

        flaky_tests.sort(key=lambda info: info.failure_count, reverse=True)
        LOGGER.info(f"Found {len(flaky_tests)} flaky tests exceeding threshold of {threshold}")
        return flaky_tests

    def get_test_failure_rate(self, test_name: str, days: int = 30) -> float:
        """Calculate failure rate percentage over the given period.

        Args:
            test_name: Fully qualified test name to query.
            days: Number of days to look back. Defaults to 30.

        Returns:
            Failure rate as a percentage (0.0 to 100.0). Returns 0.0 if no
            test results are found.
        """
        LOGGER.info(f"Calculating failure rate for '{test_name}' over {days} days")
        outcomes = self.get_test_history(test_name=test_name, days=days)
        if not outcomes:
            LOGGER.info(f"No results found for '{test_name}', returning 0.0 failure rate")
            return 0.0

        failure_count = sum(1 for outcome in outcomes if outcome.status == "FAILED")
        rate = round((failure_count / len(outcomes)) * 100.0, 2)
        LOGGER.info(f"Failure rate for '{test_name}': {rate}% ({failure_count}/{len(outcomes)})")
        return rate

    def get_launch_results(self, launch_id: str) -> list[TestOutcome]:
        """Get all test results for a specific launch/run.

        Args:
            launch_id: ReportPortal launch identifier.

        Returns:
            List of TestOutcome records for every test in the launch.
        """
        LOGGER.info(f"Fetching results for launch '{launch_id}'")
        items = self._get_launch_items(launch_id=launch_id)
        outcomes = [_parse_test_item(item=item, test_name=item.get("name", "")) for item in items]
        LOGGER.info(f"Found {len(outcomes)} test results in launch '{launch_id}'")
        return outcomes

    def _make_request(
        self, method: str, endpoint: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Execute an HTTP request against the ReportPortal API.

        Retries are handled automatically by the session's HTTPAdapter retry
        strategy configured in __init__.

        Args:
            method: HTTP method (GET, POST, etc.).
            endpoint: API endpoint path (appended to base URL).
            params: Optional query parameters.

        Returns:
            Parsed JSON response as a dictionary.

        Raises:
            HTTPError: If the request fails after all retries.
        """
        url = f"{self._base_url}/{endpoint.lstrip('/')}"
        LOGGER.info(f"ReportPortal API {method} {endpoint}")
        response = self._session.request(method=method, url=url, params=params)
        response.raise_for_status()
        return response.json()

    def _get_test_items(self, test_name: str, since_date: str) -> list[dict[str, Any]]:
        """Retrieve test item records matching a test name within a date range.

        Handles pagination to collect all matching results.

        Args:
            test_name: Test name to filter by.
            since_date: ISO-8601 start date filter.

        Returns:
            List of raw test item dictionaries from the API.
        """
        all_items: list[dict[str, Any]] = []
        page = 1

        while True:
            params: dict[str, Any] = {
                "filter.eq.name": test_name,
                "filter.gte.startTime": since_date,
                "filter.eq.type": "STEP",
                "page.page": page,
                "page.size": DEFAULT_PAGE_SIZE,
                "page.sort": "startTime,desc",
            }
            response_data = self._make_request(method="GET", endpoint="item", params=params)
            items = response_data.get("content", [])
            all_items.extend(items)

            page_info = response_data.get("page", {})
            total_pages = page_info.get("totalPages", 1)
            if page >= total_pages:
                break
            page += 1

        return all_items

    def _get_launch_ids(self, since_date: str, branch: str | None = None) -> list[str]:
        """Retrieve launch IDs within a date range, optionally filtered by branch.

        Args:
            since_date: ISO-8601 start date filter.
            branch: Optional branch name to filter by launch attributes.

        Returns:
            List of launch ID strings.
        """
        params: dict[str, Any] = {
            "filter.gte.startTime": since_date,
            "page.size": DEFAULT_PAGE_SIZE,
            "page.sort": "startTime,desc",
        }
        if branch:
            params["filter.has.compositeAttribute"] = f"branch:{branch}"
            LOGGER.info("Filtering launches by branch attribute: branch:%s", branch)

        LOGGER.info("Launch query params: %s", params)
        LOGGER.debug("Full API URL: %s/%s", self._base_url, "launch")

        all_launch_ids: list[str] = []
        current_page = 1

        while True:
            params["page.page"] = current_page
            LOGGER.debug("Request params: %s", params)
            response_data = self._make_request(method="GET", endpoint="launch", params=params)
            launches = response_data.get("content", [])

            LOGGER.debug(
                "Response page %d: %d launches, total elements: %s",
                current_page,
                len(launches),
                response_data.get("page", {}).get("totalElements", "unknown"),
            )

            if launches and current_page == 1:
                # Log first 3 launch attributes for debugging
                for launch in launches[:3]:
                    launch_name = launch.get("name", "unknown")
                    launch_attrs = launch.get("attributes", [])
                    LOGGER.info("Sample launch '%s' attributes: %s", launch_name, launch_attrs)

            all_launch_ids.extend(str(launch["id"]) for launch in launches)

            page_info = response_data.get("page", {})
            total_pages = page_info.get("totalPages", 1)
            if current_page >= total_pages:
                break
            current_page += 1

        LOGGER.info(f"Found {len(all_launch_ids)} launches since {since_date}")

        # Diagnostic fallback: if branch filter returned 0 results, query without it to compare
        if not all_launch_ids and branch:
            LOGGER.warning(
                "No launches found with branch filter 'branch:%s'. Trying without filter to diagnose...",
                branch,
            )
            debug_params = {key: value for key, value in params.items() if "Attribute" not in key}
            debug_response = self._make_request(
                method="GET", endpoint="launch", params={**debug_params, "page.size": "5"}
            )
            debug_launches = debug_response.get("content", [])
            total_without_filter = debug_response.get("page", {}).get("totalElements", 0)
            LOGGER.warning("Without branch filter: %d total launches found", total_without_filter)
            for debug_launch in debug_launches[:3]:
                LOGGER.warning(
                    "  Launch '%s' (id=%s) attributes: %s",
                    debug_launch.get("name", "?"),
                    debug_launch.get("id", "?"),
                    debug_launch.get("attributes", []),
                )

        return all_launch_ids

    def _get_launch_items(self, launch_id: str) -> list[dict[str, Any]]:
        """Retrieve all test items for a specific launch.

        Handles pagination to collect all test items.

        Args:
            launch_id: ReportPortal launch identifier.

        Returns:
            List of raw test item dictionaries from the API.
        """
        all_items: list[dict[str, Any]] = []
        page = 1

        while True:
            params: dict[str, Any] = {
                "filter.eq.launchId": launch_id,
                "filter.eq.type": "STEP",
                "page.page": page,
                "page.size": DEFAULT_PAGE_SIZE,
                "page.sort": "startTime,desc",
            }
            response_data = self._make_request(method="GET", endpoint="item", params=params)
            items = response_data.get("content", [])
            all_items.extend(items)

            page_info = response_data.get("page", {})
            total_pages = page_info.get("totalPages", 1)
            if page >= total_pages:
                break
            page += 1

        return all_items


def _format_timestamp(timestamp: datetime) -> str:
    """Format a datetime to the ReportPortal API timestamp format.

    Args:
        timestamp: The datetime to format.

    Returns:
        ISO-8601 formatted string with millisecond precision.
    """
    return timestamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _parse_timestamp(timestamp: str) -> datetime:
    """Parse a ReportPortal timestamp string into a datetime object.

    Handles both millisecond-precision ISO-8601 strings and Unix millisecond
    timestamps that ReportPortal may return.

    Args:
        timestamp: Timestamp string from ReportPortal (ISO-8601 or Unix millis).

    Returns:
        Timezone-aware datetime in UTC.
    """
    if timestamp.isdigit():
        return datetime.fromtimestamp(int(timestamp) / 1000, tz=UTC)
    cleaned = timestamp.replace("Z", "+00:00")
    return datetime.fromisoformat(cleaned)


def _extract_defect_type(item: dict[str, Any]) -> str:
    """Extract the defect type classification from a test item.

    Args:
        item: Raw test item dictionary from the API.

    Returns:
        Defect type string (e.g., "product_bug", "automation_bug"). Returns
        empty string if no defect type is present.
    """
    statistics = item.get("statistics", {})
    defects = statistics.get("defects", {})
    for defect_type, count in defects.items():
        if isinstance(count, dict):
            if count.get("total", 0) > 0:
                return defect_type
        elif isinstance(count, (int, float)) and count > 0:
            return defect_type
    return ""


def _parse_test_item(item: dict[str, Any], test_name: str) -> TestOutcome:
    """Parse a raw ReportPortal test item into a TestOutcome.

    Args:
        item: Raw test item dictionary from the API.
        test_name: Fallback test name if not present in the item.

    Returns:
        Parsed TestOutcome record.
    """
    start_time_raw = str(item.get("startTime", ""))
    end_time_raw = str(item.get("endTime", ""))

    start_time = _format_timestamp(timestamp=_parse_timestamp(timestamp=start_time_raw)) if start_time_raw else ""
    end_time = _format_timestamp(timestamp=_parse_timestamp(timestamp=end_time_raw)) if end_time_raw else ""

    issue = item.get("issue")
    if isinstance(issue, dict):
        defect_type = issue.get("issueType", "") or _extract_defect_type(item=item)
        message = issue.get("comment", item.get("description", ""))
    else:
        defect_type = _extract_defect_type(item=item)
        message = item.get("description", "")

    return TestOutcome(
        test_name=item.get("name", test_name),
        status=item.get("status", "UNKNOWN"),
        launch_id=str(item.get("launchId", "")),
        start_time=start_time,
        end_time=end_time,
        defect_type=defect_type,
        message=message or "",
    )


def get_test_history(test_name: str, days: int = 7) -> list[TestOutcome]:
    """Convenience function using env var configuration.

    Creates a ReportPortalClient from environment variables and retrieves
    test history for the specified test.

    Args:
        test_name: Fully qualified test name to query.
        days: Number of days to look back. Defaults to 7.

    Returns:
        List of TestOutcome records, most recent first.

    Raises:
        MissingEnvironmentVariableError: If required environment variables are not set.
    """
    with ReportPortalClient() as client:
        return client.get_test_history(test_name=test_name, days=days)


def get_flaky_tests(
    threshold: int = 3, days: int = 7, branch: str | None = None
) -> list[FlakyTestInfo]:
    """Convenience function using env var configuration.

    Creates a ReportPortalClient from environment variables and searches
    for flaky tests exceeding the specified failure threshold.

    Args:
        threshold: Minimum number of failures to be considered flaky. Defaults to 3.
        days: Number of days to look back. Defaults to 7.
        branch: Optional branch name to filter launches by attribute.

    Returns:
        List of FlakyTestInfo sorted by failure_count descending.

    Raises:
        MissingEnvironmentVariableError: If required environment variables are not set.
    """
    with ReportPortalClient() as client:
        return client.get_flaky_tests(threshold=threshold, days=days, branch=branch)
