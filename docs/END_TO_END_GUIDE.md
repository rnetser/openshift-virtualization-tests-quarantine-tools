# End-to-End Guide: Test Quarantine & Remediation

This guide walks through the complete quarantine lifecycle — from detecting flaky tests to quarantining them, monitoring their status, and de-quarantining when fixes land.

## Prerequisites

### Installation

```bash
# In your test repo
uv add openshift-virtualization-tests-quarantine-tools

# Or install from source
pip install git+https://github.com/rnetser/openshift-virtualization-tests-quarantine-tools.git
```

### Configuration

Add these to your shell profile or CI secrets:

```bash
# ReportPortal (required for flaky detection)
export REPORTPORTAL_URL="https://reportportal.your-company.com"
export REPORTPORTAL_TOKEN="your-api-token"       # RP UI → Profile → API Keys
export REPORTPORTAL_PROJECT="cnv-tests"

# Jira (required for ticket operations)
export JIRA_TOKEN="your-jira-pat"                 # or PYTEST_JIRA_TOKEN
export JIRA_SERVER="https://issues.redhat.com"    # default
export JIRA_PROJECT="CNV"                         # default
```

| Variable | Required For | How to Obtain |
|----------|-------------|---------------|
| `REPORTPORTAL_URL` | Flaky detection, health check | Your ReportPortal instance URL |
| `REPORTPORTAL_TOKEN` | Flaky detection, health check | ReportPortal UI → Profile → API Keys |
| `REPORTPORTAL_PROJECT` | Flaky detection, health check | Project name in ReportPortal |
| `JIRA_TOKEN` | Ticket operations | Jira → Profile → Personal Access Tokens |
| `JIRA_SERVER` | Ticket operations | Default: `https://issues.redhat.com` |
| `JIRA_PROJECT` | Ticket operations | Default: `CNV` |

> **Note:** Tools that don't need ReportPortal or Jira (like `quarantine-helper apply/remove/status` and `quarantine-dashboard` without `--with-reportportal`) work without any environment variables.

---

## Area 1: Flaky Test Detection

**Goal:** Identify which tests are flaky and whether they should be quarantined.

### Find Flaky Tests

```bash
# Find tests that failed 5+ times in the last 7 days
flaky-test-analyzer --threshold 5 --days 7 --branch main
```

Example output:
```
Flaky Test Report (threshold: 5 failures in 7 days)
============================================================
Test Name                              Team       Failures  Rate    Trend
─────────────────────────────────────────────────────────────────────────
test_successful_concurrent_blank_di... storage    12        85.7%   worsening
test_vm_ssh_connectivity              virt       8         44.4%   stable
test_network_policy_isolation         network    6         33.3%   improving
```

The **Trend** column compares the first half vs second half of the time window:
- **worsening** — more failures recently
- **stable** — consistent failure rate
- **improving** — fewer failures recently

### Cross-Reference Against Quarantined Tests

```bash
# Compare flaky tests against what's already quarantined
flaky-test-analyzer --threshold 5 --days 7 --check-quarantined --repo-path .
```

Example output:
```
Cross-Reference Results
========================
Quarantine Candidates (flaky but NOT quarantined):
  test_successful_concurrent_blank_disk_import  storage  12 failures  85.7%

De-Quarantine Candidates (quarantined but now passing):
  test_old_fixed_test                           virt     0 failures in last 14 days
```

This tells you:
- **Quarantine Candidates:** Tests that are flaky but haven't been quarantined yet — action needed
- **De-Quarantine Candidates:** Tests that were quarantined but are now passing — ready to bring back

### Export as JSON (for CI pipelines)

```bash
flaky-test-analyzer --threshold 5 --days 7 --output json > /tmp/flaky_report.json
```

---

## Area 2: Quarantine Workflow

**Goal:** Quarantine a flaky test with proper tracking.

### Step 1: Create a Jira Ticket

Create a ticket in Jira with:
- **Title:** `[stabilization] test_successful_concurrent_blank_disk_import`
- **Label:** `quarantined-test`
- **Description:** Failure context, environment details, link to test file

