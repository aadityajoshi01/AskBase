"""Guard tests for datasets/ tooling (build helpers + labeled question sets).

These never touch the live model or the built .db files — they pin the pure
helpers and the shape of the eval sets so `eval_live.py --dataset X` cannot
silently regress.
"""
from datasets.build_datasets import _date_iso, _float_or_null, _int_or_null
from datasets.question_sets import DATASETS


def test_dates_are_dd_mm_yyyy():
    # Kaggle superstore dates proven DD/MM/YYYY: 08/11/2017 is 8 Nov, not Aug 11
    assert _date_iso("08/11/2017") == "2017-11-08"
    assert _date_iso("25/12/2015") == "2015-12-25"
    assert _date_iso("13/13/2015") is None  # unparseable -> NULL, never a crash
    assert _date_iso("") is None


def test_numeric_guards_return_null_not_raise():
    assert _int_or_null("") is None
    assert _int_or_null("42420") == 42420
    assert _int_or_null("12.0") == 12
    assert _float_or_null("bad") is None
    assert _float_or_null("261.96") == 261.96


def test_every_dataset_has_sql_and_clarify_questions():
    for name, ds in DATASETS.items():
        expected = {q[0] for q in ds["questions"]}
        assert expected == {"sql", "clarify"}, name
        assert len(ds["questions"]) >= 8, name


def test_question_tuples_are_labeled_and_truthy_or_none():
    for name, ds in DATASETS.items():
        for q in ds["questions"]:
            assert q[0] in ("sql", "clarify"), (name, q)
            assert isinstance(q[1], str) and q[1].endswith("?"), (name, q)
            if q[0] == "sql":
                assert len(q) == 3 and q[2], f"{name}: sql question lacks ground truth"


def test_schemas_are_sqlite_with_tables_and_columns():
    qualified = set()
    for name, ds in DATASETS.items():
        schema = ds["schema"]
        assert schema["dialect"] == "sqlite", name
        assert schema["tables"], name
        qualified |= {f"{t['name']}.{c['name']}" for t in schema["tables"]
                      for c in t["columns"]}
    assert "line_items.sales" in qualified
    assert "engine_readings.sensor_01" in qualified
