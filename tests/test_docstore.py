"""Regression tests for NoSQL document-store support (gateway/docstore).

Self-contained fixtures plus the real TinyDB demo store when it has been
built. No LLM, no network."""
import json

import pytest

from gateway.docstore import canonicalize, import_documents, materialize, _flatten

MIXED = [
    {"_id": {"$oid": "abc123"}, "storecity": "Norwalk",
     "saleDate": {"$date": {"$numberLong": "1427144809506"}},
     "items": [
         {"name": "printer paper", "tags": ["office", "stationary"],
          "price": {"$numberDecimal": "40.01"}, "quantity": {"$numberInt": "2"}},
         {"name": "notepad", "tags": ["office"],
          "price": {"$numberDecimal": "35.29"}, "quantity": {"$numberInt": "2"}}],
     "couponUsed": False, "customer": {"gender": "M", "age": {"$numberInt": "34"}}},
    {"_id": {"$oid": "def456"}, "storecity": "Passaic", "items": [],
     "purchaseMethod": "online"},
]


@pytest.fixture
def store_and_mirror(tmp_path):
    raw = tmp_path / "docs.json"
    raw.write_text(json.dumps(MIXED), encoding="utf-8")
    store = tmp_path / "docs.tinydb"
    mirror = tmp_path / "docs.sqlite"
    n = import_documents(raw, store, "sales")
    summary = materialize(store, "sales", mirror)
    return store, mirror, summary


def test_extended_json_canonicalizes():
    doc = canonicalize(MIXED[0])
    assert doc["_id"] == "abc123"
    assert doc["saleDate"].startswith("2015-03-2")
    assert doc["items"][0]["price"] == 40.01
    assert doc["customer"]["age"] == 34


def test_ndjson_input_also_works(tmp_path):
    raw = tmp_path / "docs.ndjson"
    raw.write_text("\n".join(json.dumps(d) for d in MIXED), encoding="utf-8")
    assert import_documents(raw, tmp_path / "s.tinydb", "sales") == 2


def test_tinydb_store_holds_real_documents(store_and_mirror):
    from tinydb import TinyDB, where
    store = store_and_mirror[0]
    db = TinyDB(store)
    docs = db.table("sales").search(where("storecity") == "Norwalk")
    assert len(docs) == 1
    assert docs[0]["items"][0]["name"] == "printer paper"
    db.close()


def test_mirror_flattens_nested_arrays_into_fk_child_table(store_and_mirror):
    store, mirror, summary = store_and_mirror
    assert set(summary) == {"sales", "sales_items"}
    assert summary["sales_items"]["row_count"] == 2
    import sqlite3
    con = sqlite3.connect(mirror)
    fks = con.execute("PRAGMA foreign_key_list(sales_items)").fetchall()
    # (id, seq, table, from, to, ...)
    assert fks and fks[0][2] == "sales" and fks[0][3] == "sales_id" and fks[0][4] == "_id"
    row = con.execute(
        "SELECT s._id, SUM(i.price * i.quantity) tot FROM sales s "
        "JOIN sales_items i ON i.sales_id = s._id GROUP BY s._id").fetchall()
    assert row[0][0] == "abc123" and abs(row[0][1] - 150.60) < 1e-6
    con.close()


def test_json1_access_keeps_nested_data_queryable(store_and_mirror):
    store, mirror, _ = store_and_mirror
    import sqlite3
    con = sqlite3.connect(mirror)
    tags_len = con.execute(
        "SELECT json_array_length(tags) FROM sales_items WHERE name = 'printer paper'"
    ).fetchone()[0]
    assert tags_len == 2
    obj = con.execute("SELECT json_extract(customer, '$.gender') FROM sales "
                      "WHERE _id = 'abc123'").fetchone()[0]
    assert obj == "M"
    con.close()


def test_scalar_array_and_empty_array_become_text_columns(store_and_mirror):
    _, mirror, _ = store_and_mirror
    import sqlite3
    con = sqlite3.connect(mirror)
    row = con.execute("SELECT items FROM sales WHERE _id = 'def456'").fetchone()
    assert row[0] == "[]"
    con.close()


def test_registry_serves_the_nosql_dataset_when_built():
    from gateway.orchestrator import load_registry, resolve_database
    registry = load_registry()
    assert "sample_supplies_docs" in registry
    entry = registry["sample_supplies_docs"]
    assert entry["source"] == "nosql-document-store"
    from pathlib import Path
    if not Path(entry["path"]).is_file():
        pytest.skip("docstore demo not built in this checkout")
    database_id, resolved = resolve_database({"database": "sample_supplies_docs"})
    assert database_id == "sample_supplies_docs"
