# Quarantine Guidelines

Comprehensive guide for the test quarantine system in the openshift-virtualization-tests project.

**Audience:** QE engineers working on OpenShift Virtualization test automation.

---

## Overview

A quarantined test is marked with `@pytest.mark.xfail(reason=f"{QUARANTINED}: ...", run=False)` so that it is **skipped during normal test runs**. The pytest summary reports these tests distinctly:

```text
1 deselected, 2 passed, 1 quarantined, ...
```

**Purpose:** Prevent known-flaky tests from blocking CI/CD pipelines while tracking them for resolution.

**Key principle:** The quarantine system is recommend-only. There is no automatic quarantining. Every quarantine decision requires human verification to confirm the failure is not a real regression.

---

## When to Quarantine a Test

Quarantine a test when **all** of the following are true:

1. The test fails intermittently (it is not a real product regression).
2. The failure has occurred more than once in the past 10 runs (or exceeds the configured threshold, such as 5 failures in 7 days).
3. Root cause investigation has started.
4. A Jira ticket exists with the label `quarantined-test`.

### When NOT to Quarantine

| Scenario | Action Instead |
|----------|---------------|
| Product bug | Use `@pytest.mark.jira("CNV-12345", run=False)` to skip conditionally while the bug is open. |
| System/environment issue (Artifactory, network, resources) | Open a ticket to DevOps. You **cannot** quarantine these. |
| Release-blocking test | Use a blocker Jira ticket. Fix within the current sprint. |

### Failure Category Reference

| Category | Identification | Quarantine? | Required Action |
|----------|---------------|-------------|-----------------|
| Product bug | Failure caused by actual product defect | No | Open Jira bug, use `pytest_jira` conditional skip |
| Automation issue | Failure caused by test code or framework | Yes | Manual verification, then quarantine with Jira ticket |
| System/environment | Infrastructure or external dependency failure | No | Open DevOps ticket, verify manually |

### Y-Stream vs. Z-Stream Considerations

- **Y-stream (`main` branch):** Exercise extra caution before Code Freeze. Product code may contain bugs; distinguish carefully between product bugs and automation issues.
- **Z-stream:** Lanes **must be green**. No new features or API changes; failures are likely regressions requiring immediate attention.

---

## How to Quarantine (Manual)

Apply the `xfail` marker with the `QUARANTINED` constant and `run=False`:

```python
import pytest

from utilities.constants import QUARANTINED


@pytest.mark.xfail(
    reason=f"{QUARANTINED}: SSH timeout in CI environment, CNV-12345",
    run=False,
)
def test_example():
    ...
```

**Requirements for the marker:**

- Import `QUARANTINED` from `utilities.constants` (value: `"quarantined"`).
- The `reason` string must start with `f"{QUARANTINED}: "`.
- Include a brief description of the failure and the Jira ticket ID.
- Set `run=False` so the test is skipped rather than executed.

---

## How to Quarantine (Using the CLI Tool)

The `quarantine_helper.py` script automates the mechanical steps of adding and removing markers. It handles import management, decorator placement, and `ruff` formatting automatically.

### List Currently Quarantined Tests

```bash
uv run quarantine-helper suggest
```

Displays a table of all quarantined tests with their Jira tickets and team assignments.

### Apply a Quarantine Marker

```bash
uv run quarantine-helper apply \
    tests/virt/test_example.py::test_func \
    --jira CNV-12345 \
    --reason "SSH timeout in CI"
```

This command:

1. Parses the test path and locates the function via AST analysis.
2. Ensures `import pytest` and `from utilities.constants import QUARANTINED` are present.
3. Inserts the `@pytest.mark.xfail(...)` decorator before any existing decorators.
4. Runs `ruff format` on the modified file.

### Check Quarantine Status

```bash
uv run quarantine-helper status
```

Shows a summary with total tests, active tests, quarantined tests, and a per-team breakdown.

---

## How to De-Quarantine

A test can be de-quarantined when:

1. The Jira ticket is resolved **and** the root cause fix is merged.
2. The test passes consistently: **25 consecutive successful runs** via Jenkins using `pytest-repeat` on a cluster matching the original failure environment.

### Verification Command

```bash
pytest --repeat-scope=session --count=25 <path to test module>
```

The run count (25) can be adjusted based on test characteristics and risk; discuss within the SIG.

### Remove the Quarantine Marker (CLI)

