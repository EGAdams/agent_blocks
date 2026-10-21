#!/usr/bin/env bash
# Serve spa_documentation locally. Uses server.py rather than bare
# `python3 -m http.server` because the Update Documentation tab's button
# needs a live /api/git-status route (see server.py's docstring).
cd "$(dirname "$0")"
PORT="${1:-8931}"
npm run build
python3 server.py "$PORT"
