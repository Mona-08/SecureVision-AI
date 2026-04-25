FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Hugging Face uses port 7860 by default
CMD ["gunicorn", "-b", "0.0.0.0:7860", "app:app"]
