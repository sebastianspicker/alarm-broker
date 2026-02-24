FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY services/alarm_broker /app/services/alarm_broker

RUN python -m pip install --upgrade pip \
  && python -m pip install -e /app/services/alarm_broker

WORKDIR /app/services/alarm_broker

EXPOSE 8080

CMD ["uvicorn", "alarm_broker.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
