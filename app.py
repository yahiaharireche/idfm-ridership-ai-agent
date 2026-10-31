# The web page of the agent: type a question, get the answer and the evidence
# behind it (the SQL, the warnings and every step).
#
# Run from the project root, then open http://127.0.0.1:7860 :
#   AGENT_DATABASE=data/model/ridership_serving.duckdb .venv/bin/python app.py
#
# The settings it reads are listed in agent/settings.py.

import json
import traceback

import gradio as gr

from agent import agent, limits, llm, settings, tools

EXAMPLES = [
    "How many metro and train taps were there in 2019?",
    "How did ridership change between 2019 and 2020?",
    "Which five stations were the busiest in 2023?",
    "What problems in the data should I know about for 2016?",
]

INTRODUCTION = """
# Île-de-France Ridership Agent
Ask about public transport use in the Paris region, from 2015 to 2025. Every answer
comes with the queries and data warnings behind it, so you can check where it came from.

**It can't answer:** forecasts, timetables, or ticket prices. The data counts taps
at ticket gates and on vehicles, not people, so one person can count several times.
"""

UNEXPECTED = "Something went wrong on our side. Please try again in a moment."

# The longest tool result shown in the steps section.
MAX_SHOWN = 1500

# The model's client, made once when the app starts. Questions share it, but
# each opens its own database connection, as they can run at the same time.
CLIENT = None


def client_ip(request):
    """Who is asking, to count their questions.

    Behind a proxy (the program that receives visitors first and passes them
    on), every request seems to come from the proxy, and the visitor's address
    is the last one in the X-Forwarded-For header. Without a proxy that header
    is written by the visitor, who could change it at each question, so it is
    only used when we know our own proxy added it.
    """
    if settings.BEHIND_PROXY:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    if request.client:
        return request.client.host
    return "unknown"


def tool_calls(run):
    """Every tool call of a run, in order."""
    calls = []
    for record in run["step_details"]:
        calls = calls + record["tool_calls"]
    return calls


def query_text(call):
    """The SQL of a run_query call. The arguments are what the model wrote, so
    they may not be valid."""
    try:
        return json.loads(call["arguments"])["sql"]
    except (ValueError, KeyError, TypeError):
        return call["arguments"]


def sql_section(run):
    """Every query the agent ran, and whether it failed."""
    parts = []
    for call in tool_calls(run):
        if call["name"] == "run_query":
            heading = "-- Query " + str(len(parts) + 1)
            if call["result"].startswith("Error:"):
                heading = heading + " (failed, so not used)"
            parts.append(heading + "\n" + str(query_text(call)))
    return "\n\n".join(parts) if parts else "-- No query was run."


def warnings_section(run):
    """What the agent found out about known problems in the data."""
    parts = []
    for call in tool_calls(run):
        if call["name"] == "warnings_for_period":
            parts.append(call["result"])
        elif call["name"] == "run_query" and "warnings_between" in str(query_text(call)):
            parts.append(call["result"])
    return "\n\n".join(parts) if parts else "The agent did not check for known problems on this question."


def steps_section(run):
    """What the agent did at each step."""
    lines = []
    for record in run["step_details"]:
        cost = "cost not reported" if record["cost"] is None else "$%.4f" % record["cost"]
        lines.append("Step %d (%s)" % (record["step"], cost))
        if not record["tool_calls"]:
            lines.append("  Wrote the answer.")
        for call in record["tool_calls"]:
            result = call["result"]
            if len(result) > MAX_SHOWN:
                result = result[:MAX_SHOWN] + "\n... (cut here, " + str(len(result)) + " characters in all)"
            lines.append("  " + call["name"] + " " + call["arguments"])
            lines.append("  Result:\n" + result)
    return "\n".join(lines)


def summary(run):
    text = "%d steps · %.1f s · $%.4f" % (run["steps"], run["seconds"], run["cost"])
    if run["limit_reached"]:
        text = text + " · stopped early by the " + run["limit_reached"] + " limit: the answer may be incomplete"
    return text


def no_run(message):
    """What the page shows when no question was run."""
    return message, "", "", "", ""


def respond(question, ip, client=None, db=None):
    """One question from the page, from the limits to the sections to show.

    Returns the answer, the summary, the SQL, the warnings and the steps.
    """
    problem = agent.check_question(question)
    if problem:
        return no_run(problem)

    # If the limits cannot be checked, nothing runs: no check, no spending.
    try:
        reservation, refusal = limits.reserve(ip)
    except Exception:
        traceback.print_exc()
        return no_run(UNEXPECTED)
    if refusal:
        return no_run(refusal)

    connection = None
    try:
        connection = db or tools.connect()
        run = agent.ask(question, client or CLIENT, connection)
    except Exception:
        # What this question spent is unknown, so its reservation is kept.
        traceback.print_exc()
        return no_run(UNEXPECTED)
    finally:
        if connection is not None and db is None:
            connection.close()

    try:
        limits.settle(reservation, run["cost"])
    except Exception:
        traceback.print_exc()    # the reservation stays counted: the safe side

    return run["answer"], summary(run), sql_section(run), warnings_section(run), steps_section(run)


def on_question(question, request: gr.Request):
    # Gradio fills in request by itself, because of its declared type.
    return respond(question, client_ip(request))


def build_page():
    # analytics_enabled=False: Gradio sends no usage data to its makers.
    with gr.Blocks(title="Ridership Agent", analytics_enabled=False) as page:
        gr.Markdown(INTRODUCTION)
        question = gr.Textbox(label="Your question", lines=2, submit_btn=False)
        button = gr.Button("Ask", variant="primary")
        gr.Examples(EXAMPLES, inputs=question, label="Try one of these")

        answer = gr.Markdown(label="Answer", container=True, min_height=80, value="The answer will appear here.")
        details = gr.Markdown()

        with gr.Accordion("How it was computed (SQL)", open=False):
            sql = gr.Code(language="sql", show_label=False, interactive=False)
        with gr.Accordion("Warnings about the data", open=False):
            warnings = gr.Textbox(show_label=False, lines=6, interactive=False)
        with gr.Accordion("Every step", open=False):
            steps = gr.Textbox(show_label=False, lines=12, interactive=False)

        outputs = [answer, details, sql, warnings, steps]
        # concurrency_limit: how many questions run at the same time. The
        # others wait their turn, and the page shows they are waiting.
        button.click(on_question, question, outputs, concurrency_limit=settings.CONCURRENT_QUESTIONS, api_name="ask")
    return page


if __name__ == "__main__":
    if limits.RESERVED_PER_QUESTION > limits.DAILY_SPENDING_LIMIT:
        raise SystemExit("The daily spending limit is below what one question reserves: nothing could ever run.")

    # A missing key or database stops here, rather than at the first visitor.
    CLIENT = llm.connect()
    tools.connect().close()

    # max_size: at most this many questions wait. Others are told to try later.
    build_page().queue(max_size=20).launch(server_name=settings.HOST, server_port=settings.PORT, footer_links=[])
