from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection


@pytest.mark.integration
def test_dbg(db: Connection) -> None:
    db.execute(
        text("""
INSERT INTO catalogue_entry (business_key, preferred_term, status)
SELECT 'NPTC-8' || lpad(g::text, 8, '0'), 'Bulk plan fixture assay number ' || g::text, 'active'
FROM generate_series(1, 4000) AS g
""")
    )
    db.execute(text("ANALYZE catalogue_entry"))
    db.execute(text("SELECT set_limit(0.3::real)"))
    for row in db.execute(
        text(
            "EXPLAIN SELECT id FROM catalogue_entry WHERE nptc_search_text(preferred_term) % 'bulk plan fixture assay number 1234'"
        )
    ).scalars():
        print(row)
    print("---- with enable_seqscan off")
    db.execute(text("SET LOCAL enable_seqscan = off"))
    for row in db.execute(
        text(
            "EXPLAIN SELECT id FROM catalogue_entry WHERE nptc_search_text(preferred_term) % 'bulk plan fixture assay number 1234'"
        )
    ).scalars():
        print(row)
