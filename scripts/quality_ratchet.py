"""Code-quality ratchet for flake8, mypy, black and isort.

The codebase predates these gates and does not pass them cleanly, so a strict
gate would be red on every run and report nothing. Instead this records how
many findings each tool reports and fails when:

- a count goes UP: new debt was added; fix it rather than raise the baseline;
- a count goes DOWN: debt was paid off, so lower the baseline in the same
  change, or the slack would quietly let new debt back in.

Either way the numbers only move one direction. (Ported from NoAlibi.)

    python -m scripts.quality_ratchet
"""

import re
import subprocess
import sys

# Recorded 2026-10-05. Lower these as findings are fixed; never raise them.
BASELINE = {
    "flake8": 6,  # existing C901 complexity findings in app/
    "mypy": 41,  # mostly SQLAlchemy Column[...] typing in app/
    "black": 0,
    "isort": 0,
}
PATHS = ["app", "tests", "scripts"]


def _run(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", *args], capture_output=True, text=True, check=False
    )
    return result.stdout + result.stderr


def measure() -> dict[str, int]:
    flake8 = [line for line in _run("flake8", "app").splitlines() if line.strip()]
    mypy = re.search(r"Found (\d+) errors?", _run("mypy", "app"))
    black = re.search(
        r"(\d+) files? would be reformatted", _run("black", "--check", *PATHS)
    )
    isort = _run("isort", "--check-only", *PATHS).count("ERROR:")
    return {
        "flake8": len(flake8),
        "mypy": int(mypy.group(1)) if mypy else 0,
        "black": int(black.group(1)) if black else 0,
        "isort": isort,
    }


def main() -> int:
    counts = measure()
    failed = False
    for tool, baseline in BASELINE.items():
        count = counts[tool]
        if count > baseline:
            print(f"{tool}: {count} findings, baseline {baseline}: fix the new ones")
            failed = True
        elif count < baseline:
            print(
                f"{tool}: {count} findings, baseline {baseline}: good; lower "
                f'BASELINE["{tool}"] to {count} in scripts/quality_ratchet.py'
            )
            failed = True
        else:
            print(f"{tool}: {count} findings (at baseline)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
