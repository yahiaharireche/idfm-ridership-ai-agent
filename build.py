# Rebuilds everything, from the original files to the final database, then
# runs the tests. One command instead of eight scripts run in the right order.
#
#   .venv/bin/python build.py              rebuild from data/raw/ and test
#   .venv/bin/python build.py --download   download the originals first
#
# Downloading is never done by default: it replaces data/raw/ with whatever
# IDFM publishes today, which may differ from what the rest was built on.
# There is no download script for the metro and train data (reseau-ferre), so
# that folder is never refreshed by this command.
#
# It stops at the first step that fails, so a broken step can never feed bad
# data to the steps after it.
#
# Run from the project root.

import subprocess
import sys
import time

PYTHON = ".venv/bin/python"

DOWNLOAD = [
    "ingest/download_surface.py",
    "ingest/unzip_surface.py",
    "ingest/download_gtfs.py",
    "ingest/unzip_gtfs.py",
    "ingest/download_extra.py",
]

BUILD = [
    "pipeline/profile_files.py",      # Phase 3: record each original file's format
    "pipeline/load.py",               #          write the clean copy
    "pipeline/to_parquet.py",         #          convert it to Parquet
    "pipeline/validate.py",           #          check the clean copy
    "model/build_dimensions.py",      # Phase 4: what each code means
    "model/build_facts.py",           #          the final tables, with their keys
    "model/build_quality_flags.py",   #          the warnings
    "model/build_descriptions.py",    #          a description on every column
]

steps = []
if "--download" in sys.argv:
    steps = steps + DOWNLOAD
steps = steps + BUILD

started = time.time()

for script in steps:
    print(">>> " + script, flush=True)
    step_started = time.time()
    # The step's own output is captured and only shown if it fails, so the
    # summary stays readable.
    result = subprocess.run([PYTHON, script], capture_output=True, text=True)
    seconds = time.time() - step_started

    if result.returncode != 0:
        print(result.stdout[-3000:])
        print(result.stderr[-3000:])
        print("FAILED: " + script + " (after %.0f s). Later steps were not run." % seconds)
        sys.exit(1)

    print("    ok  %.0f s" % seconds, flush=True)

print(">>> tests", flush=True)
result = subprocess.run([PYTHON, "-m", "pytest", "tests", "-q"])
if result.returncode != 0:
    print("FAILED: the tests. The database was rebuilt, but something in it is wrong.")
    sys.exit(1)

print("\nAll done in %.0f s." % (time.time() - started))
