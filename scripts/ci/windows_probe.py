"""What breaks on Windows: an import sweep and a summary of the probe run.

The Windows probe (``.github/workflows/windows-probe.yml``) is not a gate.
It runs the suite on ``windows-latest`` and reports what fails, so the
port can be sized and tracked. Two parts:

    python scripts/ci/windows_probe.py imports --out imports.json
        Imports every module under ``src/splitsmith`` on its own and
        records each failure with the frame that raised it. The suite
        alone cannot show this: one POSIX-only import on the conftest
        path stops collection, and every test reports the same line.

    python scripts/ci/windows_probe.py summary --junit-dir D --imports F --logs-dir L
        Markdown for ``$GITHUB_STEP_SUMMARY``: totals, failures grouped by
        message (largest group first), skips grouped the same way, the
        import failures grouped by origin, and the log tail of any shard
        that wrote no JUnit report. Standard library only, so the summary
        job needs no install.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
import traceback
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOP_GROUPS = 30
EXAMPLES = 3
LOG_TAIL = 40
# A Windows absolute path or a pytest temp dir, so the same failure in two
# tests groups as one.
_PATHS = re.compile(r"[A-Za-z]:[\\/][^\s'\"]*|/tmp/[^\s'\"]*")


@dataclass
class ImportFailure:
    module: str
    error: str
    origin: str


@dataclass
class SweepResult:
    imported: int = 0
    failures: list[ImportFailure] = field(default_factory=list)


def _module_names(package_dir: Path, package: str) -> list[str]:
    names = []
    for path in sorted(package_dir.rglob("*.py")):
        rel = path.relative_to(package_dir).with_suffix("")
        parts = list(rel.parts)
        if any(not p.isidentifier() for p in parts) or parts[-1] == "__main__":
            continue
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.append(".".join([package, *parts]))
    return names


def _origin(exc: BaseException, package_dir: Path) -> str:
    """The innermost frame inside the package: the module that actually raised."""
    origin = ""
    for frame in traceback.extract_tb(exc.__traceback__):
        path = Path(frame.filename)
        try:
            rel = path.resolve().relative_to(package_dir.resolve().parent)
        except ValueError:
            continue
        origin = f"{rel.as_posix()}:{frame.lineno}"
    return origin or "(outside the package)"


def sweep(package_dir: Path, package: str) -> SweepResult:
    result = SweepResult()
    for name in _module_names(package_dir, package):
        try:
            importlib.import_module(name)
        except KeyboardInterrupt:
            raise
        except BaseException as exc:  # SystemExit from a module counts too
            message = str(exc).splitlines()[0] if str(exc) else ""
            result.failures.append(
                ImportFailure(
                    name, f"{type(exc).__name__}: {message}".rstrip(": "), _origin(exc, package_dir)
                )
            )
        else:
            result.imported += 1
    return result


def _cell(text: str, limit: int = 160) -> str:
    text = " ".join(text.split()).replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _key(element: ET.Element) -> str:
    message = (element.get("message") or "").strip()
    if not message or message == "collection failure":
        # Collection errors carry the exception on the last "E   " line.
        lines = [ln[1:].strip() for ln in (element.text or "").splitlines() if ln.startswith("E ")]
        message = lines[-1] if lines else message or "(no message)"
    return _PATHS.sub("<path>", message.splitlines()[0])


def _group_table(title: str, groups: dict[str, list[str]]) -> list[str]:
    if not groups:
        return []
    ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    out = [f"### {title} ({sum(len(v) for v in groups.values())} in {len(groups)} groups)", ""]
    out += ["| Tests | Message | Examples |", "| --- | --- | --- |"]
    for message, tests in ranked[:TOP_GROUPS]:
        out.append(f"| {len(tests)} | {_cell(message)} | {_cell(', '.join(tests[:EXAMPLES]), 200)} |")
    if len(ranked) > TOP_GROUPS:
        hidden = len(ranked) - TOP_GROUPS
        out.append(f"\n{hidden} smaller groups not shown; the JUnit files are in the run's artifacts.")
    return out + [""]


def _junit_section(junit_dir: Path) -> list[str]:
    reports = sorted(junit_dir.rglob("*.xml"))
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    failures: dict[str, list[str]] = defaultdict(list)
    skips: dict[str, list[str]] = defaultdict(list)
    for report in reports:
        for case in ET.parse(report).getroot().iter("testcase"):
            test = "::".join(p for p in (case.get("classname"), case.get("name")) if p)
            failure, error, skipped = case.find("failure"), case.find("error"), case.find("skipped")
            if failure is not None:
                counts["failed"] += 1
                failures[_key(failure)].append(test)
            elif error is not None:
                counts["errors"] += 1
                failures[_key(error)].append(test)
            elif skipped is not None:
                counts["skipped"] += 1
                skips[_key(skipped)].append(test)
            else:
                counts["passed"] += 1
    out = ["## Test suite", ""]
    out += ["| Passed | Failed | Errors | Skipped | Shard reports |", "| --- | --- | --- | --- | --- |"]
    row = [counts["passed"], counts["failed"], counts["errors"], counts["skipped"], len(reports)]
    out += ["| " + " | ".join(str(n) for n in row) + " |", ""]
    return out + _group_table("Failures and errors", failures) + _group_table("Skips", skips)


def _imports_section(imports: Path) -> list[str]:
    data = json.loads(imports.read_text(encoding="utf-8"))
    failures = data["failures"]
    out = ["## Module imports", "", f"{data['imported']} imported, {len(failures)} failed.", ""]
    if not failures:
        return out
    by_origin: dict[tuple[str, str], list[str]] = defaultdict(list)
    for f in failures:
        by_origin[(f["origin"], f["error"])].append(f["module"])
    out += ["| Raised at | Error | Modules | Examples |", "| --- | --- | --- | --- |"]
    for (origin, error), modules in sorted(by_origin.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        examples = _cell(", ".join(modules[:EXAMPLES]), 200)
        out.append(f"| `{origin}` | {_cell(error)} | {len(modules)} | {examples} |")
    return out + [""]


def _missing_reports_section(junit_dir: Path | None, logs_dir: Path) -> list[str]:
    reported = {p.stem for p in junit_dir.rglob("*.xml")} if junit_dir else set()
    out: list[str] = []
    for log in sorted(logs_dir.rglob("*.log")):
        if log.stem in reported:
            continue
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-LOG_TAIL:]
        out += [f"### {log.stem} wrote no JUnit report", "", "```", *tail, "```", ""]
    return out


def summarize(*, junit_dir: Path | None, imports: Path | None, logs_dir: Path | None) -> str:
    out = ["# Windows probe", "", "Not a gate: this run shows what breaks on Windows; nothing waits on it."]
    out.append("")
    if imports is not None and imports.exists():
        out += _imports_section(imports)
    if junit_dir is not None and junit_dir.exists():
        out += _junit_section(junit_dir)
    if logs_dir is not None and logs_dir.exists():
        out += _missing_reports_section(junit_dir, logs_dir)
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("imports")
    imp.add_argument("--out", type=Path, required=True)
    summ = sub.add_parser("summary")
    summ.add_argument("--junit-dir", type=Path)
    summ.add_argument("--imports", type=Path)
    summ.add_argument("--logs-dir", type=Path)
    args = parser.parse_args(argv)
    if args.command == "imports":
        result = sweep(ROOT / "src" / "splitsmith", "splitsmith")
        payload = {"imported": result.imported, "failures": [asdict(f) for f in result.failures]}
        args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"{result.imported} imported, {len(result.failures)} failed")
        for f in result.failures:
            print(f"  {f.module}: {f.error} ({f.origin})")
        return 0
    print(summarize(junit_dir=args.junit_dir, imports=args.imports, logs_dir=args.logs_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
