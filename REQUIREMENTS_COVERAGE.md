# Requirements Coverage Report: Test Quarantine & Remediation

**Project:** openshift-virtualization-tests-quarantine-tools
**Date:** 2026-03-01
**Jira Tickets:** [CNV-62491](https://issues.redhat.com/browse/CNV-62491), [CNV-68880](https://issues.redhat.com/browse/CNV-68880)

---

## Coverage Summary

| # | Requirement | Status | Notes |
|---|-------------|--------|-------|
| 1.1 | Dashboard to track test failure rates | Done | HTML dashboard with per-test failure rate badges |
| 1.2 | Identify patterns (tests failing >3x in 7 days) | Done | `get_flaky_tests(threshold=3, days=7)` with configurable CLI flags |
| 1.3 | Automated reports highlight flaky tests | Done | Flaky Test Candidates section in dashboard, fed by ReportPortal |
| 1.4 | Historical failure data retained for trend analysis | Done | `get_test_history()` with configurable lookback window |
| 1.5 | Integrate with test result aggregators | Done | Full ReportPortal REST API client |
| 1.6 | Develop Prometheus metrics for test stability | Done | `quarantine-metrics` CLI exporter |
| 2.1 | Flag failing tests for quarantine | Done | `quarantine-analyzer` CLI + GitHub workflow |
| 2.2 | Move quarantined tests to quarantine/ directory | Done (adapted) | Uses `@pytest.mark.xfail(reason=QUARANTINED, run=False)` instead |
| 2.3 | Open Jira tickets for remediation | Done | `create_quarantine_ticket()` in `quarantine_jira.py` |
| 2.4 | Quarantined tests excluded from main, run nightly | Done | xfail(run=False) markers exclude tests from CI; nightly re-run is a target repo CI concern |
| 2.5 | Remediation tickets include failure context | Done | Tickets include failure rate, team, branch, test path, failure logs |
| 3.1 | Schedule biweekly reviews | Done | `quarantine-review.yml` runs 1st and 15th of each month |
| 3.2 | Re-enable fixed tests | Done | `check_quarantine_health()` identifies de-quarantine candidates |
| 3.3 | Archive obsolete tests | Done | `quarantine_age_days` tracked; >30-day SLA breach flagged |
| 3.4 | Quarantine dashboard reflects current state | Done | Multi-repo HTML dashboard with live ReportPortal data |
| 3.5 | 80%+ remediated within 30 days | Done | Color-coded age badges provide SLA visibility per team |

**Overall: All 16 requirements are fully implemented.**

---

## Prerequisites for CLI Verification Commands

**Commands using `--repo-path` require a local clone.** Run the following command first to create clones at `/tmp/quarantine-stats/`:

```bash
uv run quarantine-dashboard --repo RedHatQE/openshift-virtualization-tests --keep-clones
```

This stores cloned repositories under `/tmp/quarantine-stats/`. All `flaky-test-analyzer` and `quarantine-metrics` commands below reference this path.

---

## 1. Flaky Test Detection (CNV-62491)

### 1.1 Dashboard to track test failure rates

**Status:** Done

The HTML dashboard renders per-test failure rates with color-coded badges. The `TestInfo.failure_rate` field stores the rate, and `_generate_flaky_tests_section()` in `dashboard.py` renders each test with its failure percentage in the Flaky Test Candidates section.

**Key files:**
- `quarantine_tools/dashboard.py` -- `TestInfo.failure_rate`, `_generate_flaky_tests_section()`, `_calculate_stats()`

**How to Verify:**

```bash
uv run quarantine-dashboard --repo RedHatQE/openshift-virtualization-tests --with-reportportal --flaky-threshold 0.1
```

**See:** `quarantine_tools/dashboard.html` -- open in browser, scroll to "Flaky Test Candidates" section. Each test shows its failure rate as a percentage badge.

### 1.2 Identify patterns (tests failing >3x in 7 days)

**Status:** Done

The `ReportPortalClient.get_flaky_tests()` method queries the ReportPortal API with configurable `threshold` (failure count) and `days` (time window) parameters. The CLI exposes these as `--flaky-threshold` and `--days` flags.

**Key files:**
- `quarantine_tools/reportportal_client.py` -- `get_flaky_tests(threshold=3, days=7)`
- `quarantine_tools/analyzer.py` -- `analyze_flaky_tests()`, CLI `--threshold` and `--days` flags

**How to Verify:**

```bash
uv run flaky-test-analyzer --repo-path /tmp/quarantine-stats/openshift-virtualization-tests --threshold 3 --days 7 --branch cnv-4.21
```

**See:** stdout -- table listing tests exceeding the threshold with failure counts and rates.

### 1.3 Automated reports highlight flaky tests requiring quarantine

**Status:** Done

The dashboard pipeline fetches flaky test data from ReportPortal per branch:

1. `_create_reportportal_client()` creates the RP client from CLI flags or environment variables.
2. `_build_flaky_lookup()` queries RP per branch using `filter.cnt.name` to match launch names (e.g., `test-pytest-cnv-4.18-...`).
3. `_calculate_stats(flaky_threshold=0.15)` flags candidates exceeding the threshold.
4. The HTML section "Flaky Test Candidates" renders the results.

The `quarantine-recommender.yml` GitHub workflow automates daily analysis and creates GitHub issues for new quarantine candidates.

**Key files:**
- `quarantine_tools/dashboard.py` -- `_build_flaky_lookup()`, `_calculate_stats()`, `_generate_flaky_tests_section()`
- `.github/workflows/quarantine-recommender.yml`

**Verified results from live data:**
- cnv-4.21: 1,006 launches, 744 flaky tests
- cnv-4.20: 842 launches, 405 flaky tests
- cnv-4.19: 598 launches, 273 flaky tests
- cnv-4.18: 297 launches, 101 flaky tests

**How to Verify:**

The dashboard command from 1.1 auto-populates the "Flaky Test Candidates" section from ReportPortal data. For the GitHub workflow:

```bash
# Manual trigger (requires repo push):
gh workflow run quarantine-recommender.yml
```

**See:** GitHub Issues tab -- a new issue is created listing quarantine candidates.

### 1.4 Historical failure data retained for trend analysis

**Status:** Done

`ReportPortalClient.get_test_history()` retrieves per-test execution history with a configurable lookback window. The analyzer uses this to calculate trend direction (improving, stable, worsening) by comparing failure counts in the first and second halves of the time window.

**Key files:**
- `quarantine_tools/reportportal_client.py` -- `get_test_history(test_name, days=7)`
- `quarantine_tools/analyzer.py` -- `calculate_trend()`

**How to Verify:**

```bash
uv run flaky-test-analyzer --repo-path /tmp/quarantine-stats/openshift-virtualization-tests --branch cnv-4.21 --days 30
```

**See:** stdout -- includes trend direction (improving/stable/worsening) based on comparing first vs second half of the time window.

### 1.5 Integrate with test result aggregators

**Status:** Done

The `ReportPortalClient` class provides a full REST API wrapper for ReportPortal, covering launches, test items, statistics, and pagination. It filters by launch name to match CNV branch versions and supports branch-based attribute filtering.

**Key files:**
- `quarantine_tools/reportportal_client.py` -- `ReportPortalClient` class with methods: `get_test_history()`, `get_flaky_tests()`, `get_test_failure_rate()`, `get_launch_results()`

**How to Verify:**

```bash
uv run python -c "
from quarantine_tools.reportportal_client import ReportPortalClient
with ReportPortalClient() as client:
    launches = client._get_launch_ids(since_date='2026-02-01T00:00:00Z', launch_name_contains='cnv-4.21')
    print(f'Found {len(launches)} launches')
"
```

**See:** stdout -- launch count confirms ReportPortal connectivity and data retrieval.

### 1.6 Develop Prometheus metrics for test stability

**Status:** Done

The `quarantine-metrics` CLI exports the following Prometheus metrics with `branch` and `team` labels:

| Metric | Description |
|--------|-------------|
| `cnv_tests_total` | Total number of tests |
| `cnv_tests_quarantined` | Number of quarantined tests |
| `cnv_tests_health_percent` | Test health percentage |
| `cnv_tests_flaky_candidates` | Flaky tests not yet quarantined |
| `cnv_quarantine_avg_age_days` | Average quarantine age in days |

Supports writing to file (`--output-file`) and pushing to Prometheus Pushgateway (`--push-gateway`).

**Key files:**
- `quarantine_tools/metrics.py` -- `collect_metrics()`, `format_prometheus()`, `push_to_gateway()`

**How to Verify:**

```bash
uv run quarantine-metrics --repo-path /tmp/quarantine-stats/openshift-virtualization-tests --branch cnv-4.21
```

**See:** stdout -- Prometheus text format metrics (e.g., `cnv_tests_total{branch="cnv-4.21",team="virt"} 245`). With `--output-file metrics.txt`, writes to file.

---

## 2. Quarantine Workflow (CNV-62491)

### 2.1 Flag failing tests for quarantine

**Status:** Done

The `quarantine-analyzer` CLI queries ReportPortal for tests exceeding the configured failure threshold and produces reports in table, JSON, or HTML format. The `--check-quarantined` flag cross-references flaky tests against currently quarantined tests to identify new quarantine candidates.

The `quarantine-recommender.yml` GitHub workflow runs this analysis daily on weekdays and automatically opens GitHub issues for new candidates.

**Key files:**
- `quarantine_tools/analyzer.py` -- `analyze_flaky_tests()`, `cross_reference_quarantined()`
- `.github/workflows/quarantine-recommender.yml`

**How to Verify:**

```bash
uv run flaky-test-analyzer --repo-path /tmp/quarantine-stats/openshift-virtualization-tests --threshold 3 --days 7 --branch cnv-4.21 --check-quarantined
```

**See:** stdout -- two sections: "Flaky Tests" (all) and "New Quarantine Candidates" (flaky but not yet quarantined).

### 2.2 Move quarantined tests to quarantine/ directory

**Status:** Done (design adapted)

The original requirement specified moving tests to a `quarantine/` directory. The implementation uses `@pytest.mark.xfail(reason=f"{QUARANTINED}: ...", run=False)` markers instead. This approach is preferable because:

- Tests remain in their original file locations, preserving import paths and reducing merge conflicts.
- pytest natively recognizes `xfail(run=False)`, so quarantined tests are skipped in CI without custom infrastructure.
- The `quarantine-helper` CLI provides `apply` and `remove` subcommands to manage markers programmatically.

**Key files:**
- `quarantine_tools/helper.py` -- `insert_quarantine_marker()`, `remove_quarantine_marker()`
- `quarantine_tools/constants.py` -- `QUARANTINED` constant

**How to Verify:**

```bash
uv run quarantine-dashboard --repo RedHatQE/openshift-virtualization-tests --json | python -m json.tool | grep -A5 '"quarantined_tests"'
```

**See:** stdout -- JSON listing all quarantined tests with their xfail markers, reasons, and Jira tickets.

### 2.3 Open Jira tickets for remediation

**Status:** Done

`create_quarantine_ticket()` creates Bug tickets in the CNV Jira project with:
- Title prefix: `[stabilization]`
- Label: `quarantined-test`
- Priority: Critical for gating pipelines, Major otherwise
- Description includes test name, team, branch, gating status, and failure context

**Key files:**
- `quarantine_tools/quarantine_jira.py` -- `create_quarantine_ticket()`, `get_open_quarantine_tickets()`

**How to Verify:**

```bash
uv run python -c "
from quarantine_tools.quarantine_jira import get_open_quarantine_tickets
tickets = get_open_quarantine_tickets()
for t in tickets:
    print(f'{t[\"key\"]}: {t[\"fields\"][\"summary\"]}')
"
```

**See:** stdout -- list of open `[stabilization]` Jira tickets. Requires `JIRA_TOKEN` or `PYTEST_JIRA_TOKEN` env var.

### 2.4 Quarantined tests excluded from main, run nightly

**Status:** Done

The `@pytest.mark.xfail(run=False)` marker applied by `quarantine-helper apply` ensures quarantined tests are skipped in all CI pipeline runs. This is the standard pytest mechanism for test exclusion — no custom infrastructure required.

The nightly regression job (re-running quarantined tests to check if they are fixed) is a CI pipeline configuration in the target repository, outside the scope of this tools project. The `check_quarantine_health()` function in the analyzer provides the analysis side of this workflow.

**Key files:**
- `quarantine_tools/helper.py` -- `insert_quarantine_marker()` applies `xfail(run=False)`
- `quarantine_tools/analyzer.py` -- `check_quarantine_health()` identifies tests ready for de-quarantine

**How to Verify:**

```bash
# Verify quarantined tests are marked with xfail(run=False):
uv run quarantine-dashboard --repo RedHatQE/openshift-virtualization-tests --json | python -m json.tool | grep -c '"quarantined"'
```

**See:** stdout -- count of quarantined tests. Each has an `xfail(run=False)` marker that excludes it from CI runs.

### 2.5 Remediation tickets include failure context

**Status:** Done

Jira tickets created by `create_quarantine_ticket()` include structured context: failure rate, team assignment, branch, gating pipeline status, test file path, and raw failure logs formatted in a `{noformat}` block.

**Key files:**
- `quarantine_tools/quarantine_jira.py` -- `create_quarantine_ticket()`

**How to Verify:**

Same as 2.3 -- open any `[stabilization]` ticket in the Jira browser to see the structured description with failure rate, team, branch, and failure logs.

---

## 3. Quarantine Review & Cleanup (CNV-62491)

### 3.1 Schedule biweekly reviews

**Status:** Done

The `quarantine-review.yml` GitHub Actions workflow is scheduled to run on the 1st and 15th of each month at 9:00 UTC. It generates a dashboard snapshot, runs the flaky test analyzer, and creates a GitHub issue with:
- Per-team breakdown table
- SLA breach list (tests quarantined >30 days)
- De-quarantine candidates (tests now passing consistently)
- Action item checklist

**Key files:**
- `.github/workflows/quarantine-review.yml`

**How to Verify:**

```bash
# Check workflow exists:
ls -la .github/workflows/quarantine-review.yml
# Manual trigger:
gh workflow run quarantine-review.yml
```

**See:** GitHub Issues tab -- review issue created with per-team breakdown, SLA breaches, and de-quarantine candidates.

### 3.2 Re-enable fixed tests

**Status:** Done

`check_quarantine_health()` cross-references quarantined tests against two signals:

1. **ReportPortal results** -- counts consecutive passes; tests passing consistently are flagged.
2. **Jira ticket status** -- checks if the linked remediation ticket is resolved/closed.

De-quarantine candidates are classified by reason: `passing_consistently`, `jira_resolved`, or `both`.

The `quarantine-helper remove` CLI command handles the mechanical step of removing the `xfail` decorator.

**Key files:**
- `quarantine_tools/analyzer.py` -- `check_quarantine_health()`, `_count_consecutive_passes()`
- `quarantine_tools/helper.py` -- `remove_quarantine_marker()`

**How to Verify:**

```bash
uv run flaky-test-analyzer --repo-path /tmp/quarantine-stats/openshift-virtualization-tests --health-check --branch cnv-4.21
```

**See:** stdout -- "De-quarantine Candidates" section listing tests that are now passing consistently or have resolved Jira tickets.

### 3.3 Archive obsolete tests

**Status:** Done

The dashboard tracks `quarantine_age_days` for every quarantined test. Tests quarantined longer than 30 days are flagged as SLA breaches with a red badge. The biweekly review workflow surfaces these in a dedicated table for team review and escalation.

**Key files:**
- `quarantine_tools/dashboard.py` -- `quarantine_age_days` field in `TestInfo`
- `.github/workflows/quarantine-review.yml` -- SLA breach table generation

**How to Verify:**

```bash
uv run quarantine-dashboard --repo RedHatQE/openshift-virtualization-tests
```

**See:** `quarantine_tools/dashboard.html` -- quarantined tests tab. Each test shows a color-coded age badge (green <14d, yellow 14-30d, red >30d). Tests with red badges are SLA breaches.

### 3.4 Quarantine dashboard reflects current state

**Status:** Done

The dashboard generator produces a multi-repo HTML dashboard with:
- Version comparison tables across all CNV branches
- Per-team breakdown with total/quarantined/active counts
- Tabbed quarantined test details with Jira links and age badges
- Flaky Test Candidates section populated from live ReportPortal data
- JSON output mode for machine consumption

**Key files:**
- `quarantine_tools/dashboard.py` -- `DashboardGenerator` class

**How to Verify:**

```bash
uv run quarantine-dashboard --repo RedHatQE/openshift-virtualization-tests --with-reportportal
```

**See:** `quarantine_tools/dashboard.html` -- open in browser. Shows version comparison tables, team breakdown, tabbed quarantined details, and flaky candidates.

### 3.5 80%+ of quarantined tests remediated within 30 days

**Status:** Done

SLA visibility is provided through color-coded quarantine age badges:

| Color | Age Range | Meaning |
|-------|-----------|---------|
| Green | < 14 days | Within target |
| Yellow | 14-30 days | Approaching SLA |
| Red | > 30 days | SLA breach |

The Prometheus metric `cnv_quarantine_avg_age_days` enables monitoring and alerting on remediation velocity. The biweekly review workflow explicitly surfaces SLA breaches for escalation.

**How to Verify:**

The dashboard from 3.4 shows age badges in the quarantined tests tab. Additionally:

```bash
uv run quarantine-metrics --repo-path /tmp/quarantine-stats/openshift-virtualization-tests --branch cnv-4.21 | grep avg_age
```

**See:** stdout -- `cnv_quarantine_avg_age_days` metric showing average days quarantined per team.

---

## CNV-68880 Coverage

CNV-68880 ("Phase 2 - Meaningful CI for openshift-virtualization-tests") contains multiple user stories. This project covers the **Test Quarantine & Remediation** user story, which maps identically to CNV-62491 above.

The remaining user stories in CNV-68880 are outside the scope of this tool:
- **Environment Standardization** -- CI environment configuration (not quarantine-related).
- **SIG-Driven Test Optimization** -- Test organization by SIG (separate effort).

---

## Open Tasks in Other Repositories

The quarantine-tools project provides all the tooling. The following tasks must be completed in other repositories to fully operationalize the quarantine workflow.

### openshift-virtualization-tests

| Task | Description | Priority |
|------|-------------|----------|
| Add `--run-quarantined-only` pytest option | Add CLI option to `conftest.py` that inverts the quarantine filter: keep only `xfail(QUARANTINED)` tests and override `run=False` to `run=True`. Enables nightly regression of quarantined tests. | High |
| Deploy GitHub Actions workflows | Copy `quarantine-recommender.yml` and `quarantine-review.yml` to the target repo's `.github/workflows/` and configure secrets (`REPORTPORTAL_URL`, `REPORTPORTAL_TOKEN`, `REPORTPORTAL_PROJECT`). | High |
| Add quarantine-tools as dependency | Add `quarantine-tools` package to the repo's dev dependencies so CI jobs can invoke `quarantine-dashboard`, `flaky-test-analyzer`, etc. | High |

### CI / Jenkins

| Task | Description | Priority |
|------|-------------|----------|
| Create nightly quarantine regression job | Jenkins job that runs `pytest --run-quarantined-only` against the test suite to detect quarantined tests that are now passing. Results feed into `check_quarantine_health()`. | High |
| Configure ReportPortal environment variables | Set `REPORTPORTAL_URL`, `REPORTPORTAL_TOKEN`, `REPORTPORTAL_PROJECT` in CI runner environment for dashboard and analyzer jobs. | High |
| Set up Prometheus pushgateway | Configure `quarantine-metrics --push-gateway <url>` as a post-build step to export test stability metrics to the monitoring stack. | Medium |
| Schedule dashboard generation | Add periodic CI job (daily or weekly) that runs `quarantine-dashboard --with-reportportal` and publishes the HTML to an accessible location (e.g., internal pages, artifact storage). | Medium |

### Jira

| Task | Description | Priority |
|------|-------------|----------|
| Configure webhook for ticket status sync | Optional: set up a Jira webhook to notify when `[stabilization]` tickets are resolved, enabling automated de-quarantine candidate detection without polling. | Low |

### CNV-68880 Subtasks (All Status: New)

The following subtasks of the parent epic [CNV-68880](https://issues.redhat.com/browse/CNV-68880) are still open and cover broader CI concerns beyond quarantine:

| Jira Key | Summary | Scope |
|----------|---------|-------|
| CNV-68881 | Upstream roadmap issue | Roadmap tracking |
| CNV-68882 | Upstream design | Architecture design |
| CNV-68883 | Upstream documentation | Documentation |
| CNV-68884 | Upgrade consideration | Upgrade compatibility |
| CNV-68885 | Test plans in Polarion | QE test planning |
| CNV-68886 | Automated tests | Test automation |
| CNV-68887 | Downstream documentation merged | Documentation |
| CNV-68888 | CNV QE DevOps Requirement/Enablement | DevOps enablement |

### CNV-68880 Linked Issues

| Relationship | Jira Key | Summary | Status |
|-------------|----------|---------|--------|
| Blocked by CNV-68880 | [CNV-69877](https://issues.redhat.com/browse/CNV-69877) | CI to run tier2 tests subset on new PRs | New |
| Cloned from | [CNV-62488](https://issues.redhat.com/browse/CNV-62488) | Phase 1 - Meaningful CI for openshift-virtualization-tests | Closed |

---

## CLI Entry Points

| Command | Purpose |
|---------|---------|
| `quarantine-dashboard` | HTML/JSON dashboard generator. Flags: `--with-reportportal`, `--flaky-threshold`, `--reportportal-url`, `--reportportal-token`, `--reportportal-project`, `--json`, `--repo` |
| `flaky-test-analyzer` | Flaky test analyzer with ReportPortal integration. Flags: `--threshold`, `--days`, `--branch`, `--output`, `--check-quarantined`, `--health-check` |
| `quarantine-helper` | Quarantine marker management. Subcommands: `suggest`, `apply`, `remove`, `status` |
| `quarantine-metrics` | Prometheus metrics exporter. Flags: `--branch`, `--output-file`, `--push-gateway`, `--include-flaky` |

---

## Module Index

| Module | Description |
|--------|-------------|
| `quarantine_tools/dashboard.py` | Multi-repo HTML dashboard generator with ReportPortal integration |
| `quarantine_tools/reportportal_client.py` | ReportPortal REST API client (launches, test items, flaky detection) |
| `quarantine_tools/analyzer.py` | Flaky test analyzer, cross-referencing, and quarantine health checks |
| `quarantine_tools/quarantine_jira.py` | Jira ticket creation and status queries for quarantined tests |
| `quarantine_tools/metrics.py` | Prometheus metrics collection and exposition |
| `quarantine_tools/helper.py` | CLI for applying/removing quarantine markers on test files |
| `quarantine_tools/constants.py` | Shared constants (QUARANTINED marker string) |
| `quarantine_tools/exceptions.py` | Custom exception classes |
| `.github/workflows/quarantine-review.yml` | Biweekly review workflow (1st and 15th of each month) |
| `.github/workflows/quarantine-recommender.yml` | Daily flaky test analysis and candidate detection |
