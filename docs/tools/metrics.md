# Prometheus Metrics Exporter

A CLI tool that exports quarantine statistics in Prometheus text exposition format. Designed to run as a Jenkins post-build step, write metrics to a file for node_exporter's textfile collector, or push directly to a Prometheus Pushgateway.

## Quick Start

```bash
# Print metrics to stdout (default branch: main)
quarantine-metrics

# Print metrics for a specific branch
quarantine-metrics --branch release-4.18

# Write metrics to a file
quarantine-metrics --output-file /tmp/metrics.prom

# Push metrics to a Pushgateway
quarantine-metrics --push-gateway http://pushgateway.example.com:9091
```

## Prerequisites

- **quarantine_tools** package installed
- A local clone of the test repository containing a `tests/` directory
- **ReportPortal environment variables** (only when using `--include-flaky`):

| Variable | Description |
|---|---|
| `REPORTPORTAL_URL` | Base URL of the ReportPortal instance |
| `REPORTPORTAL_TOKEN` | API bearer token for authentication |
| `REPORTPORTAL_PROJECT` | ReportPortal project name |

For standard metrics (total, quarantined, health, age), no external configuration is needed. The tool scans local test files only.

## CLI Entry Point

```
quarantine-metrics [arguments]
```

Installed as a console script via `pyproject.toml`:

```toml
[project.scripts]
quarantine-metrics = "quarantine_tools.metrics:main"
```

## CLI Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `--branch` | No | `main` | Branch name used as a Prometheus label value |
| `--repo-path` | No | Current directory | Path to the local repository root |
| `--output-file` | No | None (stdout) | Write metrics to this file instead of printing to stdout |
| `--push-gateway` | No | None | URL of a Prometheus Pushgateway to push metrics to |
| `--job-name` | No | `cnv_quarantine_stats` | Job name for the Pushgateway grouping key |
| `--include-flaky` | No | `false` | Include flaky test candidate metrics (requires ReportPortal) |

## Exported Metrics

All metrics are gauges and include `branch` and `team` labels. The `team` value comes from the test category breakdown produced by the local `TestScanner`.

| Metric Name | Labels | Description |
|---|---|---|
| `cnv_tests_total` | `branch`, `team` | Total number of tests for this team |
| `cnv_tests_quarantined` | `branch`, `team` | Number of quarantined tests for this team |
| `cnv_tests_health_percent` | `branch`, `team` | Test health percentage: `(total - quarantined) / total * 100` |
| `cnv_quarantine_avg_age_days` | `branch`, `team` | Average age of quarantined tests in days |
| `cnv_tests_flaky_candidates` | `branch`, `team` | Number of flaky tests not yet quarantined (only with `--include-flaky`) |

### Example Output

```
# HELP cnv_tests_total Total number of tests
# TYPE cnv_tests_total gauge
cnv_tests_total{branch="main",team="network"} 312
cnv_tests_total{branch="main",team="storage"} 498
cnv_tests_total{branch="main",team="virt"} 724

# HELP cnv_tests_quarantined Number of quarantined tests
# TYPE cnv_tests_quarantined gauge
cnv_tests_quarantined{branch="main",team="network"} 2
cnv_tests_quarantined{branch="main",team="storage"} 4
cnv_tests_quarantined{branch="main",team="virt"} 4

# HELP cnv_tests_health_percent Test health percentage (non-quarantined / total * 100)
# TYPE cnv_tests_health_percent gauge
cnv_tests_health_percent{branch="main",team="network"} 99.4
cnv_tests_health_percent{branch="main",team="storage"} 99.2
cnv_tests_health_percent{branch="main",team="virt"} 99.4

# HELP cnv_quarantine_avg_age_days Average age of quarantined tests in days
# TYPE cnv_quarantine_avg_age_days gauge
cnv_quarantine_avg_age_days{branch="main",team="network"} 12.5
cnv_quarantine_avg_age_days{branch="main",team="storage"} 34.2
cnv_quarantine_avg_age_days{branch="main",team="virt"} 8.0
```

## Output Modes

### Stdout (default)

Print metrics directly to standard output. Useful for quick inspection or piping to other tools.

```bash
quarantine-metrics --branch main
```

### File

Write metrics to a file. The tool creates parent directories if they do not exist. This is the typical mode for integration with Prometheus `node_exporter`'s textfile collector.

```bash
quarantine-metrics --output-file /var/lib/prometheus/node-exporter/quarantine.prom
```

### Pushgateway

Push metrics to a Prometheus Pushgateway via HTTP POST. The endpoint is constructed as `<gateway_url>/metrics/job/<job_name>`. Can be combined with `--output-file` to both write a file and push.

```bash
quarantine-metrics --push-gateway http://pushgateway:9091

# With a custom job name
quarantine-metrics --push-gateway http://pushgateway:9091 --job-name my_custom_job
```

The push uses `Content-Type: text/plain; version=0.0.4; charset=utf-8` and has a 30-second timeout. On failure, the tool logs an error but does not exit with a non-zero code, so subsequent CI steps are not blocked.

## Flaky Test Candidates

When `--include-flaky` is set, the tool queries ReportPortal for tests with a high flaky rate that are not yet quarantined. This requires three environment variables to be set:

```bash
export REPORTPORTAL_URL="https://reportportal.example.com"
export REPORTPORTAL_TOKEN="your-api-token"
export REPORTPORTAL_PROJECT="cnv-tests"

quarantine-metrics --include-flaky
```

If the environment variables are missing, the tool logs a warning and skips flaky metrics without failing.

## Jenkins Integration

Add a post-build step to export quarantine metrics after each test run.

### Pipeline Example

```groovy
pipeline {
    agent any
    stages {
        stage('Run Tests') {
            steps {
                sh 'uv run pytest tests/'
            }
        }
    }
    post {
        always {
            sh """
                quarantine-metrics \
                    --branch ${env.BRANCH_NAME} \
                    --repo-path ${env.WORKSPACE} \
                    --push-gateway http://pushgateway.internal:9091
            """
        }
    }
}
```

### Freestyle Job

Add an "Execute shell" post-build step:

```bash
quarantine-metrics \
    --branch "$GIT_BRANCH" \
    --repo-path "$WORKSPACE" \
    --output-file /var/lib/prometheus/node-exporter/quarantine.prom
```

### CI Best Practices

- Run metrics export in the `post.always` block so it executes regardless of test pass/fail.
- Use `--push-gateway` for centralized metric collection across multiple Jenkins agents.
- Use `--output-file` when the Jenkins agent runs a local Prometheus node_exporter.
- Set `--branch` to `${env.BRANCH_NAME}` or `$GIT_BRANCH` to track metrics per branch.

## Troubleshooting

### "Tests directory not found"

The tool looks for a `tests/` directory under `--repo-path` (or the current directory). Make sure you are running from the repository root or pass the correct `--repo-path`.

### "ReportPortal not configured" warning

This warning appears when `--include-flaky` is used without setting all three environment variables (`REPORTPORTAL_URL`, `REPORTPORTAL_TOKEN`, `REPORTPORTAL_PROJECT`). The tool continues and exports the standard metrics without flaky candidates.

### "Failed to push metrics to Pushgateway"

Check that the Pushgateway URL is reachable and the `/metrics/job/<job_name>` endpoint accepts POST requests. The tool logs the error details and continues without failing the process.
