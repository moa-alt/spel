# Klimathemmet AI Studio – produktionsavbild.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STUDIO_MILJO=produktion \
    STUDIO_DATA=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts/starta.sh ./starta.sh

# Appen körs som en vanlig användare, inte som root.
RUN useradd --create-home --uid 10001 studio && mkdir -p /data && chmod +x starta.sh

EXPOSE 8080
CMD ["./starta.sh"]