### Step 2: Apply the Quarantine Marker

```bash
quarantine-helper apply \
  tests/storage/test_import.py::test_successful_concurrent_blank_disk_import \
  --jira CNV-12345 \
  --reason "SSH timeout due to FD leak in concurrent imports"
```

This automatically:
1. Finds the test function in the file using AST parsing
2. Inserts the xfail decorator above all existing decorators:
   ```python
   @pytest.mark.xfail(
       reason=f"{QUARANTINED}: SSH timeout due to FD leak in concurrent imports, CNV-12345",
       run=False,
   )
   ```
3. Ensures `import pytest` and `from quarantine_tools.constants import QUARANTINED` are present
4. Runs `ruff format` on the file

### Step 3: Verify Quarantine Status

```bash
quarantine-helper status
```

```
Quarantine Status
==================
Team           Total    Active   Quarantined
─────────────────────────────────────────────
storage        450      445      5
virt           380      377      3
network        230      229      1
infrastructure 120      120      0
─────────────────────────────────────────────
TOTAL          1180     1171     9
```

### Step 4: View the Dashboard

```bash
# Generate HTML dashboard (opens dashboard.html)
quarantine-dashboard --with-reportportal

# Or JSON for programmatic use
quarantine-dashboard --json > dashboard.json
```

The HTML dashboard includes:
- Summary cards (total tests, quarantined count, health %)
- Color-coded quarantine age (green <14 days, yellow 14-30 days, red >30 days)
- Per-team breakdown with average quarantine age
- Flaky tests tab showing candidates not yet quarantined (when `--with-reportportal` is used)

### Step 5: Export Metrics for Monitoring

```bash
# Print to stdout
quarantine-metrics --branch main --repo-path .

# Push to Prometheus pushgateway
quarantine-metrics --branch main --push-gateway http://pushgateway:9091

# Save to file
quarantine-metrics --branch main --output-file /tmp/metrics.prom
```

Example output:
```
# HELP cnv_tests_total Total number of tests
# TYPE cnv_tests_total gauge
cnv_tests_total{branch="main",team="storage"} 450

# HELP cnv_tests_quarantined Number of quarantined tests
# TYPE cnv_tests_quarantined gauge
cnv_tests_quarantined{branch="main",team="storage"} 5

# HELP cnv_tests_health_percent Test health percentage
# TYPE cnv_tests_health_percent gauge
cnv_tests_health_percent{branch="main",team="storage"} 98.9

# HELP cnv_quarantine_avg_age_days Average quarantine age in days
# TYPE cnv_quarantine_avg_age_days gauge
cnv_quarantine_avg_age_days{branch="main",team="storage"} 12
```

---

## Area 3: Quarantine Review & Cleanup

**Goal:** Monitor quarantined tests and de-quarantine when fixes land.

### Automated Workflows (run on their own)

**Daily** (weekdays at 8 AM UTC): The `quarantine-recommender` GitHub Actions workflow:
1. Queries ReportPortal for flaky tests
2. Creates a GitHub issue listing quarantine candidates
3. Skips if no new candidates or an open issue already exists

**Biweekly** (1st and 15th of each month): The `quarantine-review` workflow:
1. Generates the quarantine dashboard
2. Runs the health check against ReportPortal
3. Creates a review issue with:
   - Tests quarantined >30 days (SLA breaches)
   - Tests now passing consistently (de-quarantine candidates)
   - Tests with resolved Jira tickets
   - Per-team health scores and action items

### Manual Health Check

```bash
# Check which quarantined tests are ready to come back
flaky-test-analyzer --health-check --repo-path .
```

Example output:
```
De-Quarantine Candidates
=========================
Test Name                              Team     Jira       Passes  Jira Status  Reason
──────────────────────────────────────────────────────────────────────────────────────
test_old_fixed_test                    virt     CNV-11111  25      Resolved     both
test_another_fixed                     network  CNV-22222  15      Resolved     both
test_stabilized_recently               storage  CNV-33333  10      Open         passing_consistently
```

