# Simple sample database

`create_sample_db.py` creates a deterministic SQLite fixture for the Database
Intelligence and Data Quality workstream.

From the repository root:

```bash
python agent2_data-intelligence/sample_data/create_sample_db.py
```

To choose another output path:

```bash
python agent2_data-intelligence/sample_data/create_sample_db.py /tmp/askbase.sqlite
```

The default generated database is `generated/simple_askbase.sqlite` and is
ignored by Git. The source data is intentionally **not** cleaned by the
generator.

## Tables

- `customers`: customer identities and contact fields
- `products`: product catalog and prices
- `orders`: customer orders
- `order_items`: order line items
- `customer_tags`: tag history without a primary key, used for exact-duplicate testing

## Intentional anomalies

- Duplicate customer email: `asha@example.com`
- Null email and city values
- Leading/trailing whitespace in customer and tag values
- Category collisions after `LOWER(TRIM())`: `Electronics`, `electronics `, and `ELECTRONICS`
- Text values (`N/A`) in numeric price columns
- Negative quantity and negative product price
- Very large numeric outlier candidates (`99999`)
- Invalid and mixed-format dates
- Exact duplicate row in `customer_tags`
- One orphan `order_items.order_id` value (`999`)

The orphan is deliberate: foreign-key constraints are declared, but enforcement
is disabled only during fixture seeding so the profiler can report the bad row.
