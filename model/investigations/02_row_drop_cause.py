# Evidence for FINDINGS.md, decision 1: why rail rows fell about 21% in the
# second half of 2023.
#
# The number of rows in a period is (stations) x (days) x (ticket types), since
# there is one row per station, per day, per ticket type. So if rows fall, one
# of those three must have fallen. This prints each of them per half-year.
#
# FINDINGS: stations went up (756 -> 769) while ticket types went from 9 to 7,
# and rows per station per day fell from 8.0 to 6.0.
#
# Run from the project root:
#   .venv/bin/python model/investigations/02_row_drop_cause.py

import duckdb

db = duckdb.connect()

print("%-8s %11s %9s %13s %15s" %
      ("period", "rows", "stations", "ticket types", "rows/stn/day"))

for period, rows, stations, types, per_station_day in db.sql("""
        SELECT year(CAST(jour AS DATE)) || '-H' ||
                   CASE WHEN month(CAST(jour AS DATE)) <= 6 THEN 1 ELSE 2 END AS period,
               COUNT(*),
               COUNT(DISTINCT code_stif_arret),
               COUNT(DISTINCT categorie_titre),
               ROUND(COUNT(*) / (COUNT(DISTINCT code_stif_arret) * COUNT(DISTINCT jour)), 2)
        FROM 'data/parquet/rail_counts.parquet'
        GROUP BY 1 ORDER BY 1""").fetchall():
    print("%-8s %11s %9d %13d %15s" %
          (period, format(rows, ","), stations, types, per_station_day))