The **Reason** column tells you why the test is a candidate:
- **both** — Jira ticket is resolved AND test is passing consistently (strongest signal)
- **jira_resolved** — Jira ticket is resolved but test results are inconclusive
- **passing_consistently** — Test is passing but Jira ticket is still open

### Nightly Regression of Quarantined Tests

In the test repo (requires the `--run-quarantined-only` conftest.py option):

```bash
# Run ONLY quarantined tests to check if fixes work
pytest --run-quarantined-only --tc-file=tests/global_config.py
```

This overrides `run=False` to `run=True`, so quarantined tests actually execute. Results flow back to ReportPortal, feeding the health check detection.

### De-Quarantine a Fixed Test

```bash
quarantine-helper remove tests/virt/test_vm.py::test_old_fixed_test
```

This:
1. Finds the `@pytest.mark.xfail(reason=f"{QUARANTINED}: ...", run=False)` decorator
2. Removes it (handles both single-line and multi-line decorators)
3. Runs `ruff format` on the file

Then close the Jira ticket and commit the change.

---

## The Full Lifecycle

```
                    ┌──────────────────────────┐
                    │   ReportPortal collects   │
                    │   nightly test results    │
                    └────────────┬─────────────┘
                                 │
                                 ▼
              ┌──────────────────────────────────┐
              │  flaky-test-analyzer detects      │
              │  tests failing > threshold        │
              └────────────┬─────────────────────┘
                           │
                           ▼
         ┌─────────────────────────────────────┐
         │  quarantine-recommender workflow     │
         │  creates GitHub issue with candidates│
         └────────────┬────────────────────────┘
                      │
                      ▼  (human reviews and decides)
         ┌─────────────────────────────────────┐
         │  quarantine-helper apply             │
         │  adds xfail marker + Jira ticket    │
         └────────────┬────────────────────────┘
                      │
                      ▼
         ┌─────────────────────────────────────┐
         │  Nightly: pytest --run-quarantined   │
         │  -only checks if fixes work          │
         └────────────┬────────────────────────┘
                      │
                      ▼
         ┌─────────────────────────────────────┐
         │  flaky-test-analyzer --health-check  │
         │  detects passing + resolved Jira     │
         └────────────┬────────────────────────┘
                      │
                      ▼
         ┌─────────────────────────────────────┐
         │  quarantine-review workflow          │
         │  biweekly issue with SLA breaches   │
         └────────────┬────────────────────────┘
                      │
                      ▼  (human reviews and decides)
         ┌─────────────────────────────────────┐
         │  quarantine-helper remove            │
         │  removes xfail, close Jira ticket   │
         └─────────────────────────────────────┘
```

**Key principle:** Every step is recommend-only. Humans make the quarantine and de-quarantine decisions. The tools automate detection, file manipulation, and reporting.

---

## SLA Guidelines

| Quarantine Age | Status | Required Action |
|----------------|--------|-----------------|
| < 14 days | Green | Active investigation in progress |
| 14-30 days | Yellow | Escalate to team lead |
| > 30 days | Red | SLA breach — require remediation plan or permanent skip decision |

---

## Quick Command Reference

| Task | Command |
|------|---------|
| Find flaky tests | `flaky-test-analyzer --threshold 5 --days 7` |
| Cross-reference | `flaky-test-analyzer --check-quarantined --repo-path .` |
| Health check | `flaky-test-analyzer --health-check --repo-path .` |
| Quarantine a test | `quarantine-helper apply <path>::<test> --jira CNV-XXXXX` |
| De-quarantine | `quarantine-helper remove <path>::<test>` |
| View status | `quarantine-helper status` |
| HTML dashboard | `quarantine-dashboard --with-reportportal` |
| JSON dashboard | `quarantine-dashboard --json` |
| Prometheus metrics | `quarantine-metrics --branch main` |
| Nightly regression | `pytest --run-quarantined-only` |
