"""Validate the static demo's scope, safety labels, and artifact links."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

REQUIRED_PAGES = {"index.html", "alarm.html", "acknowledge.html", "simulation.html"}
REQUIRED_ASSETS = {"assets/ui.css", "assets/ui.js", "assets/demo.css", "assets/demo.js"}
FORBIDDEN_ROUTE_PREFIXES = ("/admin", "/a/", "/v1/")


class DemoParser(HTMLParser):
    """Collect local links and verify command controls as HTML is parsed."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.command_depth = 0
        self.command_has_label: list[bool] = []
        self.violations: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        for attribute in ("href", "src"):
            value = values.get(attribute)
            if value:
                self.links.append(value)
        if tag == "button":
            if "data-simulated-action" not in values:
                self.violations.append("button is not marked data-simulated-action")
            self.command_depth += 1
            self.command_has_label.append(False)
        elif (
            self.command_depth and "simulated-label" in values.get("class", "").split()
        ):
            self.command_has_label[-1] = True

    def handle_endtag(self, tag: str) -> None:
        if tag != "button" or not self.command_depth:
            return
        if not self.command_has_label.pop():
            self.violations.append("button has no visible Simulated label")
        self.command_depth -= 1


def validate(root: Path) -> list[str]:
    """Return every validation problem found under one built artifact root."""
    violations: list[str] = []
    inventory = {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    }
    missing = sorted((REQUIRED_PAGES | REQUIRED_ASSETS) - inventory)
    violations.extend(f"missing artifact: {path}" for path in missing)

    for page_name in sorted(REQUIRED_PAGES):
        page = root / page_name
        if not page.exists():
            continue
        text = page.read_text(encoding="utf-8")
        parser = DemoParser()
        parser.feed(text)
        violations.extend(f"{page_name}: {message}" for message in parser.violations)
        for link in parser.links:
            parsed = urlsplit(link)
            if parsed.path.startswith(FORBIDDEN_ROUTE_PREFIXES):
                violations.append(
                    f"{page_name}: live application route in static artifact: {link}"
                )
            if parsed.scheme or parsed.netloc or not parsed.path or parsed.path == ".":
                continue
            target = (page.parent / parsed.path).resolve()
            if not target.exists():
                violations.append(f"{page_name}: missing local target: {link}")
        disclosures = (
            "Static product demo",
            "Static simulation feed",
            "Responder simulation",
        )
        if not any(disclosure in text for disclosure in disclosures):
            violations.append(f"{page_name}: missing explicit static-demo disclosure")
    return violations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    violations = validate(args.root.resolve())
    if violations:
        for violation in violations:
            print(violation)
        return 1
    print("Static demo validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
