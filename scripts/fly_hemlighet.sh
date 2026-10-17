#!/bin/sh
# Sparar en hemlighet i Fly.io utan att den syns på skärmen, i terminalhistoriken eller i processlistan.
# Användning: ./scripts/fly_hemlighet.sh ANTHROPIC_API_KEY
set -e
NAMN="$1"
case "$NAMN" in
  STUDIO_PASSWORD|ANTHROPIC_API_KEY|WP_APP_PASSWORD|OPENAI_API_KEY) ;;
  *) echo "Användning: $0 STUDIO_PASSWORD|ANTHROPIC_API_KEY|WP_APP_PASSWORD|OPENAI_API_KEY"; exit 1 ;;
esac
printf '%s (syns inte när du skriver): ' "$NAMN"
stty -echo; trap 'stty echo' EXIT INT TERM
read -r VARDE
stty echo; echo
[ -n "$VARDE" ] || { echo "Inget sparades."; exit 1; }
printf '%s=%s\n' "$NAMN" "$VARDE" | fly secrets import
echo "$NAMN är sparad i Fly.io."
