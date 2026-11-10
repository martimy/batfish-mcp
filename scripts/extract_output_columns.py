#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Copyright 2026 Maen Artimy

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

"""
Extract output-column tables from the Batfish docs notebooks.

The question templates describe inputs but not the columns a question returns.
The docs notebooks contain that information as a markdown "Return Value" table
after each question's invocation cell. This script downloads those notebooks
and writes ``scripts/output_columns.json``, keyed by question function name::

    .mcp/bin/python3 scripts/extract_output_columns.py

``scripts/generate_question_metadata.py`` merges this file into the static
metadata used by the MCP server.
"""

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from batfish_mcp import catalog  # noqa: E402

DOCS_BASE = "https://batfish.readthedocs.io/en/latest/_sources/notebooks/"
PAGES = [
    "configProperties",
    "topology",
    "routingProtocols",
    "routingTables",
    "forwarding",
    "filters",
    "snapshot",
    "ipsec",
    "vxlan_evpn",
    "resolvingSpecifiers",
    "differentialQuestions",
]


def fetch_notebook(page: str) -> dict:
    url = f"{DOCS_BASE}{page}.ipynb.txt"
    request = urllib.request.Request(url, headers={"User-Agent": "batfish-q-mcp"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def clean_type(text: str) -> str:
    # Markdown links like [Flow](../datamodel.rst#...) -> Flow
    return re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text).strip()


def parse_table(lines: list[str]) -> list[dict]:
    header = None
    rows: list[dict] = []
    for line in lines:
        if "|" not in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells or all(not cell for cell in cells):
            continue
        if cells[0].lower() == "name":
            header = cells
            continue
        if all(set(cell) <= set("-: ") for cell in cells):
            continue
        if header and len(cells) >= len(header):
            rows.append(dict(zip(header, cells)))
    return [
        {
            "name": row.get("Name", "").strip(),
            "type": clean_type(row.get("Type", "")),
            "description": row.get("Description", "").strip(),
        }
        for row in rows
    ]


def parse_page(notebook: dict) -> dict[str, list[dict]]:
    columns: dict[str, list[dict]] = {}
    current_fun: str | None = None
    pending_return = False

    for cell in notebook.get("cells", []):
        source = "".join(cell.get("source", []))
        if cell.get("cell_type") == "markdown":
            heading = source.strip()
            if heading.startswith("##### ") and not heading.startswith("######"):
                current_fun = None
                pending_return = False
            elif heading.startswith("###### **Return Value**"):
                pending_return = True
            elif pending_return and current_fun:
                table = parse_table(source.splitlines())
                if table:
                    columns[current_fun] = table
                    pending_return = False
        elif cell.get("cell_type") == "code" and current_fun is None:
            match = re.search(r"bf\.q\.(\w+)\s*\(", source)
            if match:
                current_fun = match.group(1)
    return columns


def main() -> None:
    catalog_funs = {
        q["fun"]
        for category in catalog.read_questions()["Batfish"]
        for q in category["questions"]
    }
    columns: dict[str, list[dict]] = {}
    for page in PAGES:
        columns.update(parse_page(fetch_notebook(page)))

    missing = sorted(catalog_funs - set(columns))
    result = {fun: columns.get(fun, []) for fun in sorted(catalog_funs)}
    out_path = ROOT / "scripts" / "output_columns.json"
    out_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out_path} ({len(result)} questions)")
    if missing:
        print(f"warning: no output columns found for {missing}")


if __name__ == "__main__":
    main()
