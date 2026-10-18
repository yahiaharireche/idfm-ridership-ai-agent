# Evidence for FINDINGS.md, decision 2, and for model/fare_categories.csv: how
# the ticket types renamed on 2023-07-01 map onto the old ones.
#
# Names alone are not proof, so each type's share of all rail taps is compared
# in the six months before the change and the six months after. People's
# habits do not change overnight: a type used for 53% of taps in June should
# still be about 53% in July, under its new name. Shares are used rather than
# raw counts because counts change with the season.
#
# Run from the project root:
#   .venv/bin/python model/investigations/03_ticket_shares_2023.py

import duckdb

db = duckdb.connect()

for label, start, end in [("2023-H1 (old labels)", "2023-01-01", "2023-07-01"),
                          ("2023-H2 (new labels)", "2023-07-01", "2024-01-01")]:
    print(label)
    for ticket, share in db.sql("""
            SELECT categorie_titre,
                   ROUND(100.0 * SUM(TRY_CAST(nb_vald AS BIGINT))
                         / SUM(SUM(TRY_CAST(nb_vald AS BIGINT))) OVER (), 2)
            FROM 'data/parquet/rail_counts.parquet'
            WHERE CAST(jour AS DATE) >= DATE '""" + start + """'
              AND CAST(jour AS DATE) < DATE '""" + end + """'
            GROUP BY 1 ORDER BY 2 DESC""").fetchall():
        print("   %-32s %6.2f%%" % (ticket, share))
    print()
