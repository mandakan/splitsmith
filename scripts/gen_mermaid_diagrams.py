"""Regenerate docs/architecture/DIAGRAMS.md from the Archify specs.

The interactive HTML diagrams under ``docs/architecture/`` need a browser;
GitHub renders none of them inline. This script derives a Mermaid version of
each ``*.<type>.json`` spec so the same topology, labels and explanatory
cards are readable straight from the repository page. The JSON stays the
single source: edit it, re-run ``archify deliver`` for the HTML, then this.

    uv run --frozen python scripts/gen_mermaid_diagrams.py          # rewrite
    uv run --frozen python scripts/gen_mermaid_diagrams.py --check  # exit 1 if stale

The mapping is deliberately lossy -- Mermaid has no port sides, channels or
guided views -- but every node, relationship and label carries over.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent / "docs" / "architecture"
OUT = ROOT / "DIAGRAMS.md"

# Order on the page. Each entry: (spec file, HTML file).
SPECS = [
    ("splitsmith.architecture.json", "splitsmith.html"),
    ("stage-status.lifecycle.json", "stage-status.html"),
    ("stage-detection.workflow.json", "stage-detection.html"),
    ("share-link.sequence.json", "share-link.html"),
]

_EDGE = {"emphasis": "==>", "dashed": "-.->", "security": "-->", "default": "-->"}
_CLASS_STYLES = {
    "frontend": "fill:#e0f2fe,stroke:#0369a1,color:#0c4a6e",
    "backend": "fill:#dcfce7,stroke:#15803d,color:#14532d",
    "database": "fill:#ede9fe,stroke:#6d28d9,color:#3b0764",
    "security": "fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d",
    "messagebus": "fill:#ffedd5,stroke:#c2410c,color:#7c2d12",
    "cloud": "fill:#f1f5f9,stroke:#475569,color:#1e293b",
    "external": "fill:#f1f5f9,stroke:#475569,color:#1e293b",
}


def _q(text: str) -> str:
    """Quote text for a Mermaid node or edge label."""
    return '"' + text.replace('"', "#quot;") + '"'


def _node_text(node: dict[str, Any]) -> str:
    text = node["label"]
    if node.get("sublabel"):
        text += "<br>" + node["sublabel"]
    return text


def _shape(node: dict[str, Any], text: str) -> str:
    t = node.get("type", "backend")
    label = _q(text)
    if t == "database":
        return f"[({label})]"
    if t == "security":
        return f"{{{{{label}}}}}"
    if t == "external":
        return f"([{label}])"
    return f"[{label}]"


def _edge(rel: dict[str, Any]) -> str:
    arrow = _EDGE.get(rel.get("variant", "default"), "-->")
    label = rel.get("label")
    if label:
        return f"{rel['from']} {arrow}|{_q(label)}| {rel['to']}"
    return f"{rel['from']} {arrow} {rel['to']}"


def _class_lines(nodes: list[dict[str, Any]]) -> list[str]:
    lines = [f"    classDef {t} {style}" for t, style in _CLASS_STYLES.items()]
    by_type: dict[str, list[str]] = {}
    for n in nodes:
        by_type.setdefault(n.get("type", "backend"), []).append(n["id"])
    lines += [f"    class {','.join(ids)} {t}" for t, ids in by_type.items()]
    return lines


# GitHub may or may not register the elk layout; when it does not, Mermaid
# falls back to dagre, and a top-down flow reads acceptably under both.
_FLOWCHART_HEAD = ['%%{init: {"flowchart": {"defaultRenderer": "elk"}}}%%', "flowchart TB"]


def architecture(spec: dict[str, Any]) -> str:
    lines = list(_FLOWCHART_HEAD)
    wrapped: set[str] = set()
    for i, b in enumerate(spec.get("boundaries", [])):
        lines.append(f"    subgraph b{i} [{_q(b['label'])}]")
        for cid in b["wraps"]:
            node = next(c for c in spec["components"] if c["id"] == cid)
            lines.append(f"        {cid}{_shape(node, _node_text(node))}")
            wrapped.add(cid)
        lines.append("    end")
    for node in spec["components"]:
        if node["id"] not in wrapped:
            lines.append(f"    {node['id']}{_shape(node, _node_text(node))}")
    lines += [f"    {_edge(c)}" for c in spec.get("connections", [])]
    lines += _class_lines(spec["components"])
    return "\n".join(lines)


def workflow(spec: dict[str, Any]) -> str:
    # Lanes are dropped on purpose: as subgraphs they fight the left-to-right
    # flow under both dagre and elk, and the chain is the point of this one.
    lines = [_FLOWCHART_HEAD[0], "flowchart LR"]
    for node in sorted(spec["nodes"], key=lambda n: (n["col"], n["lane"])):
        lines.append(f"    {node['id']}{_shape(node, _node_text(node))}")
    lines += [f"    {_edge(e)}" for e in spec.get("edges", [])]
    lines += _class_lines(spec["nodes"])
    return "\n".join(lines)


def lifecycle(spec: dict[str, Any]) -> str:
    lines = ["stateDiagram-v2", "    direction LR"]
    states = spec["states"]
    for s in states:
        desc = s["label"] + (f": {s['sublabel']}" if s.get("sublabel") else "")
        lines.append(f"    {s['id']}: {desc}")
    for s in states:
        if s["type"] == "start":
            lines.append(f"    [*] --> {s['id']}")
    # Main-rail order is the column order; transitions between adjacent rail
    # states are implicit in Archify and carry their wording in ``note``.
    for t in spec["transitions"]:
        label = t.get("label") or t.get("note")
        lines.append(f"    {t['from']} --> {t['to']}" + (f": {label}" if label else ""))
    for s in states:
        if s["type"] == "success" or s["lane"] == "terminal":
            lines.append(f"    {s['id']} --> [*]")
    return "\n".join(lines)


def sequence(spec: dict[str, Any]) -> str:
    lines = ["sequenceDiagram", "    autonumber"]
    for p in spec["participants"]:
        lines.append(f"    participant {p['id']} as {p['label']}")
    msgs = sorted(spec["messages"], key=lambda m: m["y"])
    acts = spec.get("activations", [])
    segments = spec.get("segments", [])
    first, last = spec["participants"][0]["id"], spec["participants"][-1]["id"]
    active: set[str] = set()
    open_segment: dict[str, Any] | None = None
    for m in msgs:
        y = m["y"]
        seg = next((s for s in segments if s["from"] <= y <= s["to"]), None)
        if seg is not open_segment:
            if open_segment is not None:
                lines.append("    end")
            if seg is not None:
                lines.append("    rect rgb(248, 250, 252)")
                lines.append(f"    Note over {first},{last}: {seg['label']}")
            open_segment = seg
        for a in acts:
            if a["participant"] not in active and a["from"] <= y <= a["to"] and a["participant"] == m["to"]:
                active.add(a["participant"])
                lines.append(f"    activate {a['participant']}")
        variant = m.get("variant", "default")
        arrow = "-->>" if variant == "return" else "-)" if variant == "dashed" else "->>"
        lines.append(f"    {m['from']}{arrow}{m['to']}: {m['label']}")
        for a in acts:
            if a["participant"] in active and a["participant"] == m["from"] and a["to"] <= y + 8:
                active.discard(a["participant"])
                lines.append(f"    deactivate {a['participant']}")
    if open_segment is not None:
        lines.append("    end")
    return "\n".join(lines)


_RENDER = {"architecture": architecture, "lifecycle": lifecycle, "workflow": workflow, "sequence": sequence}


def render_all() -> str:
    out = [
        "# Diagrams (GitHub-rendered)",
        "",
        "Generated by `scripts/gen_mermaid_diagrams.py` from the Archify specs in this",
        "directory; do not edit by hand. Each section links to the interactive HTML",
        "version, which adds pan/zoom, search, guided views, swim lanes and source links.",
        "",
    ]
    for spec_name, html_name in SPECS:
        spec = json.loads((ROOT / spec_name).read_text())
        kind = spec["diagram_type"]
        out += [f"## {spec['meta']['title']}", ""]
        out += [f"Interactive: [`{html_name}`]({html_name}) (source: `{spec_name}`)", ""]
        out += ["```mermaid", _RENDER[kind](spec), "```", ""]
        for card in spec.get("cards", []):
            out.append(f"**{card['title']}**")
            out.append("")
            out += [f"- {item}" for item in card["items"]]
            out.append("")
    out += ["## Hosted data model", ""]
    out += ["See [`data-model.md`](data-model.md), generated from `db/models.py`.", ""]
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when the committed file is stale")
    args = parser.parse_args()
    rendered = render_all()
    if args.check:
        if not OUT.exists() or OUT.read_text() != rendered:
            print(f"{OUT} is stale; run scripts/gen_mermaid_diagrams.py", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