```bash
uv run quarantine-helper remove \
    tests/virt/test_example.py::test_func
```

The tool locates and removes the multi-line `@pytest.mark.xfail` decorator containing `QUARANTINED`, then runs `ruff format`.

### De-Quarantine Checklist

Complete **all** items before re-including a test:

- [ ] Root cause identified and fixed
- [ ] Test fix implemented and verified locally
- [ ] Assert messages enhanced with meaningful failure context (if needed)
- [ ] `xfail` marker removed from test
- [ ] Jenkins verification passes (default: 25 consecutive runs)

After the PR is merged:

- [ ] Test is included in an active test lane
- [ ] Jira ticket updated with root cause, fix description, verification results, and closed
- [ ] Backport PRs verified using the same process (if applicable)

---

## Automated Flaky Test Detection

The `flaky_test_analyzer.py` script queries ReportPortal for tests exceeding failure thresholds. It does **not** auto-quarantine; it produces candidate lists for human review.

### Basic Usage

```bash
# Analyze flaky tests on main branch (default: 3+ failures in 7 days)
uv run flaky-test-analyzer \
    --threshold 5 --days 7 --branch main

# Cross-reference flaky tests against currently quarantined tests
uv run flaky-test-analyzer --check-quarantined

# JSON output (for CI pipelines)
uv run flaky-test-analyzer --output json > flaky_report.json

# HTML output (for dashboards)
uv run flaky-test-analyzer --output html > flaky_report.html
```

### Cross-Reference Mode

When `--check-quarantined` is used, the analyzer:

1. Scans the local repository for quarantined tests using `TestScanner`.
2. Compares against ReportPortal flaky data to identify:
   - **Quarantine candidates** -- flaky tests not yet quarantined.
   - **De-quarantine candidates** -- quarantined tests now passing consistently (0% failure rate).

### GitHub Actions: Quarantine Recommender

The `quarantine-recommender.yml` workflow runs on weekdays at 8:00 AM UTC (and supports manual dispatch):

1. Runs `flaky_test_analyzer.py` with cross-referencing enabled.
2. If quarantine candidates are found, creates or updates a GitHub issue labeled `quarantine-candidate`.
3. The issue includes a candidate table and a quarantine checklist.

**This workflow recommends candidates only. It never applies quarantine markers automatically.**

---

## Biweekly Review Process

Every 1st and 15th of the month, teams should review quarantine health.

### Review Steps

1. **Review SLA breaches.** Identify tests quarantined for more than 30 days. Escalate or create a plan for resolution.
2. **Verify de-quarantine candidates.** Run `--check-quarantined` to find tests now passing consistently. Confirm they are ready for re-inclusion.
3. **Close resolved Jira tickets.** Remove `xfail` markers for fixed tests and close the associated tickets with root cause documentation.
4. **Update dashboards.** Regenerate the HTML dashboard to reflect current status.

---

## Nightly Regression for Quarantined Tests

Quarantined tests can be run separately to verify whether fixes have landed. The `--run-quarantined-only` pytest option overrides `run=False` and executes only quarantined tests:

```bash
pytest --run-quarantined-only --tc-file=tests/global_config.py
```

**Behavior:**

- Deselects all non-quarantined tests.
- For quarantined tests, changes `run=False` to `run=True` so they actually execute.
- Results feed back into the de-quarantine detection system.

Use this in Jenkins nightly jobs to continuously check whether quarantined tests have been fixed upstream.

---

## Dashboard and Metrics

### HTML Dashboard

Generate an interactive HTML dashboard showing quarantine statistics by version and team:

```bash
uv run quarantine-dashboard
```

The dashboard scans both `RedHatQE/openshift-virtualization-tests` and `RedHatQE/cnv-tests` repositories across all active CNV version branches.

### JSON Export

```bash
uv run quarantine-dashboard --json
```

Produces machine-readable output at `scripts/quarantine_stats/dashboard.json`.

### Prometheus Metrics

Export quarantine statistics in Prometheus text exposition format:

```bash
# Print to stdout
uv run quarantine-metrics --branch main

# Write to file
uv run quarantine-metrics --output-file /tmp/metrics.prom

# Push to Prometheus Pushgateway
uv run quarantine-metrics --push-gateway http://pushgateway:9091
```

**Exported metrics:**

