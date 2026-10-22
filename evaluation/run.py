# Runs the evaluation: every question in evaluation/questions.py, asked to the
# agent several times, each answer graded by evaluation/grade.py.
#
#   .venv/bin/python evaluation/run.py baseline          run and save as "baseline"
#   .venv/bin/python evaluation/run.py after_fix         run, save, compare with baseline
#   .venv/bin/python evaluation/run.py after_fix id1,id2 the same, for some questions only
#   .venv/bin/python evaluation/run.py --regrade runs/eval_x.json label
#                                                        grade saved answers again
#
# The scores are saved to evaluation/results/<label>.json (small, kept in git,
# so changes can be compared over time); every answer and trace goes to runs/.
#
# Money: the whole phase has a fixed budget, checked against OpenRouter's own
# count of what this key has spent, so it covers every call - the agent, the
# judge, and anything spent outside this script. A question is only started if
# the budget still has room for it and for every question already running,
# each counted at its worst case. So the budget cannot be overshot by questions
# in flight.

import datetime
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

sys.path.insert(0, ".")

from agent import agent, llm, tools
from evaluation import grade
from evaluation.questions import QUESTIONS

BUDGET_FILE = "evaluation/budget.json"
RESULTS = "evaluation/results"
REPEATS = 2
WORKERS = 10

# The most one question can cost, agent and judge together. The baseline's
# most expensive run cost $0.052 (ten steps, long tool results); rounded up,
# so the budget holds even for an unusually long run.
WORST_CASE = 0.06


def key_spent():
    """What this API key has spent in total, according to OpenRouter."""
    request = urllib.request.Request("https://openrouter.ai/api/v1/key",
                                     headers={"Authorization": "Bearer " + llm.read_key()})
    return json.load(urllib.request.urlopen(request, timeout=30))["data"]["usage"]


def room_left():
    """Dollars the phase may still spend.

    OpenRouter's count can lag a few minutes behind, so what the evaluations
    recorded themselves (budget.json, "recorded") is used when it is higher.
    """
    budget = json.load(open(BUDGET_FILE, encoding="utf-8"))
    spent = max(key_spent() - budget["key_spent_at_start"], budget.get("recorded", 0.0))
    return budget["limit"] - spent


def record(amount):
    """Adds what a run spent to budget.json."""
    budget = json.load(open(BUDGET_FILE, encoding="utf-8"))
    budget["recorded"] = round(budget.get("recorded", 0.0) + amount, 6)
    with open(BUDGET_FILE, "w", encoding="utf-8") as f:
        json.dump(budget, f, indent=2)


# Each worker process opens its own connections once.
worker_client = None
worker_db = None


def start_worker():
    global worker_client, worker_db
    worker_client = llm.connect()
    worker_db = tools.connect()


def run_job(job):
    """Ask one question once, and grade the answer. Runs inside a worker."""
    item, attempt, expected = job
    run = agent.ask(item["question"], "deepseek", worker_client, worker_db)
    checks = grade.checks(item, expected, run["answer"])
    verdict, judge_cost, reason = grade.judge(worker_client, item, expected, run["answer"])
    return {
        "question": item["id"],
        "group": item["group"],
        "attempt": attempt,
        "checks": checks,
        "judge": verdict,
        "judge_reason": reason,
        "passed": passed(run["answer"], checks, verdict),
        "steps": run["steps"],
        "seconds": run["seconds"],
        "cost": run["cost"] + judge_cost,
        "file": run["file"],
        "answer": run["answer"],
    }


def passed(answer, checks, verdict):
    """True, False, or None when it could not be graded.

    An answer passes when every automatic check passes and the judge finds it
    correct, with the right warnings, and honest. A run stopped before
    answering always fails. If the judge's verdict could not be read, the
    automatic checks decide alone - unless there are none, and then the
    answer is not graded rather than counted as passed.
    """
    if answer.startswith("Stopped:"):
        return False
    if not all(checks.values()):
        return False
    if verdict is None:
        return True if checks else None
    return verdict["correct"] and verdict["warnings"] and verdict["honest"]


def expected_numbers(db):
    """The right numbers for each question, computed from the database now."""
    expected = {}
    for item in QUESTIONS:
        values = []
        if item.get("numbers_sql"):
            for row in db.sql(item["numbers_sql"]).fetchall():
                values.append(int(row[0]))
        expected[item["id"]] = values
    return expected


