# Automatic tests for the agent loop, agent/agent.py.
#
# No real model is called. A fake client plays the model's part with replies
# written in advance, in the exact format the real service sends. So these
# tests are free, work offline, and give the same result every time: they
# check our loop, not a model's mood.
#
# Run from the project root:  .venv/bin/python -m pytest tests

import copy
import json
import os

import pytest
from openai.types.chat import ChatCompletion

from agent import agent, tools


def reply(content=None, calls=None, finish=None, extra=None):
    """One reply from the fake model, in the service's format.

    calls: a list of (call id, tool name, arguments as JSON text).
    extra: more fields for the message, e.g. a model's reasoning.
    """
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = []
        for call_id, name, arguments in calls:
            message["tool_calls"].append({
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            })
    message.update(extra or {})
    return {
        "id": "fake", "object": "chat.completion", "created": 0, "model": "fake",
        "choices": [{"index": 0, "message": message,
                     "finish_reason": finish or ("tool_calls" if calls else "stop")}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost": 0.001},
    }


class FakeClient:
    """Stands in for the model service. Answers each request with the next
    reply from the list (the last one again once the list runs out), and keeps
    a copy of every conversation it was sent."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.tool_choices = []
        self.chat = self
        self.completions = self

    def create(self, **request):
        self.sent.append(copy.deepcopy(request["messages"]))
        self.tool_choices.append(request.get("tool_choice"))
        if len(self.replies) > 1:
            return ChatCompletion.model_validate(self.replies.pop(0))
        return ChatCompletion.model_validate(self.replies[0])


class BrokenClient(FakeClient):
    """A service that cannot be reached."""

    def create(self, **request):
        raise ConnectionError("network down")


@pytest.fixture(scope="module")
def db():
    connection = tools.connect()
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def runs_in_temp_folder(tmp_path, monkeypatch):
    # Trace files go to a temporary folder, not the project's runs/.
    monkeypatch.setattr(agent, "RUNS", str(tmp_path))


def tool_messages(conversation):
    found = []
    for message in conversation:
        if message["role"] == "tool":
            found.append(message)
    return found


# --- the basic loop ---

def test_answer_without_tools(db):
    client = FakeClient([reply("Hello.")])
    run = agent.ask("Hi", "deepseek", client, db)
    assert run["answer"] == "Hello."
    assert run["steps"] == 1
    assert run["cost"] == pytest.approx(0.001)


def test_instructions_come_first(db):
    client = FakeClient([reply("ok")])
    agent.ask("Hi", "deepseek", client, db)
    first = client.sent[0]
    assert first[0]["role"] == "system"
    assert "fact_rail_daily" in first[0]["content"]    # the table list is included
    assert first[1] == {"role": "user", "content": "Hi"}


def test_tool_result_goes_back_with_its_id(db):
    client = FakeClient([
        reply(calls=[("call_1", "run_query", '{"sql": "SELECT 41 + 1 AS n"}')]),
        reply("The answer is 42."),
    ])
    run = agent.ask("question", "deepseek", client, db)
    results = tool_messages(client.sent[1])
    assert len(results) == 1
    assert results[0]["tool_call_id"] == "call_1"
    assert "42" in results[0]["content"]
    assert run["answer"] == "The answer is 42."
    assert run["steps"] == 2


def test_several_tools_in_one_reply(db):
    client = FakeClient([
        reply(calls=[("a", "run_query", '{"sql": "SELECT 1 AS x"}'),
                     ("b", "warnings_for_period", '{"start": "2016-01-01", "end": "2016-01-31"}')]),
        reply("done"),
    ])
    agent.ask("question", "deepseek", client, db)
    results = tool_messages(client.sent[1])
    assert [r["tool_call_id"] for r in results] == ["a", "b"]
    assert "known problems" in results[1]["content"]


def test_reasoning_is_sent_back(db):
    # "Thinking" models need their own reasoning returned to them unchanged.
    client = FakeClient([
        reply(calls=[("c", "run_query", '{"sql": "SELECT 1"}')], extra={"reasoning": "I should query."}),
        reply("done"),
    ])
    agent.ask("question", "deepseek", client, db)
    assistant = client.sent[1][2]
    assert assistant["role"] == "assistant"
    assert assistant["reasoning"] == "I should query."


# --- when things go wrong ---

def test_invented_tool_gets_an_error_and_the_loop_goes_on(db):
    client = FakeClient([
        reply(calls=[("c", "apply_patch", '{"patch": "x"}')]),
        reply("Sorry, done."),
    ])
    run = agent.ask("question", "deepseek", client, db)
    assert tool_messages(client.sent[1])[0]["content"].startswith("Error: there is no tool")
    assert run["answer"] == "Sorry, done."


def test_bad_inputs_get_an_error(db):
    client = FakeClient([
        reply(calls=[("c", "run_query", '{"sql": "SELECT 1"')]),   # broken JSON
        reply("done"),
    ])
    agent.ask("question", "deepseek", client, db)
    assert "not valid JSON" in tool_messages(client.sent[1])[0]["content"]


def test_a_model_that_never_answers_is_stopped(db):
    client = FakeClient([reply(calls=[("c", "run_query", '{"sql": "SELECT 1"}')])])
    run = agent.ask("question", "deepseek", client, db)
    assert run["steps"] == agent.MAX_STEPS
    assert run["answer"] == "Stopped: no answer after " + str(agent.MAX_STEPS) + " steps."


def test_last_step_must_be_an_answer(db):
    # On the last step tools are switched off and the model is told to answer.
    client = FakeClient([reply(calls=[("c", "run_query", '{"sql": "SELECT 1"}')])])
    agent.ask("question", "deepseek", client, db)
    assert client.tool_choices[:-1] == ["auto"] * (agent.MAX_STEPS - 1)
    assert client.tool_choices[-1] == "none"
    assert client.sent[-1][-1]["content"].startswith("Step limit reached")


def test_a_model_that_answers_at_the_limit_is_not_stopped(db):
    replies = [reply(calls=[("c", "run_query", '{"sql": "SELECT 1"}')])] * (agent.MAX_STEPS - 1)
    client = FakeClient(replies + [reply("Here is what I found.")])
    run = agent.ask("question", "deepseek", client, db)
    assert run["answer"] == "Here is what I found."
    assert run["steps"] == agent.MAX_STEPS


def test_unreachable_service(db):
    run = agent.ask("question", "deepseek", BrokenClient([]), db)
    assert run["answer"].startswith("Stopped: the model could not be reached")
    assert run["steps"] == 0


def test_answer_cut_off_is_flagged(db):
    client = FakeClient([reply("Half an ans", finish="length")])
    run = agent.ask("question", "deepseek", client, db)
    assert "[Cut off" in run["answer"]


# --- the trace ---

def test_every_run_is_saved(db):
    client = FakeClient([
        reply(calls=[("c", "run_query", '{"sql": "SELECT 1 AS x"}')]),
        reply("done"),
    ])
    run = agent.ask("What is x?", "deepseek", client, db)
    assert os.path.exists(run["file"])
    saved = json.load(open(run["file"], encoding="utf-8"))
    assert saved["question"] == "What is x?"
    assert saved["answer"] == "done"
    assert saved["step_details"][0]["tool_calls"][0]["name"] == "run_query"
    assert len(saved["messages"]) == 5     # instructions, question, call, result, answer
