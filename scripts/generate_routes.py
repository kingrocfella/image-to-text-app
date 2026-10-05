"""Generate the mobile app's route mirror from app/paths.py.

    python -m scripts.generate_routes            # write the mirror
    python -m scripts.generate_routes --check    # exit 1 if it is stale

The mirror lives in the separate image-text-react repository, expected beside this
one (override with --output). Never edit it by hand: change app/paths.py and
regenerate.
"""

import argparse
import re
import sys
from pathlib import Path

from app.paths import API_PREFIX, Api, Web

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = (
    REPO.parent / "image-text-react" / "src" / "api" / "routes.generated.ts"
)
_PARAM_RE = re.compile(r"\{([a-z_]+)\}")


def _camel(name: str) -> str:
    head, *rest = name.lower().split("_")
    return head + "".join(part.title() for part in rest)


def _constants(cls: type) -> list[tuple[str, str]]:
    return [
        (name, value)
        for name, value in vars(cls).items()
        if name.isupper() and isinstance(value, str)
    ]


def _entry(name: str, path: str) -> str:
    params = _PARAM_RE.findall(path)
    if not params:
        return f'  {_camel(name)}: "{path}",'
    args = ", ".join(f"{_camel(p)}: string | number" for p in params)
    body = _PARAM_RE.sub(lambda m: "${encode(" + _camel(m.group(1)) + ")}", path)
    return f"  {_camel(name)}: ({args}) => `{body}`,"


def render() -> str:
    api = "\n".join(_entry(n, f"{API_PREFIX}{p}") for n, p in _constants(Api))
    web = "\n".join(_entry(n, p) for n, p in _constants(Web))
    return f"""\
// GENERATED from image-to-text-app/app/paths.py by scripts/generate_routes.py.
// Do not edit. Change app/paths.py, then run `make generate-routes` there.

const encode = (value: string | number): string =>
  encodeURIComponent(String(value));

export const API_PREFIX = "{API_PREFIX}";

/** JSON API paths, already under API_PREFIX. */
export const API_ROUTES = {{
{api}
}} as const;

/** Browser and email-link paths. Never versioned. */
export const WEB_ROUTES = {{
{web}
}} as const;
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    content = render()
    if args.check:
        if not args.output.exists() or args.output.read_text() != content:
            print(
                f"{args.output} is stale; run `make generate-routes`", file=sys.stderr
            )
            return 1
        print("check-routes: mirror is up to date")
        return 0
    if not args.output.parent.is_dir():
        print(f"{args.output.parent} does not exist", file=sys.stderr)
        return 1
    args.output.write_text(content)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