def evaluate(label, only=None):
    """Runs the evaluation and saves it as label. only: a list of question
    ids, to run just those."""
    questions = [item for item in QUESTIONS if not only or item["id"] in only]
    left = room_left()
    print("Budget left for this phase: $%.4f" % left)
    # Room for at least one question; how many run at once is then limited
    # by the budget below.
    if left < WORST_CASE:
        raise SystemExit("Not enough budget left to run safely.")

    expected = expected_numbers(tools.connect())
    jobs = []
    for item in questions:
        for attempt in range(1, REPEATS + 1):
            jobs.append((item, attempt, expected[item["id"]]))

    results = []
    spent = 0.0
    skipped = 0
    started = time.time()
    with ProcessPoolExecutor(max_workers=WORKERS, initializer=start_worker) as pool:
        running = set()
        while jobs or running:
            # Start questions while the budget has room for them and for
            # everything already running, each at its worst case.
            while jobs and len(running) < WORKERS:
                if spent + (len(running) + 1) * WORST_CASE > left:
                    if not running:
                        skipped = len(jobs)
                        jobs = []
                        print("Budget reached: %d runs not started." % skipped)
                    break      # otherwise wait for a running question to finish
                running.add(pool.submit(run_job, jobs.pop(0)))
            if not running:
                break

            done, running = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                try:
                    result = future.result()
                except Exception as error:
                    print("  !! a run failed: " + str(error)[:200])
                    continue
                spent = spent + result["cost"]
                record(result["cost"])
                results.append(result)
                verdict = {True: "PASS", False: "fail", None: "not graded"}[result["passed"]]
                print("  %3d/%d  %-18s run %d  %-10s %d steps  $%.4f  (spent $%.4f)" % (
                    len(results), len(questions) * REPEATS, result["question"], result["attempt"],
                    verdict, result["steps"], result["cost"], spent))

    order = [item["id"] for item in questions]
    results.sort(key=lambda r: (order.index(r["question"]), r["attempt"]))
    summary = summarise(results)
    summary["label"] = label
    summary["date"] = datetime.datetime.now().isoformat(timespec="seconds")
    summary["runs_not_started"] = skipped
    summary["minutes"] = round((time.time() - started) / 60, 1)

    os.makedirs(RESULTS, exist_ok=True)
    with open(RESULTS + "/" + label + ".json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    os.makedirs(agent.RUNS, exist_ok=True)
    full = agent.RUNS + "/eval_" + label + "_" + datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".json"
    with open(full, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "results": results}, f, ensure_ascii=False, indent=2)

    report(summary)
    print("\nAnswers and traces: " + full)
    print("Budget left for this phase: $%.4f" % room_left())
    return summary


def regrade(source, label):
    """Grades the answers of an earlier evaluation again, without asking the
    agent again: for when the questions' references or the grading changed,
    so that a before/after comparison measures the agent, not the grader."""
    old = json.load(open(source, encoding="utf-8"))
    by_id = {item["id"]: item for item in QUESTIONS}
    expected = expected_numbers(tools.connect())
    client = llm.connect()
    left = room_left()
    spent = 0.0
    results = []
    for result in old["results"]:
        if spent + 0.01 > left:
            raise SystemExit("Budget reached while regrading.")
        item = by_id[result["question"]]
        checks = grade.checks(item, expected[item["id"]], result["answer"])
        verdict, cost, reason = grade.judge(client, item, expected[item["id"]], result["answer"])
        spent = spent + cost
        record(cost)
        result = dict(result, checks=checks, judge=verdict, judge_reason=reason,
                      passed=passed(result["answer"], checks, verdict), cost=result["cost"] + cost)
        results.append(result)
        print("  %-18s run %d  %s" % (result["question"], result["attempt"],
                                      {True: "PASS", False: "fail", None: "not graded"}[result["passed"]]))
    summary = summarise(results)
    summary.update(label=label, date=datetime.datetime.now().isoformat(timespec="seconds"),
                   runs_not_started=0, minutes=old["summary"]["minutes"],
                   regraded_from=source, regrade_cost=round(spent, 4))
    with open(RESULTS + "/" + label + ".json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    full = source.replace(".json", "_regraded.json")
    with open(full, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "results": results}, f, ensure_ascii=False, indent=2)
    report(summary)
    print("\nregrading cost $%.4f; budget left $%.4f" % (spent, room_left()))


def rate(results):
    """Percentage passed, among the answers that could be graded."""
    graded = [r for r in results if r["passed"] is not None]
    if not graded:
        return None
    count = 0
    for r in graded:
        if r["passed"]:
            count = count + 1
    return round(100.0 * count / len(graded), 1)


