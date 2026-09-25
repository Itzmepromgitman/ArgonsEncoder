# Developed by ARGON telegram: @REACTIVEARGON
import ast
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

# Configuration
IGNORE_DIRS = {
    "venv",
    "__pycache__",
    ".git",
    "node_modules",
    ".pytest_cache",
    "build",
    "dist",
}
FLAKE8_IGNORE = "E501,W503,E302,E305,W291,E303"


def short_path(path, base):
    """Convert absolute path to relative path for cleaner output."""
    try:
        return str(Path(path).relative_to(base))
    except ValueError:
        return str(path)


def is_file_safe_to_process(file_path):
    """Check if a file is safe to process (valid Python syntax and encoding)."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Skip empty files
        if not content.strip():
            return False, "Empty file"

        # Try to parse with AST
        ast.parse(content, filename=file_path)
        return True, None

    except SyntaxError as e:
        return False, f"Syntax error: {e.msg} at line {e.lineno}"
    except UnicodeDecodeError as e:
        return False, f"Encoding error: {e}"
    except Exception as e:
        return False, f"Parse error: {e}"


def get_safe_python_files(repo_path):
    """Get list of Python files that are safe to process."""
    safe_files = []
    problematic_files = []

    for py_file in Path(repo_path).rglob("*.py"):
        if any(part in IGNORE_DIRS for part in py_file.parts):
            continue

        is_safe, error_msg = is_file_safe_to_process(py_file)
        if is_safe:
            safe_files.append(py_file)
        else:
            problematic_files.append((py_file, error_msg))

    return safe_files, problematic_files


def auto_fix_code():
    """Auto-fix code with various formatters, handling problematic files."""
    print("🔧 Auto-fixing code with autopep8, autoflake, isort, black...\n")

    # First, identify safe files
    safe_files, problematic_files = get_safe_python_files(".")

    if problematic_files:
        print("⚠️  Skipping problematic files:")
        for file_path, error in problematic_files:
            print(f"   {short_path(file_path, Path('.'))} - {error}")
        print()

    if not safe_files:
        print("❌ No safe Python files found to process.")
        return

    # Create temporary file list for tools that support file lists
    file_list = [str(f) for f in safe_files]

    tools = [
        # Process only safe files for autopep8
        (
            ["autopep8", "--in-place", "--aggressive", "--aggressive"] + file_list[:10],
            "autopep8",
        ),  # Limit batch size
        (
            [
                "autoflake",
                "--in-place",
                "--remove-all-unused-imports",
                "--remove-unused-variables",
                "--ignore-init-module-imports",
            ]
            + file_list,
            "autoflake",
        ),
        (["isort"] + file_list, "isort"),
        (["black"] + file_list, "black"),
    ]

    for cmd, tool_name in tools:
        try:
            # Process files in smaller batches to avoid command line length
            # issues
            batch_size = 20
            # Get file list from command
            all_files = cmd[cmd.index(file_list[0]) :]
            base_cmd = cmd[: cmd.index(file_list[0])]  # Get base command

            for i in range(0, len(all_files), batch_size):
                batch = all_files[i : i + batch_size]
                batch_cmd = base_cmd + batch

                result = subprocess.run(
                    batch_cmd, check=True, capture_output=True, text=True, timeout=60
                )

            print(f"✅ {tool_name} completed successfully")

        except subprocess.TimeoutExpired:
            print(f"⚠️  {tool_name} timed out - some files may be too large")
        except subprocess.CalledProcessError as e:
            print(f"⚠️  {tool_name} failed on some files")
            if e.stderr and len(e.stderr) < 500:  # Only show short error messages
                print(f"Error: {e.stderr.strip()}")
        except FileNotFoundError:
            print(f"❌ {tool_name} not found. Install with: pip install {tool_name}")
        except Exception as e:
            print(f"⚠️  {tool_name} error: {str(e)[:200]}...")

    print("✅ Auto-fix completed.\n")


def check_syntax(file_path, base_path):
    """Check Python syntax using AST parsing."""
    is_safe, error_msg = is_file_safe_to_process(file_path)
    if not is_safe:
        return f"[SyntaxError] {short_path(file_path, base_path)} - {error_msg}"
    return None


def check_imports(file_path, base_path):
    """Check if imports are valid and available."""
    errors = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        if not content.strip():
            return []

        tree = ast.parse(content, filename=file_path)
    except Exception as e:
        return [
            f"[ImportCheck] {short_path(file_path, base_path)} - Failed to parse: {e}"
        ]

    short = short_path(file_path, base_path)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not module_exists(alias.name, base_path):
                    errors.append(
                        f"[ImportError] {short}:{node.lineno} - Cannot import '{alias.name}'"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module is None and node.level == 0:
                continue

            module = node.module or ""
            level = node.level

            try:
                full_module = resolve_relative_import(
                    file_path, base_path, level, module
                )
                if full_module and not module_exists(full_module, base_path):
                    errors.append(
                        f"[ImportError] {short}:{node.lineno} - Cannot import from '{full_module}'"
                    )
            except Exception:
                # Skip problematic import resolution
                pass

    return errors


def resolve_relative_import(file_path, base_path, level, module):
    """Resolve relative imports to absolute module names."""
    if level == 0:
        return module

    try:
        rel_path = Path(file_path).relative_to(base_path)
        parent_parts = rel_path.parts[:-1]

        if level > len(parent_parts):
            return None

        target_parts = parent_parts[:-level] if level > 0 else parent_parts

        if module:
            parts = target_parts + tuple(module.split("."))
        else:
            parts = target_parts

        return ".".join(parts) if parts else None
    except Exception:
        return None


def module_exists(module_name, base_path):
    """Check if a module exists and can be imported."""
    if not module_name:
        return False

    try:
        base_str = str(base_path)
        if base_str not in sys.path:
            sys.path.insert(0, base_str)

        spec = importlib.util.find_spec(module_name)
        return spec is not None
    except Exception:
        return False


def check_with_flake8_safe(repo_path, safe_files):
    """Run flake8 only on safe files to avoid crashes."""
    if not safe_files:
        return []

    try:
        # Process files in batches to avoid crashes
        errors = []
        batch_size = 10

        for i in range(0, len(safe_files), batch_size):
            batch = safe_files[i : i + batch_size]
            file_args = [str(f) for f in batch]

            result = subprocess.run(
                [
                    "flake8",
                    f"--ignore={FLAKE8_IGNORE}",
                    "--max-line-length=88",
                    "--jobs=1",
                ]
                + file_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )

            if result.stdout.strip():
                for line in result.stdout.strip().splitlines():
                    if line.strip():
                        cleaned = clean_flake8_line(line, repo_path)
                        if cleaned:
                            errors.append(cleaned)

        return errors

    except subprocess.TimeoutExpired:
        return ["[flake8] WARNING - Timeout during analysis"]
    except FileNotFoundError:
        return ["[flake8] ERROR - flake8 not installed. Run 'pip install flake8'."]
    except Exception as e:
        return [f"[flake8] WARNING - Analysis incomplete: {str(e)[:100]}..."]


def clean_flake8_line(line, base_path):
    """Clean and format flake8 output."""
    if not line.strip():
        return None

    parts = line.split(":", 3)
    if len(parts) >= 4:
        file_path, lineno, colno, msg = parts
        short = short_path(file_path, base_path)
        clean_msg = re.sub(r"^\s*[A-Z]\d+\s+", "", msg).strip()
        return f"[flake8] {short}:{lineno}:{colno} - {clean_msg}"
    elif len(parts) >= 3:
        file_path, lineno, msg = parts
        short = short_path(file_path, base_path)
        clean_msg = re.sub(r"^\s*[A-Z]\d+\s+", "", msg).strip()
        return f"[flake8] {short}:{lineno} - {clean_msg}"

    return line.strip()


def fix_fstrings_without_placeholders(repo_path):
    """Remove f-prefix from strings that don't use f-string features."""
    print("🔧 Fixing f-strings without placeholders...\n")

    safe_files, problematic_files = get_safe_python_files(repo_path)
    fixed_count = 0

    for py_file in safe_files:
        try:
            content = py_file.read_text(encoding="utf-8")
            original_content = content

            # Simple regex for f-strings without placeholders
            pattern = r'\bf(["\'])([^"\']*?)\1'

            def replace_fstring(match):
                quote = match.group(1)
                content_str = match.group(2)
                # Only remove f if no braces and no backslashes
                if (
                    "{" not in content_str
                    and "}" not in content_str
                    and "\\" not in content_str
                ):
                    return f"{quote}{content_str}{quote}"
                return match.group(0)

            content = re.sub(pattern, replace_fstring, content)

            if content != original_content:
                py_file.write_text(content, encoding="utf-8")
                print(f"Fixed f-strings in: {py_file.relative_to(repo_path)}")
                fixed_count += 1

        except Exception as e:
            print(f"⚠️  Error processing {py_file.relative_to(repo_path)}: {e}")

    print(f"✅ f-string fix completed. Fixed {fixed_count} files.\n")


