# Base image ships the matching Chromium + all system libs Playwright needs.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Northflank wants a listening port; long-poll itself needs no ingress.
EXPOSE 8080

CMD ["python", "bot.py"]
