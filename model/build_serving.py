# Makes the smaller copy of the database that the public app reads.
#
#   data/model/ridership.duckdb           for building and checking, with its keys
#   data/model/ridership_serving.duckdb   for answering questions, without them
#
# Most of the full file is the indexes DuckDB uses to enforce the primary and
# foreign keys. They check data on its way in. A read-only copy takes nothing
# in, and the keys were already checked when the full database was built.
#
# CREATE TABLE ... AS copies the data but not the descriptions or the
# warnings_between macro, so those are copied one by one. The agent depends on
# both. The full database is only ever read here.
#
# The copy replaces the old one only after it is proved identical, so a failed
# run can never leave a bad copy behind.
#
# Run from the project root:
#   .venv/bin/python model/build_serving.py

import os

import duckdb

FULL = "data/model/ridership.duckdb"
SERVING = "data/model/ridership_serving.duckdb"
UNFINISHED = SERVING + ".unfinished"


def quote(text):
    return "'" + text.replace("'", "''") + "'"


def tables(db, database):
    rows = db.execute("SELECT table_name FROM duckdb_tables()"
                      " WHERE database_name = $1 ORDER BY 1", [database]).fetchall()
    return [row[0] for row in rows]


def columns(db, database, table):
    rows = db.execute("SELECT column_name FROM duckdb_columns()"
                      " WHERE database_name = $1 AND table_name = $2"
                      " ORDER BY column_index", [database, table]).fetchall()
    return [row[0] for row in rows]


def catalog(db, database):
    """Every name, type and description in a database, to compare two of them."""
    found = db.execute("""
        SELECT table_name, comment FROM duckdb_tables()
        WHERE database_name = $1 ORDER BY 1
    """, [database]).fetchall()
    found = found + db.execute("""
        SELECT table_name, column_name, column_index, data_type, comment FROM duckdb_columns()
        WHERE database_name = $1 ORDER BY 1, 3
    """, [database]).fetchall()
    # DuckDB rewrites a macro's text when reading it back, adding double quotes
    # in places. Those do not change what it means.
    found = found + db.execute("""
        SELECT function_name, function_type, parameters, replace(macro_definition, '"', '')
        FROM duckdb_functions()
        WHERE database_name = $1 AND NOT internal ORDER BY 1
    """, [database]).fetchall()
    return found


def fingerprint(db, database, table):
    """A summary of a table's whole content, in any row order.

    hash() turns each row into one number, and the same row always gives the
    same number. Comparing the count, sum and XOR of those numbers needs almost
    no memory, and any changed value in any row changes them.
    """
    names = ['"' + name + '"' for name in columns(db, database, table)]
    row_hash = "hash(" + ", ".join(names) + ")"
    return db.sql("SELECT COUNT(*), SUM(" + row_hash + "::HUGEINT), BIT_XOR(" + row_hash + ")"
                  " FROM " + database + "." + table).fetchone()


if os.path.exists(UNFINISHED):
    os.remove(UNFINISHED)

db = duckdb.connect()
db.sql("SET memory_limit = '2GB'")
db.sql("SET enable_progress_bar = false")
db.sql("ATTACH " + quote(FULL) + " AS full_db (READ_ONLY)")
db.sql("ATTACH " + quote(UNFINISHED) + " AS serving")

# Only tables and table macros are expected. Anything new must be added here,
# rather than silently left out of the copy.
views = db.sql("SELECT COUNT(*) FROM duckdb_views() WHERE database_name = 'full_db' AND NOT internal").fetchone()[0]
if views != 0:
    raise SystemExit("The database now has views: copy them here too.")

macros = db.sql("""
    SELECT function_name, function_type, parameters, macro_definition FROM duckdb_functions()
    WHERE database_name = 'full_db' AND NOT internal
""").fetchall()

# Later macro and table names are looked up in the copy, not the full database.
db.sql("USE serving")

for table in tables(db, "full_db"):
    db.sql("CREATE TABLE " + table + " AS SELECT * FROM full_db." + table)
    print("copied " + table, flush=True)

for table, comment in db.sql("SELECT table_name, comment FROM duckdb_tables()"
                             " WHERE database_name = 'full_db'").fetchall():
    db.sql("COMMENT ON TABLE " + table + " IS " + quote(comment))

for table, column, comment in db.sql("SELECT table_name, column_name, comment FROM duckdb_columns()"
                                     " WHERE database_name = 'full_db'").fetchall():
    db.sql("COMMENT ON COLUMN " + table + '."' + column + '" IS ' + quote(comment))

for name, kind, parameters, definition in macros:
    if kind != "table_macro":
        raise SystemExit("Unexpected kind of function: " + name + " (" + kind + "). Copy it here too.")
    db.sql("CREATE MACRO " + name + "(" + ", ".join(parameters) + ") AS TABLE " + definition)

# --- proof that nothing was lost ---

if catalog(db, "serving") != catalog(db, "full_db"):
    raise SystemExit("The names, types or descriptions of the copy differ from the full database.")

for table in tables(db, "full_db"):
    original = fingerprint(db, "full_db", table)
    copy = fingerprint(db, "serving", table)
    if original != copy:
        raise SystemExit(table + " differs: " + str(original) + " vs " + str(copy))
    print("identical " + table + " (" + format(original[0], ",") + " rows)", flush=True)

# Everything is written into the file, so it can be opened read-only alone.
db.sql("CHECKPOINT serving")
db.close()

os.replace(UNFINISHED, SERVING)

full_size = os.path.getsize(FULL)
serving_size = os.path.getsize(SERVING)
print("%s: %d MB, the full database: %d MB" % (SERVING, serving_size / 1e6, full_size / 1e6))
