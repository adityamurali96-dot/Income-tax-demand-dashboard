FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway injects PORT env var
ENV PORT=8000

EXPOSE ${PORT}

CMD python -m app.seed && uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
