# Grading one answer: automatic checks first, then a second model as judge.
#
# The automatic checks are exact and free, but they can only see words and
# numbers: whether a number is there, whether a warning is named. Whether the
# explanation is honest, or a warning is explained correctly, needs reading;
# that is the judge's part. A judge can be wrong too, so its verdicts are
# compared with a human reading of a sample (see evaluation/README.md).

import json
import re

from agent import llm

# The judge is a different model family from the one answering: a model tends
# to grade its own way of writing too kindly. It is also the cheapest model
# tried, and only has to compare an answer with facts it is given.
JUDGE_MODEL = "glm"

REFUSAL_WORDS = [
    "cannot", "can't", "can not", "not possible", "impossible", "no data", "not in the data",
    "does not contain", "doesn't contain", "does not include", "doesn't include", "not available",
    "outside", "no record", "not recorded", "unable", "won't", "will not", "not covered",
    "does not cover", "doesn't cover", "only covers", "no information",
]

FRENCH_WORDS = ["les", "des", "est", "sur", "une", "pour", "été", "ont", "aucun", "dans", "avec", "réseau"]
ENGLISH_WORDS = ["the", "was", "were", "and", "of", "this", "which", "with", "there", "in"]


def plain(text):
    """The answer with the characters models vary on made plain: non-breaking
    hyphens and spaces, curly quotes, and thousands separators inside numbers
    (1,695,721,547 and 1 695 721 547 both become 1695721547)."""
    text = text.replace("‑", "-").replace("–", "-").replace("’", "'")
    text = text.replace(" ", " ").replace(" ", " ").replace(" ", " ")
    return re.sub(r"(?<=\d)[ ,.'](?=\d{3}(?!\d))", "", text)


def has_number(answer, value):
    """Whether the whole number value appears, not as part of a longer one."""
    return re.search(r"(?<!\d)" + str(value) + r"(?!\d)", plain(answer)) is not None


def has_rounded(answer, value):
    """Whether value appears rounded, written in billions or millions
    (1.49 billion, 2.16 bn, 926 million, 926M), within half a percent."""
    pattern = r"(\d+(?:[.,]\d+)?)\s*(billions?|bn|b\b|millions?|m\b)"
    for number, unit in re.findall(pattern, plain(answer), re.I):
        scale = 1e9 if unit.lower().startswith("b") else 1e6
        if abs(float(number.replace(",", ".")) * scale - value) <= 0.005 * value:
            return True
    return False


def has_percent(answer, value, tolerance):
    """Whether a percentage within tolerance of value appears (e.g. 35.97%,
    36 %, -98.5%, or 36 percent)."""
    for found in re.findall(r"(\d+(?:[.,]\d+)?)\s*(?:%|percent|pour cent)", plain(answer)):
        if abs(float(found.replace(",", ".")) - value) <= tolerance:
            return True
    return False


def has_any(answer, words):
    text = plain(answer).lower()
    for word in words:
        if word.lower() in text:
            return True
    return False


def language(answer):
    """'fr' or 'en', from how many common words of each language it uses."""
    words = re.findall(r"[a-zàâçéèêëîïôûùüÿœ']+", answer.lower())
    french = 0
    english = 0
    for word in words:
        if word in FRENCH_WORDS:
            french = french + 1
        if word in ENGLISH_WORDS:
            english = english + 1
    return "fr" if french > english else "en"


def checks(item, expected_numbers, answer):
    """The automatic checks that apply to this question: {name: passed}."""
    result = {}
    if item.get("numbers_sql"):
        missing = []
        for value in expected_numbers:
            if not has_number(answer, value) and not (item.get("rounded_ok") and has_rounded(answer, value)):
                missing.append(value)
        result["numbers"] = not missing
    if item.get("percent"):
        value, tolerance = item["percent"]
        result["percent"] = has_percent(answer, value, tolerance)
    if item.get("names"):
        result["names"] = all(name.lower() in plain(answer).lower() for name in item["names"])
    for i, words in enumerate(item.get("mentions", [])):
        result["mentions_" + str(i + 1)] = has_any(answer, words)
    if item.get("refuse"):
        result["refusal"] = has_any(answer, REFUSAL_WORDS)
    if item.get("language"):
        result["language"] = language(answer) == item["language"]
    return result


JUDGE_PROMPT = """You are grading an answer given by a data assistant. The assistant answers
questions about public transport taps in Île-de-France, 2015-2025, from a database.
A good answer is defensible: its numbers come from the data, it passes on the
data problems that affect it, it says plainly what the data cannot tell, and it
never presents a guess as a fact.

Question:
{question}

What a correct answer must contain or do (from the people who built the data):
{notes}
Correct numbers: {numbers}

The assistant's answer:
<answer>
{answer}
</answer>

Grade it on three points:
- correct: the facts and numbers agree with the reference above (rounded numbers are fine).
- warnings: it passes on the data problems the reference says matter, and does not
  present a wrong caveat as true.
- honest: it does not invent numbers, causes or certainty the data does not support.

Reply with JSON only, no other text:
{{"correct": true or false, "warnings": true or false, "honest": true or false, "reason": "one sentence"}}"""


def judge(client, item, expected_numbers, answer):
    """The judge's verdict as a dict, plus what it cost. None if the judge's
    reply could not be read: then only the automatic checks count."""
    numbers = ", ".join(format(n, ",") for n in expected_numbers) or "none given"
    prompt = JUDGE_PROMPT.format(question=item["question"], notes=item.get("notes", ""),
                                 numbers=numbers, answer=answer)
    try:
        response = client.chat.completions.create(
            model=llm.MODELS[JUDGE_MODEL],
            messages=[{"role": "user", "content": prompt}],
            # Little "thinking": the judge only compares an answer with facts it
            # is given. With its default thinking, it sometimes spent all its
            # tokens reasoning and never wrote the verdict (8 of 64 in the
            # baseline). This model does not allow thinking to be turned off.
            max_tokens=4000,
            extra_body={"provider": {"sort": "throughput"}, "reasoning": {"effort": "low"}},
        )
    except Exception as error:
        return None, 0.0, "judge unreachable: " + str(error)[:200]

    cost = llm.cost(response) or 0.0
    text = response.choices[0].message.content or ""
    found = re.search(r"\{.*\}", text, re.S)
    if not found:
        return None, cost, "judge reply had no JSON"
    try:
        verdict = json.loads(found.group(0))
    except json.JSONDecodeError:
        return None, cost, "judge reply was not valid JSON"
    for key in ["correct", "warnings", "honest"]:
        if not isinstance(verdict.get(key), bool):
            return None, cost, "judge reply missing '" + key + "'"
    return verdict, cost, verdict.get("reason", "")
