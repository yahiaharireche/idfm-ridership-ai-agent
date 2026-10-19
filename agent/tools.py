# The three tools the AI agent will be allowed to use on the ridership
# database, and nothing else:
#
#   describe_database     which tables and columns exist, and what they mean
#   run_query             run one SQL question and get the result back
#   warnings_for_period   the known problems in the data for a date range
#
# The model never touches the database itself. It asks for one of these, our
# program runs it, and the result goes back to the model as text.
#
# The safety lives here, not in the model's instructions. A model can be
# tricked into asking for something harmful; these functions must make sure
# that whatever it asks for, nothing harmful can happen.

import datetime
import threading

import duckdb

DATABASE = "data/model/ridership.duckdb"

# A query running longer than this is stopped. The biggest sensible query here
# (a sum over all 20 million rail rows) takes under a second.
TIMEOUT_SECONDS = 10

# At most this many rows go back to the model. Every row is text it has to
# reread at each later step, and a question is answered by a few totals, not
# by thousands of raw rows.
MAX_ROWS = 200

# The period the tap data covers.
FIRST_DAY = datetime.date(2015, 1, 1)
LAST_DAY = datetime.date(2025, 12, 31)


def connect():
    """A connection that can only read the ridership database.

    read_only               nothing can be created, changed or deleted
    enable_external_access  no other files, no internet, no extensions: a
                            query cannot read /etc/passwd or a web page
    memory_limit            one heavy query cannot take all the computer's memory
    lock_configuration      a query cannot switch any of the above back off
                            (with SET or RESET)

    The progress bar is turned off too: it only clutters the terminal.
    """
    db = duckdb.connect(DATABASE, read_only=True, config={
        "enable_external_access": False,
        "memory_limit": "4GB",
    })
    # These two can only be set once connected. The lock goes last, so it
    # freezes everything above it.
    db.sql("SET enable_progress_bar = false")
    db.sql("SET lock_configuration = true")
    return db


def describe_database(db, table=None):
    """What is in the database, in words.

    Without a table name: every table, its size and what it holds.
    With a table name: every column of that table, its type and its meaning.

    Two levels instead of everything at once, because each result is added to
    what the model has to reread at every step: the full list of columns is
    about 8,000 characters, and the model usually needs one or two tables.

    The descriptions are the ones written in model/descriptions.csv and stored
    in the database with COMMENT ON in Phase 4.
    """
    if table is None:
        rows = db.sql("""
            SELECT table_name, estimated_size, comment
            FROM duckdb_tables() ORDER BY table_name
        """).fetchall()

        lines = ["Tables (ask for one by name to see its columns):"]
        for name, size, comment in rows:
            lines.append("- " + name + " (" + format(size, ",") + " rows): " + comment)
        lines.append("Known problems for a period: warnings_between('YYYY-MM-DD', 'YYYY-MM-DD'), "
                     "or the warnings_for_period tool.")
        return "\n".join(lines)

    # $1 is a query parameter: the table name is passed separately from the
    # SQL, so whatever text the model sends, it is only ever compared as a
    # name and never run as SQL.
    rows = db.execute("""
        SELECT column_name, data_type, comment
        FROM duckdb_columns() WHERE table_name = $1
        ORDER BY column_index
    """, [table]).fetchall()

    if not rows:
        names = []
        for row in db.sql("SELECT table_name FROM duckdb_tables() ORDER BY 1").fetchall():
            names.append(row[0])
        return ("Error: there is no table called '" + str(table) + "'. "
                "The tables are: " + ", ".join(names) + ".")

    lines = ["Columns of " + table + ":"]
    for name, data_type, comment in rows:
        lines.append("- " + name + " (" + data_type + "): " + comment)
    return "\n".join(lines)


