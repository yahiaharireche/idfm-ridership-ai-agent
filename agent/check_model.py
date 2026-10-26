# Sends one short message, with no tools, to the model, to check that the key
# works and the model answers, and how fast. Costs a fraction of a cent.
#
# Run from the project root:  .venv/bin/python agent/check_model.py

import sys
import time

sys.path.insert(0, ".")   # so "agent" can be imported when run as a script

from agent import llm

QUESTION = "In one short sentence: what is a public transport ridership statistic?"

client = llm.connect()
start = time.time()
response = client.chat.completions.create(
    model=llm.MODEL,
    messages=[{"role": "user", "content": QUESTION}],
    max_tokens=2000,
)

usage = response.usage
print("%s  %.1f s  %d tokens in, %d out  $%s" % (
    llm.MODEL, time.time() - start, usage.prompt_tokens, usage.completion_tokens, llm.cost(response)))
print(response.choices[0].message.content)
