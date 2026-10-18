#!/bin/sh
# Startskript i containern. En monterad volym ägs av root, så rättigheterna sätts först;
# sedan körs servern som användaren studio. Exakt en process, eftersom schemaläggaren
# och SQLite-databasen inte får köras i flera exemplar samtidigt.
set -e
if [ "$(id -u)" = "0" ]; then
  mkdir -p "${STUDIO_DATA:-/data}"
  chown -R studio:studio "${STUDIO_DATA:-/data}"
  exec setpriv --reuid=studio --regid=studio --init-groups "$0" "$@"
fi
# En säkerhetskopia som lagts i /data/aterstall.tgz återställs innan servern öppnar databasen.
python -m app.sakerhetskopia aterstall-vid-start
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}" \
  --proxy-headers --forwarded-allow-ips '*' --workers 1 --no-server-header
