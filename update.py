# Developed by ARGON telegram: @REACTIVEARGON
"""Self-update from the upstream repo. Runs as an argv-list subprocess (no shell)."""
import os
import re
import subprocess
import sys

UPSTREAM_BRANCH = os.environ.get("UPSTREAM_BRANCH", "main")
UPSTREAM_REPO = os.environ.get(
    "UPSTREAM_REPO",
    "https://github.com/Itzmepromgitman/ArgonsEncoder.git",
)

if not re.fullmatch(r"[A-Za-z0-9._/-]+", UPSTREAM_BRANCH):
    print(f"Invalid UPSTREAM_BRANCH: {UPSTREAM_BRANCH!r}")
    sys.exit(1)


def run(args, **kwargs):
    print("+", " ".join(args))
    return subprocess.run(args, check=False, **kwargs)


def main():
    if os.path.exists(".git"):
        import shutil

        shutil.rmtree(".git", ignore_errors=True)

    results = []
    results.append(run(["git", "init", "-q"]))
    # Local-only identity; never touch global git config.
    results.append(run(["git", "config", "--local", "user.email", "bot@argons.local"]))
    results.append(run(["git", "config", "--local", "user.name", "ArgonsBot"]))
    results.append(run(["git", "add", "."]))
    results.append(run(["git", "commit", "-qsm", "update", "--no-verify"]))
    results.append(run(["git", "remote", "add", "origin", UPSTREAM_REPO]))
    results.append(run(["git", "fetch", "origin", "-q"]))
    results.append(
        run(["git", "reset", "--hard", f"origin/{UPSTREAM_BRANCH}", "-q"])
    )

    reset_ok = all(r.returncode == 0 for r in results)
    if not reset_ok:
        print("Git update failed; aborting without pip step.")
        sys.exit(1)

    # Reinstall requirements only when the file actually changed.
    diff = subprocess.run(
        ["git", "diff", "HEAD@{1}", "HEAD", "--name-only"],
        capture_output=True,
        text=True,
    )
    req_changed = "requirements.txt" in (diff.stdout or "")

    if req_changed:
        pip_result = run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        if pip_result.returncode != 0:
            print("Warning: pip install reported failures.")

    print("Successfully updated to the latest commit from UPSTREAM_REPO")


if __name__ == "__main__":
    main()
