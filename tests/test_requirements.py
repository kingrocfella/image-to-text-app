"""requirements-dev.txt mirrors requirements.txt for the packages they share."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_PIN_RE = re.compile(r"^([A-Za-z0-9_.\-]+)(\[[a-z,]+\])?==(\S+)$")


def _pins(name: str) -> dict[str, str]:
    pins = {}
    for line in (ROOT / name).read_text().splitlines():
        match = _PIN_RE.match(line.strip())
        if match:
            pins[match.group(1).lower()] = match.group(3)
    return pins


def test_every_requirement_is_pinned_exactly():
    """pip-audit --no-deps (make audit) needs exact pins, and so does a rebuild."""
    for name in ("requirements.txt", "requirements-dev.txt"):
        for line in (ROOT / name).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                assert _PIN_RE.match(line), f"{name}: {line!r} is not pinned with =="


def test_dev_pins_match_the_image():
    image, dev = _pins("requirements.txt"), _pins("requirements-dev.txt")
    shared = image.keys() & dev.keys()
    assert len(shared) > 20
    assert {k: dev[k] for k in shared} == {k: image[k] for k in shared}
