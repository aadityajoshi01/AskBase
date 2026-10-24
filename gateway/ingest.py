"""CSV ingestion — turns an uploaded CSV into a registered, read-only-queryable dataset.

The UI already parses CSV for its preview, but a browser-side parse cannot be trusted as
the queryable source of truth: this module rebuilds the file server-side with SQLite's own
parser semantics, lands it under `gateway/uploads/`, and registers it so the ordinary
schema -> generate -> validate -> read-only-execute pipeline works on it unchanged.

Names never come from user input verbatim: tables and columns are reduced to
`[a-z0-9_]`, truncated and de-duplicated, and every value is bound as a parameter —
so a header like `total); DROP TABLE orders;--` is stored as a column, not executed.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
from pathlib import Path

from gateway import ROOT

UPLOAD_DIR = Path(__file__).resolve().parent / "uploads"
INDEX_FILE = UPLOAD_DIR / "index.json"

MAX_CSV_BYTES = 5 * 1024 * 1024
MAX_COLUMNS = 60
MAX_ROWS = 200_000
MAX_CELL_CHARS = 4000
MIN_DATA_ROWS = 1


class IngestError(ValueError):
    """Rejected input. The route turns this into a 400 — never a partial dataset."""


def slugify(name: str, fallback: str = "uploaded") -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(name).casefold()).strip("_")[:40]
    return slug or fallback


def _column_name(header: str, index: int, taken: set) -> str:
    """A SQLite-safe, unique column name. Unnamed columns are only allowed when they
    have data somewhere, and get a positional name so the file stays loadable."""
    base = slugify(header, fallback=f"column_{index + 1}")
    name = base[:40]
    suffix = 2
    while name in taken:
        name = f"{base[:36]}_{suffix}"
        suffix += 1
    taken.add(name)
    return name


def _positionally_named(headers: list[str]) -> list[str]:
    taken: set[str] = set()
    return [_column_name(header or f"column_{index + 1}", index, taken)
            for index, header in enumerate(headers)]


def parse_csv(text: str) -> tuple[list[str], list[list[str]]]:
    """Header + data rows, validated. Empty rows are dropped by csv.DictReader-free read."""
    if not isinstance(text, str) or not text.strip():
        raise IngestError("the CSV is empty")
    if len(text.encode("utf-8")) > MAX_CSV_BYTES:
        raise IngestError(f"CSV larger than {MAX_CSV_BYTES // (1024 * 1024)} MB")

    sample = text[:8192]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        delimiter = ","

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if len(rows) < MIN_DATA_ROWS + 1:
        raise IngestError("the CSV needs a header row and at least one data row")

    headers = [cell.strip() for cell in rows[0]]
    if len(headers) > MAX_COLUMNS:
        raise IngestError(f"more than {MAX_COLUMNS} columns")
    width = len(headers)
    for row in rows[1:]:
        for cell in row:
            if len(cell) > MAX_CELL_CHARS:
                raise IngestError(f"a cell exceeds {MAX_CELL_CHARS} characters")

    columns = _positionally_named(headers)
    data = []
    for row in rows[1:MAX_ROWS + 1]:
        padded = [str(cell)[:MAX_CELL_CHARS] for cell in row[:width]]
        data.append(padded + [""] * (width - len(padded)))
    if len(rows) - 1 > MAX_ROWS:
        raise IngestError(f"more than {MAX_ROWS} rows")
    return columns, data


def infer_types(columns: list[str], rows: list[list[str]]) -> list[str]:
    """INTEGER / REAL / TEXT per column; anything ambiguous stays TEXT (dates included,
    which matches how Agent 2 reports SQLite-affinity schemas)."""
    types = []
    for index in range(len(columns)):
        values = [row[index].strip() for row in rows if row[index].strip() != ""]
        types.append(_infer(values))
    return types


def _infer(values: list[str]) -> str:
    if not values:
        return "TEXT"
    integer = True
    number = True
    for value in values:
        if integer and re.fullmatch(r"[-+]?\d+", value):
            continue
        integer = False
        if number:
            try:
                float(value)
            except ValueError:
                number = False
        if not number:
            return "TEXT"
    return "INTEGER" if integer else "REAL"


def _identifier(name: str) -> str:
    """Quoted SQLite identifier. `name` is already slugified, quoting is belt-and-braces."""
    return '"' + name.replace('"', "") + '"'


def dataset_id(label: str, columns: list[str], rows: list[list[str]]) -> str:
    digest = hashlib.sha1("\x1f".join(columns + [str(len(rows))]).encode("utf-8")).hexdigest()
    return f"{slugify(label)}_{digest[:8]}"


def _stored_path(path) -> str:
    """Repo-relative when it is inside the repo, absolute otherwise, so the index
    stays portable in the normal case and still works for a relocated upload dir."""
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)


def build_dataset(name: str, text: str) -> dict:
    """Create (or replace) the SQLite file for one uploaded CSV and register it."""
    columns, rows = parse_csv(text)
    types = infer_types(columns, rows)
    table = slugify(name.rsplit(".", 1)[0] if name else "uploaded", fallback="uploaded")
    dataset_id_ = dataset_id(table, columns, rows)

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"{dataset_id_}.db"
    if path.exists():
        path.unlink()

    connection = sqlite3.connect(path)
    try:
        definitions = ", ".join(
            f"{_identifier(column)} {column_type}"
            for column, column_type in zip(columns, types))
        connection.execute(f"CREATE TABLE {_identifier(table)} ({definitions})")
        placeholders = ", ".join("?" * len(columns))
        # A blank cell is missing data, not the empty string: SQLite casts '' to 0 inside
        # SUM/AVG, which would turn an absent total into a real zero revenue.
        connection.executemany(
            f"INSERT INTO {_identifier(table)} VALUES ({placeholders})",
            [[cell if cell != "" else None for cell in row] for row in rows])
        connection.commit()
    finally:
        connection.close()

    entry = {
        "id": dataset_id_,
        "label": f"{name or 'Uploaded CSV'} (upload)",
        "type": "sqlite",
        "path": _stored_path(path),
        "table": table,
        "source": "upload",
        "build": "POST /api/datasets/upload",
        "row_count": len(rows),
        "columns": [{"name": column, "type": column_type}
                    for column, column_type in zip(columns, types)],
    }
    save_upload(entry)
    return entry


def load_uploads() -> dict:
    """Registered uploads, keyed by id. Missing files stay listed as unavailable."""
    if not INDEX_FILE.is_file():
        return {}
    try:
        recorded = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {entry["id"]: {**entry, "path": str(_absolute(entry["path"]))}
            for entry in recorded if isinstance(entry, dict) and entry.get("id")}


def _absolute(path: str) -> Path:
    resolved = Path(path)
    return resolved if resolved.is_absolute() else ROOT / resolved


def save_upload(entry: dict) -> None:
    uploads = load_uploads()
    uploads[entry["id"]] = {**entry, "path": _stored_path(entry["path"])}
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(list(uploads.values()), indent=2), encoding="utf-8")