| Metric | Description |
|--------|-------------|
| `cnv_tests_total` | Total number of tests per team and branch |
| `cnv_tests_quarantined` | Number of quarantined tests per team and branch |
| `cnv_tests_health_percent` | Percentage of non-quarantined tests |
| `cnv_quarantine_avg_age_days` | Average quarantine duration in days |
| `cnv_tests_flaky_candidates` | Flaky tests not yet quarantined (requires ReportPortal) |

Include `--include-flaky` to enable flaky candidate metrics (requires ReportPortal configuration).

---

## Configuration

### ReportPortal

Required for flaky test analysis and cross-referencing.

| Variable | Description |
|----------|-------------|
| `REPORTPORTAL_URL` | ReportPortal server URL |
| `REPORTPORTAL_TOKEN` | API authentication token |
| `REPORTPORTAL_PROJECT` | ReportPortal project name |

These can also be passed via CLI flags: `--reportportal-url`, `--reportportal-token`, `--reportportal-project`.

### Jira

Required for ticket management and linking.

| Variable | Description |
|----------|-------------|
| `JIRA_TOKEN` or `PYTEST_JIRA_TOKEN` | API authentication token |
| `JIRA_SERVER` | Jira instance URL (default: `https://issues.redhat.com`) |

### Thresholds

Configurable via CLI flags on `flaky_test_analyzer.py`:

| Flag | Default | Description |
|------|---------|-------------|
| `--threshold` | `3` | Minimum failure count to flag a test as flaky |
| `--days` | `7` | Number of days to look back for analysis |
| `--branch` | all | Filter analysis to a specific branch |

---

## SLA Guidelines

| Quarantine Age | Status | Required Action |
|----------------|--------|-----------------|
| Less than 14 days | Green | Active investigation in progress. No escalation needed. |
| 14 to 30 days | Yellow | Escalate to team lead. Ensure investigation is progressing. |
| More than 30 days | Red | SLA breach. Require a resolution plan or convert to permanent skip with justification. |

---

## Pull Request Requirements

When submitting a quarantine or de-quarantine PR:

- **Title:** Must start with `Quarantine:`.
- **Description:** Include a link to the Jira ticket and a brief explanation.
- **Label:** The `quarantine` label is added automatically.
- **Backporting:** If the fix applies to other branches, backport the PR and verify each backport independently.

### Jira Ticket Requirements

When creating the associated Jira ticket:

- **Title:** Starts with `[stabilization]`.
- **Labels:** `quarantined-test`.
- **Priority:** Based on test importance.
- **Content:** Complete failure analysis, logs, and specific actions for de-quarantining.
- **Backport notes:** Include if the fix needs backporting to other branches.

---

## Quick Reference

### Quarantine a Test

```bash
# Via CLI tool (recommended)
uv run quarantine-helper apply \
    tests/virt/test_example.py::test_func \
    --jira CNV-12345 \
    --reason "SSH timeout in CI"

# Check status after
uv run quarantine-helper status
```

### De-Quarantine a Test

```bash
# Verify stability first (25 runs)
pytest --repeat-scope=session --count=25 tests/virt/test_example.py

# Remove marker
uv run quarantine-helper remove \
    tests/virt/test_example.py::test_func
```

### Run Quarantined Tests Only

```bash
pytest --run-quarantined-only --tc-file=tests/global_config.py
```

### Generate Dashboard

```bash
uv run quarantine-dashboard
```

### Analyze Flaky Tests

```bash
uv run flaky-test-analyzer \
    --threshold 5 --days 7 --branch main --check-quarantined
```

---

## Troubleshooting

### "Function not found in file" when using quarantine_helper.py

The test path must use pytest-style `file_path::test_function_name` format. Verify the function name matches exactly (the tool uses AST parsing, not text search).

### ReportPortal not configured warnings

Set the three required environment variables (`REPORTPORTAL_URL`, `REPORTPORTAL_TOKEN`, `REPORTPORTAL_PROJECT`). Without these, cross-referencing and flaky analysis are unavailable but the quarantine helper's `suggest`, `apply`, `remove`, and `status` commands still work.

### ruff format fails after applying a marker

The quarantine helper runs `ruff format` automatically. If formatting fails, it logs a warning but the marker is still applied. Run `ruff format <file>` manually to resolve.

### Test still runs despite quarantine marker

Verify that `run=False` is set in the `xfail` marker. If `--run-quarantined-only` is passed, quarantined tests intentionally run with `run=True` overridden.
