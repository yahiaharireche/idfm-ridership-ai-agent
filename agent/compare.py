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


models = sys.argv[1:] or list(llm.MODELS)
client = llm.connect()
db = tools.connect()
results = []

for model in models:
    for item in QUESTIONS:
        print("\n=== " + model + " / " + item["id"])
        run = agent.ask(item["question"], model, client, db)
        hits = found(run["answer"], item["expected"])
        results.append({
            "model": model,
            "question": item["id"],
            "found": str(hits) + "/" + str(len(item["expected"])),
            "steps": run["steps"],
            "seconds": run["seconds"],
            "cost": run["cost"],
            "file": run["file"],
            "answer": run["answer"],
        })
        print("  -> found %d/%d, %d steps, %.1f s, $%.4f" % (
            hits, len(item["expected"]), run["steps"], run["seconds"], run["cost"]))

os.makedirs(agent.RUNS, exist_ok=True)
stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
path = agent.RUNS + "/compare_" + stamp + ".json"
with open(path, "w", encoding="utf-8") as f:
    json.dump({"questions": QUESTIONS, "results": results}, f, ensure_ascii=False, indent=2)

print("\n%-9s %-15s %6s %6s %7s %8s" % ("model", "question", "found", "steps", "seconds", "cost"))
for r in results:
    print("%-9s %-15s %6s %6d %7.1f %8.4f" % (
        r["model"], r["question"], r["found"], r["steps"], r["seconds"], r["cost"]))
print("\nsaved to " + path)
