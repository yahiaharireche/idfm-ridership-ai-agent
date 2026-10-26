# The connection to the language model.
#
# The model is reached through OpenRouter, which speaks the common
# "chat completions" format first made by OpenAI. The openai package is used
# only as a client for that format: it is not tied to OpenAI's own models.
# Pointing it at another service (Groq, or Ollama on this computer) means
# changing BASE_URL and the key, nothing else.

import os

import openai

BASE_URL = "https://openrouter.ai/api/v1"
KEY_FILE = ".env"

# The model the agent uses: DeepSeek V4.1 Flash, an open-weight model that is
# good at calling tools and costs about $0.003 per question. A fixed version,
# never a "latest" alias, so it cannot silently change between two evaluations.
MODEL = "deepseek/deepseek-v4.1-flash"


def read_key():
    """The OpenRouter key, from .env.

    .env is listed in .gitignore, so the key stays on this computer and never
    reaches GitHub. It is read here rather than written in the code for the
    same reason.
    """
    if not os.path.exists(KEY_FILE):
        raise SystemExit("No " + KEY_FILE + " file. Create it with one line: OPENROUTER_API_KEY=...")

    for line in open(KEY_FILE, encoding="utf-8"):
        line = line.strip()
        if line.startswith("OPENROUTER_API_KEY="):
            key = line[len("OPENROUTER_API_KEY="):].strip()
            if key:
                return key

    raise SystemExit("OPENROUTER_API_KEY is empty in " + KEY_FILE + ". Paste your key after the = sign.")


def connect():
    """A client for OpenRouter.

    timeout: a reply that takes longer than this is abandoned.
    max_retries: a request that fails for a temporary reason (a busy server,
    a dropped connection) is tried again this many times before giving up.
    """
    return openai.OpenAI(base_url=BASE_URL, api_key=read_key(), timeout=120, max_retries=2)


def cost(response):
    """What one request cost, in dollars, as reported by OpenRouter.

    It is not a standard field of the format, so the package does not know
    it by name; it sits with the other extra fields it received.
    """
    extra = response.usage.model_extra or {}
    return extra.get("cost")
