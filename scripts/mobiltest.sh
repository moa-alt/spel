#!/bin/sh
# Startar studion så att den nås från mobilen på samma wifi-nätverk.
# Kräver lösenord, eftersom alla på nätverket annars kan öppna studion.
set -e
cd "$(dirname "$0")/.."

if [ -z "$STUDIO_USER" ] || [ -z "$STUDIO_PASSWORD" ]; then
  echo "Sätt ett användarnamn och lösenord först, till exempel:"
  echo "  STUDIO_USER=moa STUDIO_PASSWORD='ett-langt-losenord' ./scripts/mobiltest.sh"
  exit 1
fi

IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')
PORT=${PORT:-8000}
echo "Öppna på mobilen (samma wifi):  http://$IP:$PORT"
echo "Stoppa med Ctrl+C."
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
