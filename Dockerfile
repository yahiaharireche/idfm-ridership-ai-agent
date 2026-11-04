# The recipe for the app's image: the code and its libraries, nothing else.
# The database and the API key are given to the container when it starts (see
# docker-compose.yml), so they are never inside the image.

FROM python:3.14-slim

WORKDIR /app

# The libraries first: Docker reuses this step until requirements.txt changes,
# so editing the code does not install everything again.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ agent/
COPY app.py .

# The app does not run as the all-powerful root user, and can only write in
# /state: the spending counters and the saved runs.
RUN useradd --create-home app && mkdir /state && chown app /state
USER app

# PYTHONUNBUFFERED: messages reach `docker compose logs` at once, instead of
# waiting in memory and being lost if the app crashes.
ENV PYTHONUNBUFFERED=1 \
    AGENT_HOST=0.0.0.0 \
    AGENT_DATABASE=/data/ridership_serving.duckdb \
    AGENT_LIMITS_DATABASE=/state/limits.sqlite \
    AGENT_RUNS_FOLDER=/state/runs

EXPOSE 7860

CMD ["python", "app.py"]
