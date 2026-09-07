FROM python:3.12-slim

ARG BUILD_ID="unknown"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENV ONTRACK_BUILD_ID=${BUILD_ID}
WORKDIR /app
RUN addgroup --system ontrack && adduser --system --ingroup ontrack ontrack
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
RUN chown -R ontrack:ontrack /app
USER ontrack
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && gunicorn app.main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000 --workers 2 --access-logfile -"]
