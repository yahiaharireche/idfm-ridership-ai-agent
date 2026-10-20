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
#   .venv/bin/python agent/agent.py --model qwen "..."

import datetime
import json
import os
import sys
import time

sys.path.insert(0, ".")   # so "agent" can be imported when run as a script

from agent import llm, prompt, tools

# A model that has not answered after this many steps is stopped. A normal
# question needs three to six: look at a table, query, check warnings, answer.
MAX_STEPS = 10

# The longest reply allowed per step, reasoning included. It also caps what a
# single step can cost.
MAX_TOKENS = 4000

RUNS = "runs"


def ask(question, model="deepseek", client=None, db=None):
    """Runs the agent on one question and returns what happened, as a dict."""
    client = client or llm.connect()
    db = db or tools.connect()

    messages = [
        {"role": "system", "content": prompt.system_prompt(db)},
        {"role": "user", "content": question},
    ]
    steps = []
    answer = None
    started = time.time()

    for step in range(1, MAX_STEPS + 1):
        step_started = time.time()
        try:
            response = client.chat.completions.create(
                model=llm.MODELS[model],
                messages=messages,
                tools=tools.TOOLS,
                max_tokens=MAX_TOKENS,
                # OpenRouter-specific: among the companies serving this model,
                # prefer the fastest one. By default it prefers the cheapest,
                # which made one step take up to two minutes.
                extra_body={"provider": {"sort": "throughput"}},
            )
        except Exception as error:
            answer = "Stopped: the model could not be reached (" + str(error)[:300] + ")."
            break

        message = response.choices[0].message

        # The model's reply goes back into the conversation exactly as it
        # came, with every field it contains: "thinking" models need their
        # own reasoning sent back to them, or their tool calls break.
        messages.append(message.model_dump(exclude_none=True))

        record = {
            "step": step,
            "seconds": round(time.time() - step_started, 1),
            "tokens_in": response.usage.prompt_tokens,
            "tokens_out": response.usage.completion_tokens,
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
        answer = "Stopped: no answer after " + str(MAX_STEPS) + " steps."

    total_cost = 0.0
    for record in steps:
        total_cost = total_cost + (record["cost"] or 0)

    run = {
        "question": question,
        "model": llm.MODELS[model],
        "answer": answer,
        "steps": len(steps),
        "seconds": round(time.time() - started, 1),
        "cost": total_cost,
        "step_details": steps,
        "messages": messages,
    }
    run["file"] = save(run, model)
    return run


def save(run, model):
    """Writes the whole run to runs/, as JSON, and returns the file name."""
    os.makedirs(RUNS, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
    path = RUNS + "/" + stamp + "_" + model + ".json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(run, f, ensure_ascii=False, indent=2, default=str)
    return path


if __name__ == "__main__":
    arguments = sys.argv[1:]
    model = "deepseek"
    if len(arguments) >= 2 and arguments[0] == "--model":
        model = arguments[1]
        arguments = arguments[2:]

    if len(arguments) != 1 or model not in llm.MODELS:
        raise SystemExit('Usage: agent/agent.py [--model ' + "|".join(llm.MODELS) + '] "question"')

    run = ask(arguments[0], model)
    print()
    print(run["answer"])
    print()
    print("%d steps, %.1f s, $%.4f  -  saved to %s" % (run["steps"], run["seconds"], run["cost"], run["file"]))
