"""Quarantine Helper CLI

Part of the quarantine_tools package. Automates the mechanical steps of
applying and removing quarantine markers (@pytest.mark.xfail with QUARANTINED)
on individual test functions, while providing status summaries via the existing
TestScanner infrastructure.

Usage:
    quarantine-helper suggest
    quarantine-helper apply tests/virt/test_example.py::test_func --jira CNV-12345
    quarantine-helper remove tests/virt/test_example.py::test_func
    quarantine-helper status
"""

from __future__ import annotations

from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from ast import FunctionDef, parse, walk
from collections.abc import Callable
from pathlib import Path
from textwrap import dedent

from pyhelper_utils.shell import run_command
from simple_logger.logger import get_logger

LOGGER = get_logger(name=__name__)

QUARANTINE_IMPORT = "from quarantine_tools.constants import QUARANTINED"
PYTEST_IMPORT = "import pytest"


def parse_test_path(test_path: str) -> tuple[Path, str]:
    """Parse a pytest-style test path into file path and function name.

    Args:
        test_path: Test identifier in "file_path::test_function_name" format.

    Returns:
        Tuple of (resolved file Path, function name).

    Raises:
        ValueError: If the test path format is invalid or the file does not exist.

    """
    if "::" not in test_path:
        raise ValueError(f"Invalid test path format: '{test_path}'. Expected 'file_path::test_function_name'.")

    file_str, function_name = test_path.rsplit("::", maxsplit=1)
    file_path = Path(file_str).resolve()

    if not file_path.exists():
        raise ValueError(f"Test file does not exist: {file_path}")

    if not file_path.suffix == ".py":
        raise ValueError(f"Test file is not a Python file: {file_path}")

    if not function_name:
        raise ValueError("Function name cannot be empty.")

    return file_path, function_name


def find_function_line(content: str, function_name: str) -> int:
    """Find the line number of a test function definition using AST parsing.

    Handles both module-level functions and class-level methods.

    Args:
        content: Full file content as a string.
        function_name: Name of the function to find.

    Returns:
        1-based line number of the function definition.

    Raises:
        ValueError: If the function is not found in the file content.

    """
    tree = parse(source=content)

    for node in walk(tree):
        if isinstance(node, FunctionDef) and node.name == function_name:
            return node.lineno

    raise ValueError(f"Function '{function_name}' not found in file.")


def _get_function_indentation(content: str, function_name: str) -> str:
    """Determine the indentation level of a function definition.

    Args:
        content: Full file content as a string.
        function_name: Name of the function to find indentation for.

    Returns:
        The whitespace prefix of the function definition line.

    """
    line_number = find_function_line(content=content, function_name=function_name)
    lines = content.splitlines()
    func_line = lines[line_number - 1]
    return func_line[: len(func_line) - len(func_line.lstrip())]


def _find_decorator_insert_line(content: str, function_name: str) -> int:
    """Find the line number where a new decorator should be inserted.

    Walks backwards from the function definition to find the start of
    the existing decorator block. The new decorator is inserted before
    all existing decorators for the function.

    Args:
        content: Full file content as a string.
        function_name: Name of the target function.

    Returns:
        1-based line number where the new decorator should be inserted.

    """
    func_line = find_function_line(content=content, function_name=function_name)
    lines = content.splitlines()
    insert_line = func_line

    for line_idx in range(func_line - 2, max(0, func_line - 52) - 1, -1):
        stripped = lines[line_idx].strip()
        if not stripped:
            break
        if stripped.startswith("class "):
            break
        if stripped.startswith("@"):
            insert_line = line_idx + 1
        elif stripped.startswith((")", "(")) or stripped.endswith((",", "(")):
            insert_line = line_idx + 1
        elif stripped.startswith(('"', "'", 'f"', "f'")):
            insert_line = line_idx + 1
        elif stripped.startswith("#"):
            continue
        else:
            break

    return insert_line


