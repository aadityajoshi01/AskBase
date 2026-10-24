"""Labeled eval sets for the two Kaggle datasets built by build_datasets.py.

Each entry: (expected, question, ground_truth) where expected is "sql" (material
question, definite answer in this schema) or "clarify" (materially ambiguous).
Ground truths were computed directly with sqlite3 against the built .db files
on 2026-10-24 — they let Agent 4's evaluation compare generated SQL by RESULT,
not just by static shape.

Schemas are hand-written summaries of what build_datasets.py creates (Agent 2's
profiler should replace them at Gate 4). Column names are kept short so the
26-column C-MAPSS schema still fits the 4096-token prompt budget.
"""

SUPERSTORE = {
    "schema": {
        "dialect": "sqlite",
        "tables": [
            {"name": "customers", "columns": [
                {"name": "customer_id", "type": "TEXT", "pk": True},
                {"name": "name", "type": "TEXT"},
                {"name": "segment", "type": "TEXT"},
                {"name": "country", "type": "TEXT"},
                {"name": "city", "type": "TEXT"},
                {"name": "state", "type": "TEXT"},
                {"name": "region", "type": "TEXT"},
                {"name": "postal_code", "type": "TEXT"}]},
            {"name": "products", "columns": [
                {"name": "product_id", "type": "TEXT", "pk": True},
                {"name": "name", "type": "TEXT"},
                {"name": "category", "type": "TEXT"},
                {"name": "sub_category", "type": "TEXT"}]},
            {"name": "orders", "columns": [
                {"name": "order_id", "type": "TEXT", "pk": True},
                {"name": "order_date", "type": "DATE"},
                {"name": "ship_date", "type": "DATE"},
                {"name": "ship_mode", "type": "TEXT"},
                {"name": "customer_id", "type": "TEXT", "fk_to": "customers.customer_id"}]},
            {"name": "line_items", "columns": [
                {"name": "row_id", "type": "INTEGER", "pk": True},
                {"name": "order_id", "type": "TEXT", "fk_to": "orders.order_id"},
                {"name": "product_id", "type": "TEXT", "fk_to": "products.product_id"},
                {"name": "sales", "type": "REAL"}]},
        ],
        "warnings": ["11 customers have missing postal_code",
                     "sales is gross revenue in USD; no quantity or discount column exists"],
    },
    "questions": [
        ("sql", "What is total sales by customer region?",
         "West 744293.55 > East 606351.14 > Central 514251.47 > South 396640.62"),
        ("sql", "Which product category generated the most sales?",
         "Technology 827455.87, then Furniture, then Office Supplies"),
        ("sql", "How many customers are there?", "793"),
        ("sql", "How many orders were placed in each customer segment?",
         "Consumer 2537, Corporate 1491, Home Office 894"),
        ("sql", "How many distinct sub-categories of products exist?", "17"),
        ("sql", "Which product had the highest total sales?",
         "Canon imageCLASS 2200 Advanced Copier, 61599.82"),
        ("sql", "How many orders were placed in 2018?", "1661"),
        ("sql", "How many customers are located in Washington?", "38"),
        ("sql", "What is the average number of days between order date and ship date for each shipping mode?",
         "Same Day 0.05, First Class 2.19, Second Class 3.24, Standard Class 5.0"),
        ("sql", "What is the single highest line-item sales value?", "22638.48"),
        ("clarify", "Which are the best-selling products?",
         "ambiguous: 'best-selling' = by revenue or by units? no units column exists; no period, no limit"),
        ("clarify", "What were sales recently?",
         "ambiguous: 'recently' has no defined period (data spans 2015-2018)"),
        ("clarify", "Which region is performing best?",
         "ambiguous: 'performing' unmapped - sales? order count? growth? per-region basis undefined"),
        ("clarify", "Are orders increasing?",
         "ambiguous: no comparison period or granularity (monthly? yearly? which metric?)"),
    ],
}

CMAPSS = {
    "schema": {
        "dialect": "sqlite",
        "tables": [
            {"name": "engine_readings", "columns": [
                {"name": "dataset", "type": "TEXT"},
                {"name": "split", "type": "TEXT"},
                {"name": "unit_id", "type": "INTEGER"},
                {"name": "cycle", "type": "INTEGER"},
                {"name": "setting_1", "type": "REAL"},
                {"name": "setting_2", "type": "REAL"},
                {"name": "setting_3", "type": "REAL"},
                {"name": "sensor_01", "type": "REAL"},
                {"name": "sensor_02", "type": "REAL"},
                {"name": "sensor_03", "type": "REAL"},
                {"name": "sensor_04", "type": "REAL"},
                {"name": "sensor_05", "type": "REAL"},
                {"name": "sensor_06", "type": "REAL"},
                {"name": "sensor_07", "type": "REAL"},
                {"name": "sensor_08", "type": "REAL"},
                {"name": "sensor_09", "type": "REAL"},
                {"name": "sensor_10", "type": "REAL"},
                {"name": "sensor_11", "type": "REAL"},
                {"name": "sensor_12", "type": "REAL"},
                {"name": "sensor_13", "type": "REAL"},
                {"name": "sensor_14", "type": "REAL"},
                {"name": "sensor_15", "type": "REAL"},
                {"name": "sensor_16", "type": "REAL"},
                {"name": "sensor_17", "type": "REAL"},
                {"name": "sensor_18", "type": "REAL"},
                {"name": "sensor_19", "type": "REAL"},
                {"name": "sensor_20", "type": "REAL"},
                {"name": "sensor_21", "type": "REAL"}]},
            {"name": "engine_rul", "columns": [
                {"name": "dataset", "type": "TEXT"},
                {"name": "unit_id", "type": "INTEGER"},
                {"name": "last_cycle", "type": "INTEGER"},
                {"name": "true_rul_cycles", "type": "INTEGER"}]},
        ],
        "warnings": ["sensor_01..21 have no physical names in the official NASA readme",
                     "unit ids overlap between split='train' and split='test' - always filter split"],
    },
    "questions": [
        ("sql", "How many sensor reading rows are there in total?", "265256"),
        ("sql", "How many distinct engines are in the FD001 training split?", "100"),
        ("sql", "What is the average sensor_01 value for FD001 training readings?", "518.67"),
        ("sql", "What is the last recorded cycle for unit 20 in the FD001 training split?", "234"),
        ("sql", "How many test engines have a true remaining useful life below 20 cycles?", "119"),
        ("sql", "Which engine in the FD001 test set has the shortest true remaining useful life?",
         "unit 34, RUL 7 cycles"),
        ("sql", "What is the average true remaining useful life per dataset?",
         "FD001 75.5, FD002 81.2, FD003 75.3, FD004 86.6"),
        ("sql", "How many test engines have true remaining useful life of at least 100 cycles?",
         "262"),
        ("clarify", "Are the engines degrading faster in FD002?",
         "ambiguous: 'degrading faster' unmapped - which sensor, what comparison baseline?"),
        ("clarify", "Which sensors correlate with failure?",
         "ambiguous: 'failure' metric undefined (RUL table vs run-to-failure length); correlation basis undefined"),
        ("clarify", "How many engines are unhealthy?",
         "ambiguous: 'unhealthy' has no threshold or definition in this schema"),
    ],
}

DATASETS = {"superstore": SUPERSTORE, "cmaps": CMAPSS}
