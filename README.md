# OpenShift Virtualization Tests — Quarantine Tools

Test quarantine management tools for OpenShift Virtualization test suites. Provides flaky test detection, quarantine workflow automation, and periodic review via ReportPortal and Jira integration.

## Features

- **Flaky Test Detection** — Query ReportPortal for tests exceeding failure thresholds
- **Quarantine Dashboard** — HTML dashboard with quarantine age, failure rates, and team breakdown
- **Quarantine Helper CLI** — Apply/remove quarantine markers, suggest candidates
- **Prometheus Metrics** — Export quarantine health metrics for monitoring
- **GitHub Actions Workflows** — Automated daily recommendations and biweekly reviews
- **Jira Integration** — Create and track quarantine stabilization tickets

## Installation

```bash
uv add openshift-virtualization-tests-quarantine-tools
```

Or install from source:

```bash
git clone https://github.com/rnetser/openshift-virtualization-tests-quarantine-tools.git
cd openshift-virtualization-tests-quarantine-tools
uv sync
```

## CLI Tools

```bash
# Analyze flaky tests
flaky-test-analyzer --threshold 5 --days 7 --branch main

# Cross-reference against quarantined tests
flaky-test-analyzer --check-quarantined --repo-path /path/to/test-repo

# Check quarantine health (de-quarantine candidates)
flaky-test-analyzer --health-check --repo-path /path/to/test-repo

# Quarantine helper
quarantine-helper suggest
quarantine-helper apply tests/virt/test_example.py::test_func --jira CNV-12345
quarantine-helper remove tests/virt/test_example.py::test_func
quarantine-helper status

# Generate quarantine dashboard (scans default repos)
quarantine-dashboard

# Scan a specific repo
quarantine-dashboard --repo RedHatQE/openshift-virtualization-tests

# Scan multiple repos
quarantine-dashboard --repo RedHatQE/openshift-virtualization-tests --repo RedHatQE/cnv-tests

# Include ReportPortal flaky test data
quarantine-dashboard --with-reportportal --flaky-threshold 0.1

# Pass ReportPortal credentials via CLI (alternative to env vars)
quarantine-dashboard --with-reportportal \
  --reportportal-url https://reportportal.example.com \
  --reportportal-token <token> \
  --reportportal-project <project>

# Output as JSON
quarantine-dashboard --json

# Keep cloned repos for other tools to use
quarantine-dashboard --keep-clones

# Export Prometheus metrics
quarantine-metrics --repo-path /path/to/test-repo --branch main
quarantine-metrics --repo-path /path/to/test-repo --branch cnv-4.21 --include-flaky
quarantine-metrics --push-gateway http://pushgateway:9091
```

**Note:** `quarantine-dashboard` uses `--repo ORG/NAME` to clone remote repositories. Other tools (`flaky-test-analyzer`, `quarantine-metrics`) use `--repo-path` for local paths. Run `quarantine-dashboard --keep-clones` to create local clones at `/tmp/quarantine-stats/` for use with the other tools.

## Configuration

| Variable | CLI Flag | Description | Default |
|----------|----------|-------------|---------|
| `REPORTPORTAL_URL` | `--reportportal-url` | ReportPortal server URL | — |
| `REPORTPORTAL_TOKEN` | `--reportportal-token` | ReportPortal API token | — |
| `REPORTPORTAL_PROJECT` | `--reportportal-project` | ReportPortal project name | — |
| `JIRA_TOKEN` | — | Jira API token | Falls back to `PYTEST_JIRA_TOKEN` |
| `JIRA_SERVER` | — | Jira server URL | `https://issues.redhat.com` |
| `JIRA_PROJECT` | — | Jira project key | `CNV` |
| `GITHUB_TOKEN` | `--github-token` | GitHub token for private repos | — |

## Documentation

See [Quarantine Guidelines](docs/QUARANTINE_GUIDELINES.md) for the full guide.

## License

Apache License 2.0
