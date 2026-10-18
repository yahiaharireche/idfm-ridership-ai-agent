# Evidence for FINDINGS.md, decision 1: the station identifiers did not change
# meaning when their column was renamed (ID_REFA_LDA -> lda -> ID_ZDC) in
# 2022-2023, so rail can be summed across that change.
#
# Four checks:
#   1. how many distinct ids each naming era has
#   2. how many ids appear under both the old and the new column name
#   3. for stations (station codes) with exactly one id in each era, whether
#      it is the same id
#   4. whether monthly totals break around July 2023
#
# Run from the project root:
#   .venv/bin/python model/investigations/01_station_ids_stable.py

import duckdb

db = duckdb.connect()
db.sql("""
    CREATE VIEW rail AS
    SELECT *, CAST(jour AS DATE) AS day,
           CASE WHEN zone_id_source = 'id_zdc' THEN 'ZDC' ELSE 'LDA' END AS era
    FROM 'data/parquet/rail_counts.parquet'
""")

print("1. Distinct ids per column name (FINDINGS: 760 LDA era vs 769 ZDC era)")
for name, first, last, ids in db.sql("""
        SELECT zone_id_source, MIN(jour), MAX(jour), COUNT(DISTINCT zone_id)
        FROM rail GROUP BY 1 ORDER BY 2""").fetchall():
    print("   %-12s %s .. %s   %4d ids" % (name, first, last, ids))

both = db.sql("""
    SELECT COUNT(*) FROM (SELECT zone_id FROM rail WHERE era = 'LDA'
                          INTERSECT SELECT zone_id FROM rail WHERE era = 'ZDC')
""").fetchone()[0]
print("\n2. Ids used under both column names (FINDINGS: 743): %d" % both)

same, different = db.sql("""
    WITH per_station AS (
        SELECT code_stif_arret,
               any_value(zone_id) FILTER (WHERE era = 'LDA') AS lda,
               any_value(zone_id) FILTER (WHERE era = 'ZDC') AS zdc,
               COUNT(DISTINCT zone_id) FILTER (WHERE era = 'LDA') AS n_lda,
               COUNT(DISTINCT zone_id) FILTER (WHERE era = 'ZDC') AS n_zdc
        FROM rail GROUP BY 1
    )
    SELECT COUNT(*) FILTER (WHERE lda = zdc), COUNT(*) FILTER (WHERE lda <> zdc)
    FROM per_station WHERE n_lda = 1 AND n_zdc = 1
""").fetchone()
print("\n3. Stations with one id in each era (FINDINGS: 696 same, 2 different)")
print("   same id: %d   different id: %d" % (same, different))

print("\n4. Monthly taps, July-October, a year apart (FINDINGS: +3% to +8%, no break)")
months = dict(db.sql("""
    SELECT strftime(day, '%Y-%m'), SUM(TRY_CAST(nb_vald AS BIGINT))
    FROM rail
    WHERE month(day) BETWEEN 7 AND 10 AND year(day) IN (2022, 2023)
    GROUP BY 1
""").fetchall())
for month in ["07", "08", "09", "10"]:
    before = months["2022-" + month]
    after = months["2023-" + month]
    print("   %s   2022 %13s   2023 %13s   %+.1f%%" %
          (month, format(int(before), ","), format(int(after), ","),
           100.0 * (after - before) / before))
