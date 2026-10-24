"""Build the NoSQL demo dataset: MongoDB Atlas sample_supplies documents.

Source: https://github.com/neelabalan/mongodb-sample-dataset  (mirror of the
official MongoDB Atlas sample datasets; Apache-2.0). 19k+ deeply nested sale
documents with arrays of line items — the classic document-store shape.

Pipeline: raw Extended JSON -> TinyDB document store (source of truth) ->
SQLite read-only mirror registered as `sample_supplies_docs` in the gateway.

Run from the repo root:  .venv/Scripts/python.exe gateway/docstore/build_sample_supplies.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from gateway.docstore import rebuild_dataset  # noqa: E402

RAW = HERE / "sample_supplies.json"
STORE = HERE / "data" / "sample_supplies.tinydb"
MIRROR = HERE / "data" / "sample_supplies.sqlite"

if __name__ == "__main__":
    if not RAW.exists():
        raise SystemExit(
            f"missing {RAW}\ndownload first:\n"
            "curl -sL -o gateway/docstore/sample_supplies.json "
            "https://raw.githubusercontent.com/neelabalan/mongodb-sample-dataset/"
            "main/sample_supplies/sales.json")
    result = rebuild_dataset(RAW, STORE, "sales", MIRROR)
    print(f"imported {result['imported_documents']} documents into TinyDB store {STORE}")
    for table, info in result["tables"].items():
        print(f"  mirror table {table}: {info['row_count']} rows, "
              f"{len(info['columns'])} columns")
