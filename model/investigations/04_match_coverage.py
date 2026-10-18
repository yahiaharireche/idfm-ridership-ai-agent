# Evidence for FINDINGS.md, decision 3: station and line identity comes from
# ids, never names.
#
# Three checks:
#   1. names are unreliable: how many station codes carry more than one spelling
#   2. how much of the data each matching method covers, for stations and lines
#   3. the bus and tram line code in the tap data matches nothing in the line
#      reference, so the line id has to be used instead
#
# Run from the project root, after the model is built:
#   .venv/bin/python model/investigations/04_match_coverage.py

import duckdb

db = duckdb.connect()

codes, several = db.sql("""
    SELECT COUNT(*), COUNT(*) FILTER (WHERE spellings > 1)
    FROM (SELECT code_stif_arret, COUNT(DISTINCT upper(trim(libelle_arret))) AS spellings
          FROM 'data/parquet/rail_counts.parquet' GROUP BY 1)
""").fetchone()
print("1. Station codes with more than one spelling (FINDINGS: 419 of 806): %d of %d"
      % (several, codes))
for code, names in db.sql("""
        SELECT code_stif_arret, string_agg(DISTINCT upper(trim(libelle_arret)), ' | ')
        FROM 'data/parquet/rail_counts.parquet' GROUP BY 1
        HAVING COUNT(DISTINCT upper(trim(libelle_arret))) > 1
        ORDER BY 1 LIMIT 4""").fetchall():
    print("   e.g. %-6s %s" % (code, names[:80]))

for table, label in [("dim_station", "stations (FINDINGS: 97.83 / 2.12 / 0.05 / 0.001)"),
                     ("dim_line", "lines (FINDINGS: 88.2 / 9.3 / 2.5)")]:
    print("\n2. Share of taps by matching method, " + label)
    for method, ids, share in db.sql("""
            SELECT match_method, COUNT(*),
                   ROUND(100.0 * SUM(validations) / SUM(SUM(validations)) OVER (), 3)
            FROM 'data/model/""" + table + """.parquet'
            GROUP BY 1 ORDER BY 3 DESC""").fetchall():
        print("   %-18s %5d ids  %8s%%" % (method, ids, share))

codes, matched = db.sql("""
    SELECT COUNT(*), COUNT(*) FILTER (WHERE r.id_line IS NOT NULL)
    FROM (SELECT DISTINCT code_stif_ligne FROM 'data/parquet/surface_counts.parquet') s
    LEFT JOIN (SELECT DISTINCT id_line FROM 'data/parquet/extra/referentiel-des-lignes.parquet') r
           ON s.code_stif_ligne = r.id_line
""").fetchone()
print("\n3. Bus and tram line codes found in the line reference (FINDINGS: 0 of 726): %d of %d"
      % (matched, codes))
