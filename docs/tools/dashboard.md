# Quarantine Dashboard

The Quarantine Dashboard scans OpenShift Virtualization test repositories for quarantined tests and generates an HTML dashboard or JSON output with statistics broken down by CNV version and team. It clones and scans multiple repositories and branches automatically.

## Quick Start

Generate an HTML dashboard scanning both default repositories across all valid branches:

```bash
quarantine-dashboard
```

This clones the repositories into a temporary directory, scans all `main` and `cnv-X.Y` branches, generates `dashboard.html`, and cleans up the cloned repos.

## Prerequisites

### GitHub Access (required)

The tool clones Git repositories. For public repositories, no authentication is needed. For private repositories, provide a GitHub personal access token through a CLI flag or environment variable.

| Configuration      | Source                                         | Required |
|--------------------|------------------------------------------------|----------|
| GitHub token (CLI) | `--github-token <token>`                       | No       |
| GitHub token (env) | `GITHUB_TOKEN` environment variable            | No       |

The CLI flag takes precedence over the environment variable.

### ReportPortal Access (optional, for `--with-reportportal`)

When `--with-reportportal` is enabled, the dashboard enriches test data with failure rates and flaky test detection from ReportPortal. This requires the following environment variables:

| Variable               | Description               | Required When                    |
|------------------------|---------------------------|----------------------------------|
| `REPORTPORTAL_URL`     | ReportPortal server URL   | `--with-reportportal` is used    |
| `REPORTPORTAL_TOKEN`   | ReportPortal API token    | `--with-reportportal` is used    |
| `REPORTPORTAL_PROJECT` | ReportPortal project name | `--with-reportportal` is used    |

### System Requirements

- Git must be installed and available on `PATH` (used to clone repositories and list branches).
- Sufficient disk space in the temporary directory for cloned repositories.

## CLI Arguments

| Argument              | Type   | Default                             | Description                                                                                         |
|-----------------------|--------|-------------------------------------|-----------------------------------------------------------------------------------------------------|
| `--keep-clones`       | flag   | off                                 | Keep cloned repositories after completion. By default, the working directory is removed on exit.     |
| `--json`              | flag   | off                                 | Output JSON instead of an HTML dashboard. The output file is named `dashboard.json`.                |
| `--workdir`           | path   | `<tmpdir>/quarantine-stats`         | Directory to clone repositories into. Uses the system temp directory by default.                     |
| `--output-dir`        | path   | script directory                    | Directory to save output files (`dashboard.html` or `dashboard.json`).                              |
| `--github-token`      | string | `GITHUB_TOKEN` env var              | GitHub personal access token for cloning private repositories.                                      |
| `--with-reportportal` | flag   | off                                 | Include ReportPortal data (failure rates, flaky test candidates) in the dashboard. Requires `REPORTPORTAL_*` env vars. |

## Scanned Repositories

The tool scans two hardcoded repositories:

| Repository                                    | Minimum Branch Version |
|-----------------------------------------------|------------------------|
| `RedHatQE/openshift-virtualization-tests`     | All `cnv-X.Y` branches |
| `RedHatQE/cnv-tests`                          | `cnv-4.14` and higher  |

Both repositories are scanned on the `main` branch and all branches matching the `cnv-X.Y` pattern.

### Excluded Folders (cnv-tests only)

The following folders are excluded from scanning in the `cnv-tests` repository: `ansible-module`, `ci`, `ci_tests`, `csv`, `security`, `vmimport`.

## Output Files

### HTML Dashboard (default)

Generates a self-contained HTML file with embedded CSS. The dashboard includes:

- **Summary cards** -- total tests, active tests, quarantined tests.
- **Progress bar** -- visual representation of quarantine ratio.
- **Version comparison table** -- quarantine counts across all scanned branches for each repository.
- **Team breakdown table** -- quarantined test counts per team across all repository/version combinations.
- **Quarantined test details** -- full list of quarantined tests with file path, line number, Jira ticket link, quarantine reason, and age in days.
- **Flaky tests section** (only with `--with-reportportal`) -- tab showing flaky test candidates detected through ReportPortal.

