"""Create AskBase's small, intentionally messy SQLite database.

The database is a deterministic fixture for Agent 2's schema-discovery and
data-profiling work. It contains realistic relationships and documented data
quality problems, but this script never cleans or repairs those problems.

Usage from the repository root::

    python agent2_data-intelligence/sample_data/create_sample_db.py
    python agent2_data-intelligence/sample_data/create_sample_db.py path/to/db.sqlite

The default output is ignored by Git through the repository's ``*.sqlite``
rule.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


DEFAULT_DATABASE_PATH = (
    Path(__file__).resolve().parent / "generated" / "simple_askbase.sqlite"
)

TABLE_NAMES = (
    "customers",
    "products",
    "orders",
    "order_items",
    "customer_tags",
)


SCHEMA_SQL = """
CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL,
    email TEXT,
    city TEXT,
    created_at TEXT
);

CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    product_name TEXT NOT NULL,
    category TEXT,
    unit_price NUMERIC,
    available_on TEXT
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    order_date TEXT,
    status TEXT,
    total_amount NUMERIC,
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE TABLE order_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER,
    unit_price NUMERIC,
    FOREIGN KEY (order_id) REFERENCES orders(id),
    FOREIGN KEY (product_id) REFERENCES products(id)
);

-- Deliberately has no primary key so an exact duplicate row can be profiled.
CREATE TABLE customer_tags (
    customer_id INTEGER NOT NULL,
    tag TEXT,
    created_at TEXT,
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);
"""


def _seed_database(connection: sqlite3.Connection) -> None:
    """Create tables and insert the fixed messy fixture rows.

    Foreign-key enforcement is disabled only while inserting the deliberate
    orphan order_items row. The constraints remain declared in the schema and
    are re-enabled before the database is returned.
    """

    connection.execute("PRAGMA foreign_keys = OFF")
    connection.executescript(SCHEMA_SQL)

    # Duplicate email values (customer ids 1 and 3), a NULL email/city, and an
    # invalid date are intentional profiling cases.
    connection.executemany(
        """
        INSERT INTO customers (id, full_name, email, city, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (1, "Asha Patel", "asha@example.com", "New York", "2024-01-15"),
            (2, "Ben Carter", "BEN@example.com", "Mumbai", "15/02/2024"),
            (3, "Asha Patel Duplicate", "asha@example.com", "New York ", "2024-02-01"),
            (4, "Carla Gomez", None, None, "2024-13-45"),
            (5, "Dev Singh", " dev@example.com ", "Bengaluru", "2024-03-20"),
        ],
    )

    # Category casing/whitespace collisions, a text value in a numeric
    # column, a negative price, a NULL price, and mixed date formats.
    connection.executemany(
        """
        INSERT INTO products (id, product_name, category, unit_price, available_on)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (1, "Laptop", "Electronics", 999.99, "2024-01-05"),
            (2, "Phone", "electronics ", 599.99, "2024-02-30"),
            (3, "Mouse", "ELECTRONICS", "N/A", "31/02/2024"),
            (4, "Desk", "Home & Office", -25.0, "2024/04/01"),
            (5, "Office Chair", None, None, "2024-05-10"),
        ],
    )

    # Mixed date formats, one invalid date, a NULL amount, and a very large
    # amount that should be reported as an outlier candidate.
    connection.executemany(
        """
        INSERT INTO orders (id, customer_id, order_date, status, total_amount)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (1, 1, "2024-01-20", "completed", 1599.98),
            (2, 2, "20/02/2024", "shipped", 599.99),
            (3, 3, "2024-13-45", "pending", 99999.0),
            (4, 4, None, "cancelled", None),
            (5, 5, "2024-03-25", "completed", 25.0),
        ],
    )

    # A negative quantity, a text value in a numeric column, and one orphan
    # order_id (999) are intentional. The orphan is possible because FK checks
    # are disabled only for this seed transaction.
    connection.executemany(
        """
        INSERT INTO order_items (id, order_id, product_id, quantity, unit_price)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (1, 1, 1, 1, 999.99),
            (2, 1, 2, 1, "N/A"),
            (3, 2, 2, -2, 599.99),
            (4, 3, 3, 1, 99999.0),
            (5, 5, 4, 1, None),
            (6, 999, 1, 1, 10.0),
        ],
    )

    # The first two rows are an exact duplicate. This table intentionally has
    # no primary key so GROUP BY across all columns can detect it.
    connection.executemany(
        """
        INSERT INTO customer_tags (customer_id, tag, created_at)
        VALUES (?, ?, ?)
        """,
        [
            (1, "VIP", "2024-01-01"),
            (1, "VIP", "2024-01-01"),
            (2, "vip ", "01/02/2024"),
            (3, "new", "2024-02-01"),
        ],
    )

    connection.commit()
    connection.execute("PRAGMA foreign_keys = ON")


def create_sample_database(destination: str | Path | None = None) -> Path:
    """Create and return the path to a fresh deterministic SQLite database."""

    database_path = Path(destination) if destination is not None else DEFAULT_DATABASE_PATH
    database_path = database_path.expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)

    if database_path.exists():
        database_path.unlink()

    connection = sqlite3.connect(database_path)
    try:
        _seed_database(connection)
        foreign_key_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if len(foreign_key_violations) != 1:
            raise RuntimeError(
                "sample database should contain exactly one deliberate foreign-key violation"
            )
    finally:
        connection.close()

    return database_path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "destination",
        nargs="?",
        type=Path,
        help=f"SQLite output path (default: {DEFAULT_DATABASE_PATH})",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    database_path = create_sample_database(args.destination)
    connection = sqlite3.connect(database_path)
    try:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in TABLE_NAMES
        }
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        connection.close()

    print(f"Created: {database_path}")
    print("Row counts:")
    for table, count in counts.items():
        print(f"  {table}: {count}")
    print(f"Foreign-key violations intentionally present: {len(violations)}")


if __name__ == "__main__":
    main()