def ensure_import(content: str, import_line: str) -> str:
    """Add an import line to file content if not already present.

    Places the import in the appropriate location: standard library imports,
    then third-party imports, then local imports. If the import already exists
    (even as part of a broader import statement), the content is returned unchanged.

    Args:
        content: Full file content as a string.
        import_line: The import statement to add (e.g., "import pytest").

    Returns:
        Updated file content with the import added, or unchanged if already present.

    """
    lines = content.splitlines(keepends=True)

    # Check if import already exists (exact match or as part of broader import)
    module_name = import_line.split()[-1] if import_line.startswith("import ") else None
    from_module = None
    if import_line.startswith("from "):
        parts = import_line.split()
        from_idx = parts.index("from")
        import_idx = parts.index("import")
        from_module = parts[from_idx + 1]
        imported_name = parts[import_idx + 1]

    for line in lines:
        stripped = line.strip()
        if stripped == import_line:
            return content
        # Check if module_name is already imported via "import X" or "from X import ..."
        if module_name and (stripped == f"import {module_name}" or stripped.startswith(f"from {module_name} import ")):
            return content
        # Check if the specific name is already imported from the same module
        if from_module and stripped.startswith(f"from {from_module} import "):
            existing_imports = stripped.split("import ", maxsplit=1)[1]
            existing_names = [name.strip().rstrip(",") for name in existing_imports.split(",")]
            if imported_name in existing_names:
                return content

    # Find the last import line to insert after
    last_import_idx = -1
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")) and not stripped.startswith("from __future__"):
            last_import_idx = idx

    if last_import_idx >= 0:
        lines.insert(last_import_idx + 1, import_line + "\n")
    else:
        # No imports found; insert after module docstring or at top
        insert_at = 0
        in_docstring = False
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('"""') or stripped.startswith("'''"):
                if in_docstring:
                    insert_at = idx + 1
                    break
                elif stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                    insert_at = idx + 1
                    continue
                else:
                    in_docstring = True
            elif not in_docstring and stripped and not stripped.startswith("#"):
                insert_at = idx
                break

        lines.insert(insert_at, import_line + "\n")

    return "".join(lines)


def insert_quarantine_marker(file_path: Path, function_name: str, reason: str, jira_ticket: str) -> None:
    """Insert the xfail+QUARANTINED decorator above a test function.

    Adds the quarantine marker decorator before all existing decorators
    of the target function. Ensures required imports (pytest and QUARANTINED)
    are present. Runs ruff format after modification.

    Args:
        file_path: Path to the test file to modify.
        function_name: Name of the test function to quarantine.
        reason: Human-readable reason for quarantining.
        jira_ticket: Jira ticket identifier (e.g., "CNV-12345").

    Raises:
        ValueError: If the function is not found in the file.

    """
    content = file_path.read_text(encoding="utf-8")

    # Verify function exists
    find_function_line(content=content, function_name=function_name)

    # Ensure required imports
    content = ensure_import(content=content, import_line=PYTEST_IMPORT)
    content = ensure_import(content=content, import_line=QUARANTINE_IMPORT)

    # Determine indentation and insertion point
    indentation = _get_function_indentation(content=content, function_name=function_name)
    insert_line = _find_decorator_insert_line(content=content, function_name=function_name)

    # Build the decorator block
    reason_text = f"{reason}, {jira_ticket}" if reason else jira_ticket
    decorator = dedent(f"""\
        {indentation}@pytest.mark.xfail(
        {indentation}    reason=f"{{QUARANTINED}}: {reason_text}",
        {indentation}    run=False,
        {indentation})
    """)

    # Insert the decorator
    lines = content.splitlines(keepends=True)
    lines.insert(insert_line - 1, decorator)
    content = "".join(lines)

    file_path.write_text(data=content, encoding="utf-8")

    # Format with ruff
    _run_ruff_format(file_path=file_path)

    LOGGER.info(
        "Quarantine marker applied to '%s' in %s (reason: %s, ticket: %s)",
        function_name,
        file_path,
        reason,
        jira_ticket,
    )