def scan_repo(repo_path):
    """Main scanning function with better error handling."""
    repo_path = Path(repo_path).resolve()

    if not repo_path.exists():
        print(f"❌ Path does not exist: {repo_path}")
        sys.exit(1)

    print(f"🔍 Scanning Python files in: {repo_path.name}/\n")

    # Get safe and problematic files
    safe_files, problematic_files = get_safe_python_files(repo_path)

    if problematic_files:
        print(f"⚠️  Found {len(problematic_files)} problematic files (skipping):")
        for file_path, error in problematic_files:
            print(f"   {short_path(file_path, repo_path)} - {error}")
        print()

    if not safe_files:
        print("❌ No safe Python files found to scan.")
        return

    print(f"✅ Found {len(safe_files)} safe Python files to scan.\n")

    errors = []

    # Check safe files only
    for py_file in safe_files:
        # Import check (syntax already verified)
        import_errors = check_imports(py_file, repo_path)
        errors.extend(import_errors)

    # Run flake8 on safe files only
    print("Running flake8 analysis on safe files...")
    flake8_errors = check_with_flake8_safe(repo_path, safe_files)
    errors.extend(flake8_errors)

    # Report results
    print(f"\n📊 Scanned {len(safe_files)} safe files")

    if problematic_files:
        print(f"⚠️  Skipped {len(problematic_files)} problematic files")

    if errors:
        print(f"\n❌ Found {len(errors)} issues in safe files:\n")
        for err in errors:
            print(f"  {err}")
        print(f"\n💡 Fix syntax errors in problematic files first, then re-run.")
        sys.exit(1)
    else:
        print("\n✅ All safe files passed checks!")
        if problematic_files:
            print("⚠️  Please fix the problematic files listed above.")


def main():
    """Main entry point with comprehensive error handling."""
    try:
        current_dir = Path(".")
        if not current_dir.exists():
            print("❌ Current directory does not exist")
            sys.exit(1)

        if "--fix" in sys.argv:
            print("⚠️ --fix enables in-place formatting; use only on a clean worktree.")
            auto_fix_code()
            fix_fstrings_without_placeholders(".")
        else:
            print("🔎 Read-only scan (pass --fix to enable in-place changes).")
        scan_repo(".")

    except KeyboardInterrupt:
        print("\n⏹️  Process interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
