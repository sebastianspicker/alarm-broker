"""Build the GitHub Pages artifact from static demo source and live UI assets."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PAGES_SOURCE = REPOSITORY_ROOT / "pages"
PRODUCT_ASSETS = (
    REPOSITORY_ROOT / "services" / "escalane" / "escalane" / "api" / "assets"
)


def build(output: Path) -> None:
    """Create a clean Pages artifact while keeping product assets authoritative."""
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(PAGES_SOURCE, output)
    destination_assets = output / "assets"
    for asset in PRODUCT_ASSETS.iterdir():
        if asset.is_file():
            shutil.copy2(asset, destination_assets / asset.name)
    (output / ".nojekyll").touch()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "build" / "pages",
    )
    args = parser.parse_args()
    build(args.output.resolve())
    print(f"Built GitHub Pages artifact at {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
