FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir flask gunicorn

COPY app.py /app/app.py
COPY templates /app/templates

CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:5000", "app:app"]
