# Evidence for FINDINGS.md, decision 4: today's station and line references
# are good enough to name every past year.
#
# The official station list (GTFS) describes the network at one moment. If
# many stations had closed since 2015, it could not name the early years and
# we would need old copies of it. This measures, year by year, how much of the
# data today's references can name.
#
# FINDINGS: GTFS covers 2026-09-24 .. 2026-10-26; rail is >= 99.89% named in
# every year; lines missing from today's reference are 1.5-4.7% per year.
#
# Run from the project root, after the model is built:
#   .venv/bin/python model/investigations/05_reference_coverage_by_year.py

import duckdb

db = duckdb.connect("data/model/ridership.duckdb", read_only=True)

first, last = db.sql("""
    SELECT MIN(start_date), MAX(end_date) FROM 'data/parquet/gtfs/calendar.parquet'
""").fetchone()
print("Period the official network files describe: %s .. %s\n" % (first, last))

print("Rail: share of each year's taps that have an official station name")
print("  year   exact id   via code   named   unknown")
for row in db.sql("""
        SELECT year,
               ROUND(100.0 * SUM(taps) FILTER (WHERE station_match = 'exact_id') / SUM(taps), 2),
               ROUND(100.0 * coalesce(SUM(taps) FILTER (WHERE station_match = 'via_station_code'), 0) / SUM(taps), 2),
               ROUND(100.0 * SUM(taps) FILTER (WHERE station_match IN ('exact_id', 'via_station_code')) / SUM(taps), 2),
               ROUND(100.0 * coalesce(SUM(taps) FILTER (WHERE station_match = 'unknown_station'), 0) / SUM(taps), 2)
        FROM fact_rail_daily GROUP BY 1 ORDER BY 1""").fetchall():
    print("  %d %9s%% %9s%% %6s%% %8s%%" % row)

print("\nBus and tram: share of each year's taps by line match")
print("  year   named   unknown line   not in reference")
for row in db.sql("""
        SELECT year,
               ROUND(100.0 * SUM(taps) FILTER (WHERE line_match = 'exact_id') / SUM(taps), 1),
               ROUND(100.0 * coalesce(SUM(taps) FILTER (WHERE line_match = 'unknown_line'), 0) / SUM(taps), 1),
               ROUND(100.0 * coalesce(SUM(taps) FILTER (WHERE line_match = 'unmatched'), 0) / SUM(taps), 1)
        FROM fact_surface_daily GROUP BY 1 ORDER BY 1""").fetchall():
    print("  %d  %5s%%  %12s%%  %16s%%" % row)
