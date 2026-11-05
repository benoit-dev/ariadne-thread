#!/usr/bin/env python3
"""Show how Ariadne's Thread turns SKILL.md files into flows.

    python scripts/parse_skills_report.py <skills_dir> [<skills_dir> ...] [--markdown]

Run it with the Python that has Hermes installed (it imports fastapi and
hermes_constants through dashboard/plugin_api.py). Each skills_dir is walked
the way the plugin walks a profile's skills folder, symlinked shared skills
included. --markdown prints the report as a markdown table.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from collections import Counter
from pathlib import Path

API_PATH = Path(__file__).resolve().parent.parent / "dashboard" / "plugin_api.py"
COLUMNS = ("skill", "source dir", "steps", "fell back", "strategy / notes")


def load_api():
    spec = importlib.util.spec_from_file_location("ariadne_plugin_api", API_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except ImportError as exc:
        sys.exit(f"cannot import {API_PATH.name} ({exc}); run this with the Python that has Hermes installed")
    return module


def describe(api, md: Path):
    """Return (steps, fallback, strategy, notes) for one SKILL.md."""
    text = md.read_text(encoding="utf-8", errors="replace")
    fm, body = api._split_frontmatter(text)
    flow = api.parse_skill_flow(fm, body)
    kinds = Counter(step["kind"] for step in flow["steps"])
    mix = ", ".join(f"{n} {kind}" for kind, n in sorted(kinds.items()))
    note = f"{flow['strategy']} ({mix})"
    if not fm:
        note += "; no frontmatter"
    return len(flow["steps"]), flow["fallback"], flow["strategy"], note


def collect(api, skills_dirs: list[Path]):
    rows = []
    for root in skills_dirs:
        for md in api._skill_files(root):
            label = md.parent.relative_to(root).as_posix() or "."
            try:
                steps, fallback, strategy, note = describe(api, md)
            except Exception as exc:
                rows.append((label, str(root), 0, True, "error", f"error: {exc}"))
                continue
            rows.append((label, str(root), steps, fallback, strategy, note))
    return rows


def render_table(rows, markdown: bool) -> list[str]:
    cells = [(r[0], r[1], str(r[2]), "yes" if r[3] else "no", r[5]) for r in rows]
    if markdown:
        out = ["| " + " | ".join(COLUMNS) + " |", "|" + "|".join(["---"] * len(COLUMNS)) + "|"]
        out += ["| " + " | ".join(c.replace("|", "\\|") for c in row) + " |" for row in cells]
        return out
    widths = [max(len(COLUMNS[i]), *(len(row[i]) for row in cells)) if cells else len(COLUMNS[i]) for i in range(5)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    return [fmt.format(*COLUMNS)] + [fmt.format(*row) for row in cells]


def render_summary(rows, markdown: bool) -> list[str]:
    by_strategy = Counter(r[4] for r in rows)
    fell_back = sum(1 for r in rows if r[3])
    lines = ["", "## Summary", ""] if markdown else ["", "Summary"]
    bullet = "- " if markdown else "  "
    lines.append(f"{bullet}skills parsed: {len(rows)}")
    for name in ("step-headings", "procedure-list", "first-ordered-list", "sections", "single", "error"):
        if by_strategy.get(name):
            lines.append(f"{bullet}{name}: {by_strategy[name]}")
    share = f" ({fell_back * 100 // len(rows)}%)" if rows else ""
    lines.append(f"{bullet}fell back: {fell_back}{share}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report how SKILL.md files parse into Ariadne's Thread flows.")
    parser.add_argument("skills_dirs", nargs="+", type=Path, metavar="skills_dir")
    parser.add_argument("--markdown", action="store_true", help="print a markdown table")
    args = parser.parse_args(argv)

    missing = [d for d in args.skills_dirs if not d.is_dir()]
    if missing:
        print("not a directory: " + ", ".join(str(d) for d in missing), file=sys.stderr)
        return 2

    rows = collect(load_api(), args.skills_dirs)
    print("\n".join(render_table(rows, args.markdown) + render_summary(rows, args.markdown)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