def remove_quarantine_marker(file_path: Path, function_name: str) -> None:
    """Remove the xfail+QUARANTINED decorator from above a test function.

    Identifies and removes the multi-line @pytest.mark.xfail decorator that
    contains a QUARANTINED reason marker. Runs ruff format after modification.

    Args:
        file_path: Path to the test file to modify.
        function_name: Name of the test function to de-quarantine.

    Raises:
        ValueError: If the function or quarantine marker is not found.

    """
    content = file_path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)

    func_line = find_function_line(content=content, function_name=function_name)

    # Walk backwards from the function def to find the quarantine decorator
    quarantine_start = -1
    quarantine_end = -1

    for line_idx in range(func_line - 2, max(0, func_line - 52) - 1, -1):
        stripped = lines[line_idx].strip()
        if not stripped:
            break

        if "@pytest.mark.xfail" in stripped:
            if stripped.endswith(")"):
                # Single-line decorator: check for QUARANTINED
                if "QUARANTINED" in stripped:
                    quarantine_start = line_idx
                    quarantine_end = line_idx
                break
            else:
                # Multi-line: scan forward for closing paren
                quarantine_start = line_idx
                for block_idx in range(line_idx + 1, func_line):
                    block_line = lines[block_idx].strip()
                    if block_line == ")" or block_line.endswith(")"):
                        quarantine_end = block_idx
                        break
                # Verify QUARANTINED is in the block
                block_text = "\n".join(lines[quarantine_start : quarantine_end + 1])
                if "QUARANTINED" not in block_text:
                    quarantine_start = -1
                    quarantine_end = -1
                break

        if stripped.startswith(("def ", "class ")) and not stripped.startswith("def " + function_name):
            break

    if quarantine_start < 0 or quarantine_end < 0:
        raise ValueError(f"No quarantine marker found for function '{function_name}' in {file_path}")

    # Remove the decorator lines (inclusive)
    del lines[quarantine_start : quarantine_end + 1]

    content = "".join(lines)
    file_path.write_text(data=content, encoding="utf-8")

    # Format with ruff
    _run_ruff_format(file_path=file_path)

    LOGGER.info("Quarantine marker removed from '%s' in %s", function_name, file_path)


def _run_ruff_format(file_path: Path) -> None:
    """Run ruff format on a file to ensure consistent formatting.

    Args:
        file_path: Path to the file to format.

    """
    success, _, stderr = run_command(
        command=["ruff", "format", str(file_path)],
        check=False,
        verify_stderr=False,
    )
    if not success:
        LOGGER.warning("ruff format failed for %s: %s", file_path, stderr)


def _find_repo_root() -> Path:
    """Find the repository root directory by looking for pyproject.toml.

    Returns:
        Path to the repository root.

    Raises:
        RuntimeError: If the repository root cannot be determined.

    """
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("Cannot find repository root (no pyproject.toml found).")


def _find_tests_dir() -> Path:
    """Find the tests/ directory under the repository root.

    Returns:
        Path to the tests/ directory.

    Raises:
        RuntimeError: If the tests directory does not exist.

    """
    repo_root = _find_repo_root()
    tests_dir = repo_root / "tests"
    if not tests_dir.is_dir():
        raise RuntimeError(f"Tests directory not found: {tests_dir}")
    return tests_dir


def command_suggest(args: Namespace) -> int:
    """List quarantine candidates or show current quarantine summary.

    Falls back to showing currently quarantined tests if ReportPortal
    is not configured.

    Args:
        args: Parsed CLI arguments (unused for this subcommand).

    Returns:
        Exit code: 0 on success, 1 on error.

    """
    try:
        from quarantine_tools.dashboard import TestScanner
    except ImportError:
        LOGGER.error("Cannot import TestScanner. Ensure quarantine_tools is installed.")
        return 1

    try:
        tests_dir = _find_tests_dir()
    except RuntimeError as error:
        LOGGER.error("%s", error)
        return 1

    LOGGER.info("Scanning for quarantine candidates...")
    scanner = TestScanner(tests_dir=tests_dir)
    stats = scanner.scan_all_tests()

    if not stats.quarantined_list:
        LOGGER.info("No quarantined tests found.")
        return 0

    # Display summary table
    print(f"\n{'Currently Quarantined Tests':=^80}")
    print(f"{'Test Name':<50} {'Jira':<15} {'Team':<15}")
    print("-" * 80)

    for test_info in sorted(stats.quarantined_list, key=lambda test: test.category):
        print(f"{test_info.name:<50} {test_info.jira_ticket:<15} {test_info.category:<15}")

    print("-" * 80)
    print(f"Total quarantined: {stats.quarantined_tests}")
    print()

    return 0


def command_apply(args: Namespace) -> int:
    """Apply a quarantine marker to a test function.

    Args:
        args: Parsed CLI arguments containing test_path, jira, and optional reason.

    Returns:
        Exit code: 0 on success, 1 on error.

    """
    try:
        file_path, function_name = parse_test_path(test_path=args.test_path)
    except ValueError as error:
        LOGGER.error("%s", error)
        return 1

    reason = args.reason or ""

    try:
        insert_quarantine_marker(
            file_path=file_path,
            function_name=function_name,
            reason=reason,
            jira_ticket=args.jira,
        )
    except (ValueError, OSError) as error:
        LOGGER.error("Failed to apply quarantine marker: %s", error)
        return 1

    print("\nQuarantine marker applied successfully:")
    print(f"  File:     {file_path}")
    print(f"  Function: {function_name}")
    print(f"  Jira:     {args.jira}")
    if reason:
        print(f"  Reason:   {reason}")
    print()

    return 0


