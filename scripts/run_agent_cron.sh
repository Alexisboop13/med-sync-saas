#!/usr/bin/env bash
# Dispara el agente de recordatorios desde un cron externo (Render/Railway).
# Reemplaza al APScheduler in-proceso que vivía en app/agent/background.py.
#
# No requiere `pip install -e .`: usa `python -m` directo contra el código
# ya instalado por `pip install -r requirements.txt`.
set -euo pipefail
cd "$(dirname "$0")/.."
python -m app.cli.agent run --yes
