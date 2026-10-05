FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .

ENV PORT=9000
EXPOSE 9000
CMD ["sh", "-c", "gunicorn -b 0.0.0.0:$PORT -w 2 -t 120 server:app"]
