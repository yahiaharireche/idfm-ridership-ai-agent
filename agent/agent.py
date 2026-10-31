# The agent loop: a question goes in, a defensible answer comes out.
#
#   1. send the conversation (instructions, question, everything so far) and
#      the tool descriptions to the model
#   2. if it asks for tools, run them and add their results to the conversation
#   3. repeat, until it answers without asking for a tool, or MAX_STEPS is reached
#
# Every run is saved to runs/, step by step, so any answer can be traced back
# to the exact queries and results it came from.
#
# Run from the project root:
#   .venv/bin/python agent/agent.py "How many metro and train taps were there in 2019?"

import datetime
import json
import os
import sys
import time

sys.path.insert(0, ".")   # so "agent" can be imported when run as a script

from agent import llm, prompt, settings, tools

# A model that has not answered after this many steps is stopped. A normal
# question needs three to six: look at a table, query, check warnings, answer.
MAX_STEPS = settings.MAX_STEPS

# The longest reply allowed per step, reasoning included. It also caps what a
# single step can cost.
MAX_TOKENS = settings.MAX_TOKENS

# Once a question has cost this much, the model must answer with what it has.
MAX_COST = settings.MAX_COST_PER_QUESTION

MAX_QUESTION_LENGTH = settings.MAX_QUESTION_LENGTH

# Counted for a step whose cost the service did not report. Counting it as zero
# would let a question run past MAX_COST unnoticed. The dearest step ever
# measured cost $0.01.
UNKNOWN_STEP_COST = 0.01

RUNS = settings.RUNS

UNAVAILABLE = "Stopped: the model could not be reached. Please try again in a moment."


def check_question(question):
    """Why a question must be refused, as a message, or None if it is fine.

    This runs before any model call, so a refused question costs nothing.
    """
    if not isinstance(question, str) or not question.strip():
        return "Please type a question."
    if len(question) > MAX_QUESTION_LENGTH:
        return ("This question is too long (%d characters). Please keep it under %d."
                % (len(question), MAX_QUESTION_LENGTH))
    return None


def total_cost(steps):
    """What the steps so far have cost, in dollars."""
    total = 0.0
    for record in steps:
        if record["cost"] is None:
            total = total + UNKNOWN_STEP_COST
        else:
            total = total + record["cost"]
    return total


def ask(question, client=None, db=None):
    """Runs the agent on one question and returns what happened, as a dict."""
    problem = check_question(question)
    if problem:
        return {"question": question, "model": llm.MODEL, "answer": problem, "steps": 0, "seconds": 0.0,
                "cost": 0.0, "limit_reached": None, "error": None, "step_details": [], "messages": [],
                "file": None}

    client = client or llm.connect()
    db = db or tools.connect()

    messages = [
        {"role": "system", "content": prompt.system_prompt(db)},
        {"role": "user", "content": question},
    ]
    steps = []
    answer = None
    limit_reached = None
    error_detail = None
    started = time.time()

    for step in range(1, MAX_STEPS + 1):
        step_started = time.time()

        # On the last step, no more tools: the model must answer with what it
        # has, rather than be cut off with nothing (which happened in the
        # evaluation, on questions where it kept exploring). The last step is
        # either the step limit or the cost limit, whichever comes first.
        if total_cost(steps) >= MAX_COST:
            limit_reached = "cost"
        elif step == MAX_STEPS:
            limit_reached = "steps"

        last = limit_reached is not None
        if last:
            name = "Cost" if limit_reached == "cost" else "Step"
            messages.append({"role": "user", "content": (
                name + " limit reached: no more tools. Answer now with what you have found, "
                "and say plainly what remains uncertain.")})

        response = None
        try:
            response = client.chat.completions.create(
                model=llm.MODEL,
                messages=messages,
                tools=tools.TOOLS,
                tool_choice="none" if last else "auto",
                max_tokens=MAX_TOKENS,
                # OpenRouter-specific: among the companies serving this model,
                # prefer the fastest one. By default it prefers the cheapest,
                # which made one step take up to two minutes.
                extra_body={"provider": {"sort": "throughput"}},
            )
            # A service in trouble can also reply normally, with no answer in
            # the reply. This fails here too, rather than crash further down.
            message = response.choices[0].message
            usage = response.usage
            tokens_in, tokens_out = usage.prompt_tokens, usage.completion_tokens
        except Exception as error:
            # The user gets a clear message. The technical detail stays in the
            # saved run: it means nothing to them, and it is not theirs to see.
            answer = UNAVAILABLE
            error_detail = str(error)[:300]
            # A reply did arrive, so it may have been charged for.
            if response is not None:
                steps.append({"step": step, "seconds": round(time.time() - step_started, 1),
                              "tokens_in": None, "tokens_out": None, "cost": None,
                              "finish_reason": None, "tool_calls": []})
            break

        # The model's reply goes back into the conversation exactly as it
        # came, with every field it contains: "thinking" models need their
        # own reasoning sent back to them, or their tool calls break.
        messages.append(message.model_dump(exclude_none=True))

        record = {
            "step": step,
            "seconds": round(time.time() - step_started, 1),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost": llm.cost(response),
            "finish_reason": response.choices[0].finish_reason,
            "tool_calls": [],
        }
        steps.append(record)

        # No tool asked for: this is the answer.
        if not message.tool_calls:
            answer = message.content or ""
            if record["finish_reason"] == "length":
                answer = answer + "\n\n[Cut off: the reply reached the " + str(MAX_TOKENS) + "-token limit.]"
            if not answer.strip():
                answer = "Stopped: the model returned an empty reply. Please try again."
            break

        # Told to answer, it asked for tools anyway: they are not run.
        if last:
            break

        # Run each tool asked for, and send the result back with the call's id,
        # so the model can tell which result answers which call.
        for call in message.tool_calls:
            print("  step %d: %s %s" % (step, call.function.name, call.function.arguments[:150]))
            result = tools.run_tool(db, call.function.name, call.function.arguments)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
            record["tool_calls"].append({
                "name": call.function.name,
                "arguments": call.function.arguments,
                "result": result,
            })

    if answer is None:
        if limit_reached == "cost":
            answer = "Stopped: the cost limit of $%.2f was reached before an answer." % MAX_COST
        else:
            answer = "Stopped: no answer after " + str(MAX_STEPS) + " steps."

    run = {
        "question": question,
        "model": llm.MODEL,
        "answer": answer,
        "steps": len(steps),
        "seconds": round(time.time() - started, 1),
        "cost": total_cost(steps),
        "limit_reached": limit_reached,
        "error": error_detail,
        "step_details": steps,
        "messages": messages,
    }
    run["file"] = save(run)
    return run


def save(run):
    """Writes the whole run to the runs folder, as JSON, and returns the file name."""
    os.makedirs(RUNS, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
    path = RUNS + "/" + stamp + ".json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(run, f, ensure_ascii=False, indent=2, default=str)
    return path


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit('Usage: agent/agent.py "question"')

    run = ask(sys.argv[1])
    print()
    print(run["answer"])
    print()
    if run["file"]:
        print("%d steps, %.1f s, $%.4f  -  saved to %s" % (run["steps"], run["seconds"], run["cost"], run["file"]))
