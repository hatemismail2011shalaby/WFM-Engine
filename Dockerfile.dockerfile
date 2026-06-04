# Dockerfile – LinePilot WFM Engine API
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV LINEPILOT_API_KEY=pilot-demo-key-2025
ENV LINEPILOT_API_URL=http://api:8000
ENV LINEPILOT_SLACK_WEBHOOK=""

EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]