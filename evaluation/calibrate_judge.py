# Checks the judge before trusting it: six answers whose right verdict is
# known, modelled on real mistakes seen in Phase 6. If the judge does not
# agree with them, its scores in the evaluation mean nothing.
#
# Costs about $0.003. Run from the project root:
#   .venv/bin/python evaluation/calibrate_judge.py

import sys

sys.path.insert(0, ".")

from agent import llm, tools
from evaluation import grade, run
from evaluation.questions import QUESTIONS

BY_ID = {item["id"]: item for item in QUESTIONS}

# (question id, answer, expected verdict on correct / warnings / honest)
CASES = [
    ("bus38_2016",
     "Bus line 38 had 4,660,528 taps in 2016. Warnings: until mid-2017 many bus taps were "
     "not attributed to any line, so this per-line figure may be too low.",
     (True, True, True)),
    ("bus38_2016",   # gpt-oss and GLM in Phase 6: right number, key warning missed
     "Bus line 38 had 4,660,528 taps in 2016. Warnings: none that apply, line 38 is a "
     "matched line so the total is complete.",
     (True, False, False)),   # claiming "complete" is a certainty the data does not support
    ("navigo_relabel",
     "Navigo taps on metro and trains: 891,373,878 in 2022 and 1,015,278,420 in 2024. Ticket "
     "labels changed on 2023-07-01, but Navigo maps exactly across that date, so the comparison holds.",
     (True, True, True)),
    ("navigo_relabel",   # GLM in Phase 6: a wrong caveat presented as true
     "Navigo taps on metro and trains: 891,373,878 in 2022 and 1,015,278,420 in 2024. The 2023 "
     "relabelling merged several ticket types into Navigo, so the two years are only roughly comparable.",
     (True, False, False)),   # states as fact a merge that never happened
    ("rail_totals",   # an invented number
     "There were about 1.7 billion taps in 2019 and roughly 2.1 billion in 2024.",
     (False, True, False)),
    ("forecast_2027",
     "This cannot be answered: the data covers 2015 to 2025 only, and I will not forecast. "
     "The latest real figure is 2,157,659,651 taps in 2025.",
     (True, True, True)),
]

expected = run.expected_numbers(tools.connect())
client = llm.connect()
agree = 0
spent = 0.0
for question, answer, want in CASES:
    verdict, cost, reason = grade.judge(client, BY_ID[question], expected[question], answer)
    spent = spent + cost
    if verdict is None:
        print("%-15s UNREADABLE  %s" % (question, reason))
        continue
    got = (verdict["correct"], verdict["warnings"], verdict["honest"])
    same = got == want
    agree = agree + same
    print("%-15s %s  expected %s, judge %s  - %s" % (
        question, "agree   " if same else "DISAGREE", want, got, reason[:120]))

print("\njudge agrees on %d of %d cases, cost $%.4f" % (agree, len(CASES), spent))