def run_query(db, sql):
    """Run one SQL question and return the result as text.

    Every problem comes back as text starting with "Error:", never as a crash,
    so the model can read what went wrong and try again.
    """
    # --- 1. exactly one statement, and it must be a SELECT ---
    # DuckDB's own parser splits the text into statements and says what kind
    # each one is. This is more reliable than looking for words like DELETE,
    # which can also appear inside a station name.
    try:
        statements = db.extract_statements(sql)
    except duckdb.Error as error:
        return "Error: the SQL could not be read. " + short(error)

    if len(statements) != 1:
        return ("Error: send exactly one SQL statement, got " +
                str(len(statements)) + ".")
    if statements[0].type != duckdb.StatementType.SELECT:
        return ("Error: only SELECT queries are allowed (this one is " +
                statements[0].type.name + "). The database can only be read.")

    # --- 2. run it, with a time limit ---
    # A timer runs in the background; if the query is still going when it
    # fires, interrupt() stops it and DuckDB raises InterruptException.
    timer = threading.Timer(TIMEOUT_SECONDS, db.interrupt)
    timer.start()
    try:
        result = db.execute(sql)
        columns = []
        for column in result.description:
            columns.append(column[0])
        rows = result.fetchmany(MAX_ROWS + 1)

        # --- 3. more rows than we send back: say how many there were ---
        total = len(rows)
        if total > MAX_ROWS:
            query = sql.strip().rstrip(";")
            total = db.execute("SELECT COUNT(*) FROM (" + query + ")").fetchone()[0]
            rows = rows[:MAX_ROWS]
    except duckdb.InterruptException:
        return ("Error: the query took more than " + str(TIMEOUT_SECONDS) +
                " seconds and was stopped. Aggregate (SUM, COUNT, GROUP BY) "
                "or filter on fewer rows.")
    except duckdb.Error as error:
        return "Error: " + short(error)
    finally:
        timer.cancel()

    # --- 4. the result, as plain text ---
    lines = [" | ".join(columns)]
    for row in rows:
        values = []
        for value in row:
            values.append("NULL" if value is None else str(value))
        lines.append(" | ".join(values))

    if total > MAX_ROWS:
        lines.append("(showing the first " + str(MAX_ROWS) + " of " +
                     str(total) + " rows - aggregate or filter to see the rest)")
    else:
        lines.append("(" + str(total) + " rows)")
    return "\n".join(lines)


def warnings_for_period(db, start, end):
    """The known problems in the data that overlap a period, as text.

    start and end are dates written YYYY-MM-DD. The warnings come from the
    warnings_between table macro built in Phase 4, so this tool and a query
    the model writes itself always agree.
    """
    # Dates are checked here, before any SQL, so a mistake gets a message the
    # model can act on instead of a database error.
    try:
        start_day = datetime.date.fromisoformat(start)
        end_day = datetime.date.fromisoformat(end)
    except (TypeError, ValueError):
        return ("Error: start and end must be real dates written YYYY-MM-DD, "
                "got '" + str(start) + "' and '" + str(end) + "'.")
    if start_day > end_day:
        return "Error: start (" + start + ") is after end (" + end + ")."

    if end_day < FIRST_DAY or start_day > LAST_DAY:
        return ("Error: there is no data between " + start + " and " + end +
                ". The data covers " + str(FIRST_DAY) + " to " + str(LAST_DAY) + ".")

    lines = []
    if start_day < FIRST_DAY or end_day > LAST_DAY:
        lines.append("Note: the data only covers " + str(FIRST_DAY) + " to " +
                     str(LAST_DAY) + ". There are no taps outside that period.")

    # The dates go in as query parameters, never pasted into the SQL.
    rows = db.execute("""
        SELECT flag_id, "table", affects, start_date, end_date, description,
               rows_affected, taps_affected
        FROM warnings_between($1, $2)
        ORDER BY "table", flag_id
    """, [start_day, end_day]).fetchall()

    if not rows:
        lines.append("No known problems between " + start + " and " + end + ".")
        return "\n".join(lines)

    lines.append(str(len(rows)) + " known problems between " + start + " and " + end + ":")
    for flag_id, table, affects, first, last, description, n_rows, n_taps in rows:
        size = format(n_rows, ",") + " rows"
        if n_taps is not None:
            size = size + ", " + format(n_taps, ",") + " taps"
        lines.append("- " + flag_id + " (" + table + ", " + str(first) + " to " +
                     str(last) + ", makes " + affects + " unreliable; " + size +
                     "): " + description)
    return "\n".join(lines)


def short(error):
    """A DuckDB error message, trimmed so it does not flood the model."""
    return str(error)[:500]
