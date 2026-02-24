# Quarantine Helper

A CLI tool for applying and removing quarantine markers (`@pytest.mark.xfail` with `QUARANTINED`) on individual test functions, and for viewing quarantine status summaries.

## Quick Start

```bash
# View currently quarantined tests
quarantine-helper suggest

# Quarantine a failing test
quarantine-helper apply tests/virt/test_example.py::test_my_function --jira CNV-12345

# Remove quarantine after fixing the root cause
quarantine-helper remove tests/virt/test_example.py::test_my_function

# Show quarantine summary with per-team breakdown
quarantine-helper status
```

## Prerequisites

- **quarantine_tools** package installed (`pip install` or `uv add` from the repository)
- **ruff** available on `PATH` (the tool runs `ruff format` after every file modification)
- Run from the **repository root** (the tool locates `pyproject.toml` to find the repo root and `tests/` directory)

No external service configuration (ReportPortal, Jira API) is required for basic operations. The `suggest` and `status` subcommands use local file scanning only.

## CLI Entry Point

```
quarantine-helper <command> [arguments]
```

Installed as a console script via `pyproject.toml`:

```toml
[project.scripts]
quarantine-helper = "quarantine_tools.helper:main"
```

## Subcommands

### `suggest`

List currently quarantined tests in a summary table. Scans the local `tests/` directory for functions marked with the `QUARANTINED` xfail decorator.

```bash
quarantine-helper suggest
```

**Arguments:** None.

**Output:** A formatted table showing each quarantined test name, its Jira ticket, and its team category.

```
=========================Currently Quarantined Tests===========================
Test Name                                          Jira            Team
--------------------------------------------------------------------------------
test_concurrent_blank_disk_import                  CNV-12345       storage
test_vm_migration_timeout                          CNV-67890       virt
--------------------------------------------------------------------------------
Total quarantined: 2
```

---

### `apply`

Apply a quarantine marker to a test function. This modifies the test file by:

1. Adding `import pytest` if not already present.
2. Adding `from quarantine_tools.constants import QUARANTINED` if not already present.
3. Inserting a multi-line `@pytest.mark.xfail` decorator above the target function (before any existing decorators).
4. Running `ruff format` on the modified file.

The resulting decorator looks like:

```python
@pytest.mark.xfail(
    reason=f"{QUARANTINED}: <reason>, <jira_ticket>",
    run=False,
)
def test_my_function():
    ...
```

Setting `run=False` means pytest skips executing the test body entirely and reports it as an expected failure (xfail).

| Argument | Required | Description |
|---|---|---|
| `test_path` | Yes | Test path in `file_path::test_function_name` format |
| `--jira` | Yes | Jira ticket identifier (e.g., `CNV-12345`) |
| `--reason` | No | Human-readable reason for quarantining (default: empty) |

**Examples:**

```bash
# Quarantine with a Jira ticket only
quarantine-helper apply tests/storage/test_import.py::test_concurrent_blank_disk_import \
    --jira CNV-12345

# Quarantine with a reason and Jira ticket
quarantine-helper apply tests/virt/test_migration.py::test_vm_live_migration \
    --jira CNV-67890 \
    --reason "Intermittent SSH timeout during migration"
```

**Output on success:**

```
Quarantine marker applied successfully:
  File:     /absolute/path/to/tests/virt/test_migration.py
  Function: test_vm_live_migration
  Jira:     CNV-67890
  Reason:   Intermittent SSH timeout during migration
```

---

### `remove`

Remove the quarantine marker from a test function. This modifies the test file by:

1. Locating the `@pytest.mark.xfail` decorator that contains `QUARANTINED` in its reason string.
2. Deleting all lines of that decorator (single-line or multi-line).
3. Running `ruff format` on the modified file.

Existing imports (`pytest` and `QUARANTINED`) are left in place. Other decorators on the function are not affected.

| Argument | Required | Description |
|---|---|---|
| `test_path` | Yes | Test path in `file_path::test_function_name` format |

**Example:**

```bash
quarantine-helper remove tests/storage/test_import.py::test_concurrent_blank_disk_import
```

**Output on success:**

```
Quarantine marker removed successfully:
  File:     /absolute/path/to/tests/storage/test_import.py
  Function: test_concurrent_blank_disk_import
```

**Error case** (no quarantine marker found):

```
Failed to remove quarantine marker: No quarantine marker found for function 'test_concurrent_blank_disk_import' in /path/to/file.py
```

---

### `status`

Show a quarantine status summary for the local repository, including per-team breakdown of total, active, and quarantined test counts.

```bash
quarantine-helper status
```

**Arguments:** None.

**Output:**

```
============================Quarantine Status Summary============================
  Total tests:       1842
  Active tests:      1830
  Quarantined tests: 12

Team                        Total   Active  Quarantined
-------------------------------------------------------
network                       312      310            2
storage                       498      494            4
virt                          724      720            4
compute                       308      306            2
-------------------------------------------------------
```

## Test Path Format

All subcommands that accept a test path use the pytest node ID format:

```
file_path::test_function_name
```

| Component | Description | Example |
|---|---|---|
| `file_path` | Relative or absolute path to the Python test file | `tests/virt/test_migration.py` |
| `::` | Separator (required) | `::` |
| `test_function_name` | Name of the test function (not the class) | `test_vm_live_migration` |

**Valid examples:**

```
tests/virt/test_migration.py::test_vm_live_migration
tests/storage/test_import.py::test_concurrent_blank_disk_import
/home/user/repo/tests/network/test_sriov.py::test_sriov_attach
```

**Invalid examples:**

```
tests/virt/test_migration.py          # Missing :: and function name
test_vm_live_migration                 # Missing file path
tests/virt/test_migration.py::        # Empty function name
tests/virt/not_a_python_file.txt::fn  # Not a .py file
```

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Error (invalid path, function not found, file not found, missing quarantine marker) |

## Troubleshooting

### "Cannot find repository root (no pyproject.toml found)"

Run the command from the repository root directory, or from any subdirectory that has `pyproject.toml` in a parent directory.

### "ruff format failed"

Ensure `ruff` is installed and available on your `PATH`. The tool logs a warning but does not fail if ruff formatting fails; the file modification itself still applies.

### "Function 'test_name' not found in file"

Verify the function name matches exactly. The tool uses Python AST parsing, so the function must be a valid `def` statement in the file. Class methods are supported.

### "No quarantine marker found for function"

The `remove` subcommand only removes decorators that contain `QUARANTINED` in the xfail reason. If the test was quarantined manually with a different pattern, this tool will not detect it.