```bash
# Generate HTML dashboard (default)
quarantine-dashboard

# Save to a specific directory
quarantine-dashboard --output-dir /path/to/reports
```

Output file: `<output-dir>/dashboard.html`

### JSON Output (`--json`)

Generates a machine-readable JSON file containing all scan results.

```bash
quarantine-dashboard --json
```

Output file: `<output-dir>/dashboard.json`

## The `--with-reportportal` Flag

When this flag is set, the dashboard generator fetches additional data from ReportPortal after scanning the repositories:

- **Failure rates** for quarantined tests, displayed as an additional column.
- **Flaky test candidates** section in the HTML dashboard, identifying tests that may need quarantining.

Without this flag, the dashboard shows only data from static code analysis (marker detection in test files). ReportPortal environment variables (`REPORTPORTAL_URL`, `REPORTPORTAL_TOKEN`, `REPORTPORTAL_PROJECT`) must be set when using this flag.

```bash
# Dashboard with ReportPortal integration
export REPORTPORTAL_URL=https://rp.example.com
export REPORTPORTAL_TOKEN=my-token
export REPORTPORTAL_PROJECT=my-project
quarantine-dashboard --with-reportportal
```

## Example Commands

```bash
# Scan both repos, generate HTML dashboard (default behavior)
quarantine-dashboard

# Generate JSON output instead of HTML
quarantine-dashboard --json

# Keep cloned repos for inspection after completion
quarantine-dashboard --keep-clones

# Use a custom working directory for clones
quarantine-dashboard --workdir /data/clones

# Save output to a specific directory
quarantine-dashboard --output-dir /var/www/reports

# Authenticate for private repositories
quarantine-dashboard --github-token ghp_abc123

# Full example with ReportPortal integration
quarantine-dashboard --with-reportportal --output-dir /var/www/reports

# JSON output with ReportPortal data, keep clones for debugging
quarantine-dashboard --json --with-reportportal --keep-clones --workdir /tmp/debug-clones
```

## How It Works

1. **Clone** -- Clones each repository into the working directory (or updates if already present).
2. **Branch discovery** -- Lists all remote branches and filters to `main` and `cnv-X.Y` patterns, applying per-repo minimum version rules.
3. **Scan** -- For each branch, checks out the branch and scans all Python test files under `tests/`. Detects quarantine markers, extracts Jira ticket references, and calculates quarantine age.
4. **Aggregate** -- Collects statistics per team per version per repository.
5. **Generate** -- Produces the HTML dashboard or JSON output.
6. **Cleanup** -- Removes the cloned repositories unless `--keep-clones` is specified.

## Team Assignment

Tests are assigned to teams based on the first directory component under `tests/` in the file path. For the `cnv-tests` repository, certain folders are mapped to the `install_upgrade_operators` team: `must-gather`, `must_gather`, `product_uninstall`, `product_upgrade`.

## Troubleshooting

**"No repositories could be scanned" error**

- Verify network access to GitHub.
- For private repositories, ensure a valid GitHub token is provided via `--github-token` or the `GITHUB_TOKEN` environment variable.
- Check that Git is installed and available on `PATH`.

**Empty dashboard (no quarantined tests found)**

- Verify that the repositories contain test files with quarantine markers under `tests/`.
- Check the log output for branch scanning details.

**ReportPortal data not appearing**

- Confirm the `--with-reportportal` flag is set.
- Verify all three `REPORTPORTAL_*` environment variables are set and correct.

**Disk space issues**

- The tool clones full repositories into the working directory. Use `--workdir` to specify a location with sufficient space.
- Clones are removed automatically unless `--keep-clones` is set.

**Stale data from previous runs**

- The default working directory is `<tmpdir>/quarantine-stats`. If `--keep-clones` was used in a previous run, old clones may persist. Delete the working directory manually or run without `--keep-clones` to ensure fresh clones.
