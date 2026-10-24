"""Regression test for the audit finding: a broken view made get_schema and
profile_database raise sqlite3.OperationalError instead of returning."""
import sqlite3

from backend.data_profiler import profile_database
from backend.schema_discovery import get_schema


def _db_with_broken_view(tmp_path):
    path = tmp_path / "broken.sqlite"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE real_table (id INTEGER PRIMARY KEY, amount REAL);
        INSERT INTO real_table (amount) VALUES (1.5), (2.5);
        CREATE VIEW ok_view AS SELECT amount FROM real_table;
        CREATE VIEW broken_view AS SELECT x FROM table_that_was_dropped;
    """)
    con.commit()
    con.close()
    return {"type": "sqlite", "path": str(path)}


def test_get_schema_survives_broken_view_and_warns(tmp_path):
    result = get_schema(_db_with_broken_view(tmp_path))
    assert result["status"] == "success"
    names = {t["name"] for t in result["tables"]}
    assert {"real_table", "ok_view", "broken_view"} <= names
    broken = next(t for t in result["tables"] if t["name"] == "broken_view")
    assert broken["row_count"] is None
    assert any("broken_view" in w for w in result["warnings"])
    # healthy objects are unaffected
    real = next(t for t in result["tables"] if t["name"] == "real_table")
    assert real["row_count"] == 2


def test_profile_database_survives_broken_view(tmp_path):
    result = profile_database(_db_with_broken_view(tmp_path))
    assert result["status"] == "warning"
    codes = {w["code"] for w in result["warnings"]}
    assert "unreadable_object" in codes
    broken = next(t for t in result["tables"] if t["name"] == "broken_view")
    assert broken["row_count"] is None
    real = next(t for t in result["tables"] if t["name"] == "real_table")
    assert real["row_count"] == 2
