"""Build SQLite databases from the two downloaded Kaggle datasets.

    cd ai-analyst
    ../.venv/Scripts/python.exe datasets/build_datasets.py \
        [--src "C:/Users/Aaditya Joshi/Downloads/Plane Images"]

Stdlib only (csv + sqlite3). Outputs, in this directory (gitignored, rebuildable):
  superstore.db — train.csv normalized into customers/products/orders/line_items
                  (this Kaggle variant has no Quantity column — sales $ only)
  cmapss.db     — FD001-FD004 telemetry + true test RUL, dataset column = subset id

These feed eval_live.py --dataset superstore|cmaps (see question_sets.py).
Agent 2 owns schema discovery/profiling long-term; this script just makes the
raw files SQL-queryable so Gates 4-7 have real data to point at.
"""
import argparse
import csv
import sqlite3
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent

# C-MAPSS column semantics per official readme.txt: cols 3-5 are operational
# settings, 6-26 "sensor measurement 1..21". The readme gives no physical names,
# so columns keep the generic documented names (no invented semantics).
CMAPSS_COLS = [
    ("unit_id", "INTEGER"), ("cycle", "INTEGER"),
    ("setting_1", "REAL"), ("setting_2", "REAL"), ("setting_3", "REAL"),
] + [(f"sensor_{i:02d}", "REAL") for i in range(1, 22)]


def _date_iso(value):
    try:
        # Kaggle superstore dates are DD/MM/YYYY — verified: 0 unparseable rows
        # and ship-order lag 0-7 days; MM/DD leaves 5841 unparseable and
        # implies impossible 4-month shipping lags.
        return datetime.strptime(value.strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None  # keep messy dates NULL rather than guess


def _int_or_null(value):
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None  # empty/garbage numerics become NULL, not crashes


def _postal_or_null(value):
    """US ZIP as TEXT — int() silently ate the leading zero of 01550-style ZIPs."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return text.zfill(5)
    return text


def _float_or_null(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def build_superstore(src_dir: Path) -> Path:
    csv_path = src_dir / "superstore" / "train.csv"
    rows = list(csv.DictReader(open(csv_path, newline="", encoding="utf-8")))
    if not rows:
        raise SystemExit(f"no rows in {csv_path}")

    customers, products, orders = {}, {}, {}
    line_items = []
    for r in rows:
        cid = r["Customer ID"]
        customers.setdefault(cid, (cid, r["Customer Name"], r["Segment"],
                                   r["Country"], r["City"], r["State"],
                                   r["Region"], _postal_or_null(r["Postal Code"])))
        pid = r["Product ID"]
        products.setdefault(pid, (pid, r["Product Name"], r["Category"],
                                  r["Sub-Category"]))
        oid = r["Order ID"]
        orders.setdefault(oid, (oid, _date_iso(r["Order Date"]),
                                _date_iso(r["Ship Date"]), r["Ship Mode"], cid))
        line_items.append((r["Row ID"], oid, pid, _float_or_null(r["Sales"])))

    db = HERE / "superstore.db"
    db.unlink(missing_ok=True)
    con = sqlite3.connect(db)
    con.executescript("""
CREATE TABLE customers(customer_id TEXT PRIMARY KEY, name TEXT, segment TEXT,
    country TEXT, city TEXT, state TEXT, region TEXT, postal_code TEXT);
CREATE TABLE products(product_id TEXT PRIMARY KEY, name TEXT, category TEXT,
    sub_category TEXT);
CREATE TABLE orders(order_id TEXT PRIMARY KEY, order_date DATE, ship_date DATE,
    ship_mode TEXT, customer_id TEXT REFERENCES customers(customer_id));
CREATE TABLE line_items(row_id INTEGER PRIMARY KEY,
    order_id TEXT REFERENCES orders(order_id),
    product_id TEXT REFERENCES products(product_id),
    sales REAL);
""")
    con.executemany("INSERT INTO customers VALUES(?,?,?,?,?,?,?,?)", customers.values())
    con.executemany("INSERT INTO products VALUES(?,?,?,?)", products.values())
    con.executemany("INSERT INTO orders VALUES(?,?,?,?,?)", orders.values())
    con.executemany("INSERT INTO line_items VALUES(?,?,?,?)", line_items)
    con.commit()
    con.close()
    print(f"superstore.db: {len(customers)} customers, {len(products)} products, "
          f"{len(orders)} orders, {len(line_items)} line items")
    return db


def _load_cmapss_table(con, dataset, split, path):
    """One FDxxx txt file -> engine_readings rows (unit ids are 1..N contiguous,
    verified against the files, which is how RUL lines are matched to engines)."""
    readings = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) != len(CMAPSS_COLS):
                continue
            readings.append([dataset, split] + [int(parts[0]), int(parts[1])]
                            + [float(x) for x in parts[2:]])
    col_names = ", ".join(["dataset", "split"] + [c for c, _ in CMAPSS_COLS])
    placeholders = ", ".join("?" * (len(CMAPSS_COLS) + 2))
    con.executemany(f"INSERT INTO engine_readings({col_names}) VALUES "
                    f"({placeholders})", readings)
    return readings


def build_cmapss(src_dir: Path) -> Path:
    src = src_dir / "c-maps"
    db = HERE / "cmapss.db"
    db.unlink(missing_ok=True)
    con = sqlite3.connect(db)
    cols_sql = ",\n    ".join(f"{n} {t}" for n, t in CMAPSS_COLS)
    con.executescript(f"""
CREATE TABLE engine_readings(dataset TEXT, split TEXT, {cols_sql});
CREATE INDEX idx_read ON engine_readings(dataset, split, unit_id, cycle);
CREATE TABLE engine_rul(dataset TEXT, unit_id INTEGER,
    last_cycle INTEGER, true_rul_cycles INTEGER);
""")
    total = 0
    for fd in ("FD001", "FD002", "FD003", "FD004"):
        for kind in ("train", "test"):
            readings = _load_cmapss_table(con, fd, kind, src / f"{kind}_{fd}.txt")
            total += len(readings)
        rul_lines = [l.split() for l in open(src / f"RUL_{fd}.txt") if l.split()]
        # truth: one line per test engine in unit order; last *train* cycle is
        # where the true RUL is defined (test series end early by design)
        last_cycle = dict(con.execute(
            "SELECT unit_id, MAX(cycle) FROM engine_readings "
            "WHERE dataset=? AND split='train' GROUP BY unit_id", (fd,)))
        con.executemany(
            "INSERT INTO engine_rul VALUES(?,?,?,?)",
            [(fd, i + 1, last_cycle.get(i + 1), int(parts[0]))
             for i, parts in enumerate(rul_lines)])
    con.commit()
    n_rul = con.execute("SELECT COUNT(*) FROM engine_rul").fetchone()[0]
    con.close()
    print(f"cmapss.db: {total} readings rows, {n_rul} true-RUL rows")
    return db


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="C:/Users/Aaditya Joshi/Downloads/Plane Images")
    args = ap.parse_args()
    src = Path(args.src)
    build_superstore(src)
    build_cmapss(src)


if __name__ == "__main__":
    main()
