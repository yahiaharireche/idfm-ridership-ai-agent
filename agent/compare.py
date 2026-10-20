# Asks the same test questions to every model and prints a summary.
#
# Each question has a trap: something a fluent but careless answer gets wrong.
# The right numbers were computed by hand from the database beforehand. The
# check here is only a first pass (are the right numbers in the answer?); the
# answers themselves are saved in runs/ and still need reading.
#
# Run from the project root:  .venv/bin/python agent/compare.py [model ...]

import datetime
import json
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, ".")

from agent import agent, llm, tools

QUESTIONS = [
    {
        "id": "rail_totals",
        "question": "How many metro and train taps were there in 2019, and in 2024?",
        "trap": "basic: right table, right totals",
        "expected": ["1695721547", "1905998830"],
    },
    {
        "id": "covid_drop",
        "question": "How much did bus and tram ridership drop in April 2020 compared with April 2019?",
        "trap": "a comparison over a short period",
        "expected": ["94545573", "1428141"],
    },
    {
        "id": "top_stations",
        "question": "Which 5 stations had the most taps in 2023?",
        "trap": "group by station id, not by name",
        "expected": ["Saint-Lazare", "Gare de Lyon", "La Défense", "Les Halles", "Montparnasse"],
    },
    {
        "id": "navigo_relabel",
        "question": "How many Navigo taps were there on the metro and trains in 2022, compared with 2024?",
        "trap": "ticket labels changed on 2023-07-01: use ticket_category",
        "expected": ["891373878", "1015278420"],
    },
    {
        "id": "bus38_2016",
        "question": "How many taps were there on bus line 38 in 2016?",
        "trap": "2015-2017: half of bus taps have no line, small counts hidden; must warn",
        "expected": ["4660528"],
    },
    {
        "id": "timetable",
        "question": "At what time did bus 38 arrive at Gare du Nord on 15 March 2017?",
        "trap": "not in the data: must refuse and say why",
        "expected": [],
    },
]


def numbers_only(text):
    """The answer with thousands separators removed: 1,695,721,547 and
    1 695 721 547 both become 1695721547, so they can be searched for."""
    return re.sub(r"(?<=\d)[ ,.'   ](?=\d{3})", "", text)


def found(answer, expected):
    """How many of the expected numbers or names appear in the answer."""
    # Some models write a non-breaking hyphen (Saint‑Lazare) or a narrow
    # non-breaking space instead of the plain characters.
    text = answer.replace("‑", "-").replace(" ", " ").replace(" ", " ")
    text = numbers_only(text)
    count = 0
    for item in expected:
        if item.lower() in text.lower():
            count = count + 1
    return count


# Each worker process keeps its own connection to the model service and to
# the database, opened once when the worker starts.
worker_client = None
worker_db = None


def start_worker():
    global worker_client, worker_db
    worker_client = llm.connect()
    worker_db = tools.connect()


def run_job(job):
    """One question, one model, one attempt - run inside a worker."""
    model, item, attempt = job
    return agent.ask(item["question"], model, worker_client, worker_db)


def compare(questions, models, repeats=1, budget=None, name="compare", workers=1):
    """Asks every question to every model, repeats times each, and saves and
    prints a summary.

    workers: how many questions run at the same time, each in its own process.
    budget: once the money spent reaches it, questions not started yet are
    cancelled (the ones already running still finish).
    """
    jobs = []
    for model in models:
        for item in questions:
            for attempt in range(1, repeats + 1):
                jobs.append((model, item, attempt))

    results = []
    spent = 0.0
    with ProcessPoolExecutor(max_workers=workers, initializer=start_worker) as pool:
        futures = {}
        for job in jobs:
            futures[pool.submit(run_job, job)] = job

        for future in as_completed(futures):
            model, item, attempt = futures[future]
            if future.cancelled():
                continue
            try:
                run = future.result()
            except Exception as error:
                print("  !! %s / %s / run %d failed: %s" % (model, item["id"], attempt, str(error)[:200]))
                continue

            spent = spent + run["cost"]
            hits = found(run["answer"], item["expected"])
            results.append({
                "model": model,
                "question": item["id"],
                "attempt": attempt,
                "found": str(hits) + "/" + str(len(item["expected"])),
                "steps": run["steps"],
                "seconds": run["seconds"],
                "cost": run["cost"],
                "file": run["file"],
                "answer": run["answer"],
            })
            print("  -> %d/%d done: %s / %s / run %d: found %d/%d, %d steps, %.1f s, $%.4f  (total $%.4f)" % (
                len(results), len(jobs), model, item["id"], attempt, hits, len(item["expected"]),
                run["steps"], run["seconds"], run["cost"], spent))

            if budget is not None and spent >= budget:
                print("\nBudget of $%.2f reached: cancelling the questions not started yet." % budget)
                for other in futures:
                    other.cancel()

    # back in a fixed order, whatever order they finished in
    order = {}
    for i, item in enumerate(questions):
        order[item["id"]] = i
    results.sort(key=lambda r: (models.index(r["model"]), order[r["question"]], r["attempt"]))
    return finish(questions, results, name)


def finish(questions, results, name):
    """Saves the results to runs/ and prints the summary table."""
    os.makedirs(agent.RUNS, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = agent.RUNS + "/" + name + "_" + stamp + ".json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"questions": questions, "results": results}, f, ensure_ascii=False, indent=2)

    print("\n%-9s %-17s %3s %6s %6s %7s %8s" % ("model", "question", "run", "found", "steps", "seconds", "cost"))
    total = 0.0
    for r in results:
        total = total + r["cost"]
        print("%-9s %-17s %3d %6s %6d %7.1f %8.4f" % (
            r["model"], r["question"], r["attempt"], r["found"], r["steps"], r["seconds"], r["cost"]))
    print("\ntotal $%.4f, saved to %s" % (total, path))
    return results


if __name__ == "__main__":
    compare(QUESTIONS, sys.argv[1:] or list(llm.MODELS))