def summarise(results):
    """Pass rates overall, per group, per check and per question, plus
    consistency (did both runs of a question get the same verdict?) and cost."""
    summary = {"runs": len(results), "pass_rate": rate(results),
               "not_graded": len([r for r in results if r["passed"] is None]),
               "groups": {}, "checks": {}, "judge": {}, "questions": {}}

    for group in sorted(set(r["group"] for r in results)):
        summary["groups"][group] = rate([r for r in results if r["group"] == group])

    names = sorted(set(name for r in results for name in r["checks"]))
    for name in names:
        values = [r["checks"][name] for r in results if name in r["checks"]]
        summary["checks"][name] = round(100.0 * sum(values) / len(values), 1)
    for name in ["correct", "warnings", "honest"]:
        values = [r["judge"][name] for r in results if r["judge"]]
        summary["judge"][name] = round(100.0 * sum(values) / len(values), 1) if values else None
    summary["judge"]["unreadable"] = len([r for r in results if r["judge"] is None])

    consistent = 0
    asked = 0
    for question in dict.fromkeys(r["question"] for r in results):
        runs = [r for r in results if r["question"] == question]
        summary["questions"][question] = {
            "passed": str(sum(1 for r in runs if r["passed"])) + "/" + str(len(runs)),
            "pass_rate": rate(runs),
            "failed_checks": sorted(set(n for r in runs for n, ok in r["checks"].items() if not ok)),
            "judge_reasons": [r["judge_reason"] for r in runs if not r["passed"]],
        }
        if len(runs) > 1:
            asked = asked + 1
            if len(set(r["passed"] for r in runs)) == 1:
                consistent = consistent + 1
    summary["consistency"] = round(100.0 * consistent / asked, 1) if asked else None

    costs = [r["cost"] for r in results]
    steps = sorted(r["steps"] for r in results)
    summary["cost_total"] = round(sum(costs), 4)
    summary["cost_per_question"] = round(sum(costs) / len(costs), 4) if costs else None
    summary["steps_median"] = steps[len(steps) // 2] if steps else None
    return summary


def report(summary):
    baseline = None
    path = RESULTS + "/baseline.json"
    if summary["label"] != "baseline" and os.path.exists(path):
        baseline = json.load(open(path, encoding="utf-8"))

    def line(name, now, before):
        change = ""
        if before is not None and now is not None:
            change = "  (%+.1f)" % (now - before)
        print("  %-22s %6s%s" % (name, "-" if now is None else str(now) + "%", change))

    print("\n=== " + summary["label"] + ": " + str(summary["runs"]) + " runs ===")
    line("passed", summary["pass_rate"], baseline and baseline["pass_rate"])
    line("consistency", summary["consistency"], baseline and baseline["consistency"])
    print("  not graded             %d" % summary["not_graded"])
    print("\nby group")
    for group, value in summary["groups"].items():
        line(group, value, baseline and baseline["groups"].get(group))
    print("\nautomatic checks")
    for name, value in summary["checks"].items():
        line(name, value, baseline and baseline["checks"].get(name))
    print("\njudge")
    for name in ["correct", "warnings", "honest"]:
        line(name, summary["judge"][name], baseline and baseline["judge"].get(name))
    print("  unreadable verdicts    %d" % summary["judge"]["unreadable"])
    print("\nquestions not passed every time")
    for question, info in summary["questions"].items():
        before = ""
        if baseline and question in baseline["questions"]:
            before = "   (baseline " + baseline["questions"][question]["passed"] + ")"
        if not info["passed"].startswith(str(REPEATS)):
            print("  %-18s %s  checks failed: %s%s" % (question, info["passed"], ", ".join(info["failed_checks"]) or "-", before))
            for reason in info["judge_reasons"]:
                print("      judge: " + reason[:160])
    print("\ncost $%.4f ($%.4f per question), median %s steps, %.1f min" % (
        summary["cost_total"], summary["cost_per_question"], summary["steps_median"], summary["minutes"]))


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--regrade":
        regrade(sys.argv[2], sys.argv[3])
        raise SystemExit
    if len(sys.argv) not in (2, 3):
        raise SystemExit("Usage: evaluation/run.py <label> [question_id,question_id,...]")
    evaluate(sys.argv[1], sys.argv[2].split(",") if len(sys.argv) == 3 else None)
