# Evidence for FINDINGS.md, decision 6: most hourly profiles add up to 100%,
# a few do not, and the ones that do not cannot be repaired.
#
# A profile is one station (or line), one type of day, one half-year or
# quarter: its hours should add up to 100%. This counts, for each table, the
# profiles that do, the ones over 101% (several profiles stacked under one key,
# with the same hour listed more than once) and the ones under 99% (hours
# missing). It then repeats the rail count split by operator, to show that
# operators were not the cause.
#
# Totals are rounded to 6 decimals: adding the same decimals in a different
# order otherwise tips a few profiles sitting exactly on 99% either side.
#
# FINDINGS: rail 94,772 profiles, 99.8% fine, 149 over, 84 under;
# bus and tram 147,371 profiles, 97.7% fine, 3,004 over, 353 under.
#
# Run from the project root:
#   .venv/bin/python model/investigations/06_hourly_profiles.py

import duckdb

db = duckdb.connect()


def profiles(source, key):
    """Count profiles that add up to 99-101%, over 101%, under 99%."""
    total, fine, over, under, repeated = db.sql("""
        SELECT COUNT(*),
               COUNT(*) FILTER (WHERE total BETWEEN 99 AND 101),
               COUNT(*) FILTER (WHERE total > 101),
               COUNT(*) FILTER (WHERE total < 99),
               COUNT(*) FILTER (WHERE n_rows > n_hours)
        FROM (SELECT ROUND(SUM(TRY_CAST(pourc_validations AS DOUBLE)), 6) AS total,
                     COUNT(*) AS n_rows, COUNT(DISTINCT trnc_horr_60) AS n_hours
              FROM 'data/parquet/""" + source + """.parquet'
              GROUP BY source_file, """ + key + """, cat_jour)
    """).fetchone()
    print("   %7s profiles   %5.1f%% add up to 99-101%%   %5d over   %4d under"
          "   (%d list an hour more than once)"
          % (format(total, ","), 100.0 * fine / total, over, under, repeated))


print("Rail")
profiles("rail_hourly", "zone_id, code_stif_arret")
print("Rail, split by operator and network as well (FINDINGS: changes nothing)")
profiles("rail_hourly", "code_stif_trns, code_stif_res, zone_id, code_stif_arret")
print("Bus and tram")
profiles("surface_hourly", "id_groupoflines, code_stif_ligne")
