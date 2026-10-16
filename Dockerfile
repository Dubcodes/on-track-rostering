FROM python:3.12-slim

ARG BUILD_ID=""
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN addgroup --system ontrack && adduser --system --ingroup ontrack ontrack
RUN apt-get update && apt-get install -y --no-install-recommends gosu && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY scripts/container-entrypoint.sh /usr/local/bin/ontrack-entrypoint
RUN if [ -n "$BUILD_ID" ]; then printf '%s' "$BUILD_ID" > /app/.ontrack-build-id; else find app migrations -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1 > /app/.ontrack-build-id; fi
RUN chown -R ontrack:ontrack /app && chmod 0755 /usr/local/bin/ontrack-entrypoint
ENTRYPOINT ["/usr/local/bin/ontrack-entrypoint"]
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && gunicorn app.main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000 --workers 2 --access-logfile -"]
