# Sends one short message, with no tools, to each of the five models, to check
# that the key works, that every model name is right, and how fast each one
# answers. Costs a fraction of a cent in total.
#
# Run from the project root:  .venv/bin/python agent/check_models.py

import sys
import time

sys.path.insert(0, ".")   # so "agent" can be imported when run as a script

from agent import llm

QUESTION = "In one short sentence: what is a public transport ridership statistic?"

client = llm.connect()

for name, model in llm.MODELS.items():
    start = time.time()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": QUESTION}],
            max_tokens=2000,
        )
    except Exception as error:
        print("%-9s FAILED  %s" % (name, str(error)[:200]))
        continue

    seconds = time.time() - start
    answer = (response.choices[0].message.content or "").strip().replace("\n", " ")
    usage = response.usage
    print("%-9s %5.1f s  %5d tokens in, %5d out  $%s" % (
        name, seconds, usage.prompt_tokens, usage.completion_tokens, llm.cost(response)))
    print("          " + answer[:200])
