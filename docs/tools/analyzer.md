# Flaky Test Analyzer

The Flaky Test Analyzer queries ReportPortal for flaky tests across OpenShift Virtualization test suites. It produces reports in table, JSON, or HTML format, identifies quarantine candidates, and checks the health of currently quarantined tests to find de-quarantine candidates.

## Quick Start

Run a basic flaky test analysis against ReportPortal with default settings (3+ failures in the last 7 days):

```bash
flaky-test-analyzer
```

This requires ReportPortal credentials. See [Prerequisites](#prerequisites) for setup.

## Prerequisites

### ReportPortal Access (required for analysis and cross-reference modes)

The analyzer connects to ReportPortal to query test execution history. Provide credentials through environment variables or CLI flags. CLI flags take precedence over environment variables.

| Variable               | Description                          | Required For           |
|------------------------|--------------------------------------|------------------------|
| `REPORTPORTAL_URL`     | ReportPortal server URL              | Analysis, cross-reference |
| `REPORTPORTAL_TOKEN`   | ReportPortal API token               | Analysis, cross-reference |
| `REPORTPORTAL_PROJECT` | ReportPortal project name            | Analysis, cross-reference |

Health check mode (`--health-check`) works without ReportPortal but produces limited results. When ReportPortal is configured, health check also checks consecutive pass history.

### Local Repository (required for cross-reference and health check modes)

Cross-reference (`--check-quarantined`) and health check (`--health-check`) modes scan the local repository for quarantined tests. The repository must contain a `tests/` directory.

### Jira Access (optional, used by health check mode)

Health check mode optionally queries Jira to check whether tickets linked to quarantined tests are resolved. If Jira integration is not configured, the tool skips ticket status checks and logs a warning.

## CLI Arguments

| Argument                  | Type    | Default | Description                                                                 |
|---------------------------|---------|---------|-----------------------------------------------------------------------------|
| `--threshold`             | int     | `3`     | Minimum number of failures to flag a test as flaky.                         |
| `--days`                  | int     | `7`     | Number of days to look back for analysis.                                   |
| `--branch`                | string  | all     | Filter results by branch name. When omitted, all branches are included.     |
| `--output`                | string  | `table` | Output format. Choices: `table`, `json`, `html`.                            |
| `--check-quarantined`     | flag    | off     | Cross-reference flaky tests against currently quarantined tests.            |
| `--health-check`          | flag    | off     | Check quarantine health to find tests ready for de-quarantine.              |
| `--repo-path`             | string  | `.`     | Path to local repository root containing a `tests/` directory.              |
| `--reportportal-url`      | string  | env var | ReportPortal server URL. Overrides `REPORTPORTAL_URL` environment variable. |
| `--reportportal-token`    | string  | env var | ReportPortal API token. Overrides `REPORTPORTAL_TOKEN` environment variable.|
| `--reportportal-project`  | string  | env var | ReportPortal project name. Overrides `REPORTPORTAL_PROJECT` environment variable. |

## Modes

### Analysis Mode (default)

Queries ReportPortal for tests that have failed at least `--threshold` times within the last `--days` days. Groups results by team (derived from the test path under `tests/`) and calculates a trend direction by comparing failure counts in the first half of the time window versus the second half.

```bash
# Find tests with 3+ failures in the last 7 days (defaults)
flaky-test-analyzer

# Find tests with 5+ failures in the last 14 days on the main branch
flaky-test-analyzer --threshold 5 --days 14 --branch main
```

**Output includes:**

- Test name (truncated to 60 characters for table display)
- Team assignment (virt, network, storage, iuo, observability, infrastructure, data_protection, chaos)
- Failure count and failure rate
- Trend direction: `improving`, `WORSENING`, or `stable`

### Cross-Reference Mode (`--check-quarantined`)

Extends the analysis by scanning the local repository for quarantined tests (tests marked with the quarantine marker). Compares the flaky test list from ReportPortal against the quarantined test list to identify:

- **Quarantine candidates** -- flaky tests that are not yet quarantined.
- **De-quarantine candidates** -- quarantined tests that now have a 0% failure rate.

```bash
# Cross-reference against the local repository
flaky-test-analyzer --check-quarantined --repo-path /path/to/openshift-virtualization-tests

# Cross-reference with custom thresholds
flaky-test-analyzer --check-quarantined --threshold 5 --days 14 --repo-path /path/to/repo
```

**Requires:** ReportPortal credentials and a local repository with a `tests/` directory.

### Health Check Mode (`--health-check`)

An independent workflow that identifies quarantined tests ready for de-quarantine. It cross-references quarantined tests against two data sources:

1. **ReportPortal pass history** -- tests with 5 or more consecutive passes are flagged.
2. **Jira ticket status** -- tests linked to resolved or closed Jira tickets are flagged.

A test is flagged as a de-quarantine candidate when either or both conditions are met.

```bash
# Run health check with default 14-day lookback
flaky-test-analyzer --health-check --repo-path /path/to/repo

# Health check with a custom lookback period
flaky-test-analyzer --health-check --days 30 --repo-path /path/to/repo

# Health check with JSON output
flaky-test-analyzer --health-check --output json --repo-path /path/to/repo
```

**Output includes:**

- Test name and team
- Linked Jira ticket
- Consecutive pass count (from ReportPortal)
- Whether the Jira ticket is resolved
- Reason for flagging: `passing_consistently`, `jira_resolved`, or `both`

**Works partially without ReportPortal or Jira.** When either integration is unavailable, the tool skips that check and logs a warning. At least one integration must provide a signal for a test to appear as a candidate.

## Output Formats

### Table (default)

Human-readable ASCII tables printed to stdout. In cross-reference mode, the flaky test table is followed by a separate cross-reference report.

```bash
flaky-test-analyzer --output table
```

### JSON

Machine-readable JSON printed to stdout. Includes all data fields. Suitable for piping to other tools or saving to a file.

```bash
flaky-test-analyzer --output json > flaky_report.json
```

### HTML

HTML fragment suitable for embedding in a dashboard page. Includes styled tables with CSS classes for trend indicators and cross-reference sections.

```bash
flaky-test-analyzer --output html > flaky_report.html
```

## Example Commands

```bash
# Basic analysis with defaults
flaky-test-analyzer

# Strict analysis: 5+ failures in 14 days, main branch only, JSON output
flaky-test-analyzer --threshold 5 --days 14 --branch main --output json

# Cross-reference to find quarantine and de-quarantine candidates
flaky-test-analyzer --check-quarantined --repo-path ~/git/openshift-virtualization-tests

# Health check: find tests ready to leave quarantine
flaky-test-analyzer --health-check --days 14 --repo-path ~/git/openshift-virtualization-tests

# Health check with HTML output for dashboard integration
flaky-test-analyzer --health-check --output html --repo-path ~/git/openshift-virtualization-tests

# Use explicit ReportPortal credentials instead of environment variables
flaky-test-analyzer --reportportal-url https://rp.example.com \
    --reportportal-token my-token \
    --reportportal-project my-project
```

## Exit Codes

| Code | Meaning                                                                                  |
|------|------------------------------------------------------------------------------------------|
| `0`  | Success. Analysis completed and output was produced. Also returned when ReportPortal is not configured and no analysis was needed. |
| `1`  | Error. Possible causes: repository path does not exist, `tests/` directory not found, `--check-quarantined` used without ReportPortal credentials. |

## Team Mapping

Tests are assigned to teams based on the first directory component under `tests/` in the test path:

| Directory                  | Team               |
|----------------------------|---------------------|
| `virt`                     | virt                |
| `network`                  | network             |
| `storage`                  | storage             |
| `install_upgrade_operators`| iuo                 |
| `observability`            | observability       |
| `infrastructure`           | infrastructure      |
| `data_protection`          | data_protection     |
| `chaos`                    | chaos               |
| `deprecated_api`           | chaos (first match) |

Tests that do not match any known directory are assigned to the `unknown` team.

## Troubleshooting

**"ReportPortal not configured" warning**

Set the three required environment variables or pass them as CLI flags. All three are required for analysis and cross-reference modes.

**"Tests directory not found" error**

Ensure `--repo-path` points to the repository root that contains a `tests/` subdirectory. The default is the current working directory.

**"Jira integration unavailable" warning (health check mode)**

Jira integration is optional. The health check continues without it, using only ReportPortal data. If you need Jira checks, ensure the `quarantine_tools.quarantine_jira` module is importable and configured.

**No de-quarantine candidates found**

This means no quarantined tests meet the criteria: either no tests have 5+ consecutive passes in ReportPortal, and no linked Jira tickets are resolved/closed within the lookback period.