def command_remove(args: Namespace) -> int:
    """Remove a quarantine marker from a test function.

    Args:
        args: Parsed CLI arguments containing test_path.

    Returns:
        Exit code: 0 on success, 1 on error.

    """
    try:
        file_path, function_name = parse_test_path(test_path=args.test_path)
    except ValueError as error:
        LOGGER.error("%s", error)
        return 1

    try:
        remove_quarantine_marker(file_path=file_path, function_name=function_name)
    except (ValueError, OSError) as error:
        LOGGER.error("Failed to remove quarantine marker: %s", error)
        return 1

    print("\nQuarantine marker removed successfully:")
    print(f"  File:     {file_path}")
    print(f"  Function: {function_name}")
    print()

    return 0


def command_status(args: Namespace) -> int:
    """Show quarantine status summary for the local repository.

    Args:
        args: Parsed CLI arguments (unused for this subcommand).

    Returns:
        Exit code: 0 on success, 1 on error.

    """
    try:
        from quarantine_tools.dashboard import TestScanner
    except ImportError:
        LOGGER.error("Cannot import TestScanner. Ensure quarantine_tools is installed.")
        return 1

    try:
        tests_dir = _find_tests_dir()
    except RuntimeError as error:
        LOGGER.error("%s", error)
        return 1

    LOGGER.info("Scanning local repository for quarantine status...")
    scanner = TestScanner(tests_dir=tests_dir)
    stats = scanner.scan_all_tests()

    print(f"\n{'Quarantine Status Summary':=^80}")
    print(f"  Total tests:       {stats.total_tests}")
    print(f"  Active tests:      {stats.active_tests}")
    print(f"  Quarantined tests: {stats.quarantined_tests}")
    print()

    if stats.category_breakdown:
        print(f"{'Team':<25} {'Total':>8} {'Active':>8} {'Quarantined':>12}")
        print("-" * 55)

        for category in sorted(stats.category_breakdown):
            breakdown = stats.category_breakdown[category]
            print(f"{category:<25} {breakdown['total']:>8} {breakdown['active']:>8} {breakdown['quarantined']:>12}")

        print("-" * 55)

    print()
    return 0


def parse_args() -> Namespace:
    """Parse command-line arguments for the quarantine helper.

    Returns:
        Parsed arguments namespace.

    """
    parser = ArgumentParser(
        description="Quarantine helper: automate quarantine/de-quarantine operations on test files.",
        formatter_class=RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", required=True, help="Available commands")

    # suggest subcommand
    subparsers.add_parser(
        "suggest",
        help="List flaky test candidates or show current quarantine summary.",
    )

    # apply subcommand
    apply_parser = subparsers.add_parser(
        "apply",
        help="Apply a quarantine marker to a test function.",
    )
    apply_parser.add_argument(
        "test_path",
        help="Test path in 'file_path::test_function_name' format.",
    )
    apply_parser.add_argument(
        "--jira",
        required=True,
        help="Jira ticket identifier (e.g., CNV-12345).",
    )
    apply_parser.add_argument(
        "--reason",
        default="",
        help="Human-readable reason for quarantining.",
    )

    # remove subcommand
    remove_parser = subparsers.add_parser(
        "remove",
        help="Remove a quarantine marker from a test function.",
    )
    remove_parser.add_argument(
        "test_path",
        help="Test path in 'file_path::test_function_name' format.",
    )

    # status subcommand
    subparsers.add_parser(
        "status",
        help="Show quarantine status summary for the local repository.",
    )

    return parser.parse_args()


def main() -> int:
    """Entry point for the quarantine helper CLI.

    Returns:
        Exit code: 0 on success, 1 on error.

    """
    args = parse_args()

    command_handlers: dict[str, Callable[..., int]] = {
        "suggest": command_suggest,
        "apply": command_apply,
        "remove": command_remove,
        "status": command_status,
    }

    handler = command_handlers.get(args.command)
    if not handler:
        LOGGER.error("Unknown command: %s", args.command)
        return 1

    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
