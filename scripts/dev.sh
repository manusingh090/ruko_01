#!/usr/bin/env sh
# Start the whole RUKO demo stack (macOS / Linux / Git Bash) and connect the phone to it.
#
#   sh scripts/dev.sh              start everything
#   REGENERATE=1 sh scripts/dev.sh rebuild data first
#   BUILD_CONSOLE=1 sh scripts/dev.sh  rebuild the analyst console
#   sh scripts/dev.sh stop         stop the services
#
# Same steps as scripts/dev.ps1. API_PORT and INTEL_PORT can be overridden.
set -eu
cd "$(dirname "$0")/.."
API_PORT="${API_PORT:-8000}"
INTEL_PORT="${INTEL_PORT:-8001}"
PAYERS="${PAYERS:-40}"
mkdir -p logs
PIDS=logs/dev.pids

stop() { [ -f "$PIDS" ] && { xargs kill 2>/dev/null < "$PIDS" || true; rm -f "$PIDS"; }; return 0; }
[ "${1:-}" = "stop" ] && { stop; exit 0; }
stop

if [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe; SEP=";"
elif [ -x .venv/bin/python ]; then PY=.venv/bin/python; SEP=":"
else
  python3 -m venv .venv 2>/dev/null || python -m venv .venv
  if [ -x .venv/bin/python ]; then PY=.venv/bin/python; SEP=":"; else PY=.venv/Scripts/python.exe; SEP=";"; fi
  "$PY" -m pip install -q -r backend/requirements.txt
fi
# The API scores with the trained model (ml/model/predict.py), which needs LightGBM; without it the
# API falls back to the rules. ml/requirements.txt includes backend/requirements.txt.
"$PY" -c "import lightgbm" 2>/dev/null || "$PY" -m pip install -q -r ml/requirements.txt
export PYTHONPATH="backend${SEP}ml${SEP}data" PYTHONIOENCODING=utf-8

if [ "${REGENERATE:-0}" = 1 ] || [ ! -f data/ledger.parquet ]; then
  "$PY" -m sim.generate --users 2000 --days 30 --seed 7 --out data/
fi

# Analyst console (console/dist is git ignored; the API serves it at /console/)
if [ "${BUILD_CONSOLE:-0}" = 1 ] || [ ! -f console/dist/index.html ]; then
  if command -v npm >/dev/null 2>&1; then
    echo "building the analyst console"
    (cd console && { [ -d node_modules ] || npm install --no-audit --no-fund >/dev/null; } && npm run build >/dev/null)       || echo "console build failed; the API still runs without it"
  else
    echo "npm not found; skipping the analyst console (install Node.js to get it)"
  fi
fi

wait_health() {
  i=0
  until curl -fs "$1" >/dev/null 2>&1; do
    i=$((i + 1)); [ "$i" -gt 60 ] && { echo "no answer from $1 (see logs/)"; exit 1; }
    sleep 0.5
  done
}

"$PY" -m intel_mock.app --data data --port "$INTEL_PORT" > logs/intel.log 2>&1 &
echo $! > "$PIDS"
wait_health "http://127.0.0.1:$INTEL_PORT/health"
echo "mock intel up on $INTEL_PORT"

"$PY" -m api.main --data data --port "$API_PORT" --intel "http://127.0.0.1:$INTEL_PORT" --demo-clock > logs/api.log 2>&1 &
echo $! >> "$PIDS"
wait_health "http://127.0.0.1:$API_PORT/health"
echo "risk API up on $API_PORT"

[ "${NO_PUMP:-0}" = 1 ] || "$PY" -m sim.mule_pump --url "http://127.0.0.1:$INTEL_PORT" --payers "$PAYERS"

if command -v adb >/dev/null 2>&1 && adb devices | grep -q "	device$"; then
  adb reverse tcp:8000 "tcp:$API_PORT" >/dev/null
  echo "phone: adb reverse tcp:8000 -> laptop tcp:$API_PORT"
  adb reverse tcp:8001 tcp:8001 >/dev/null   # mock NPCI's UPI number mapper
  "$PY" -m sim.contacts --data data --adb >/dev/null && echo "phone: Meena's contacts are in the phone's Contacts"
else
  echo "no phone found; run later: adb reverse tcp:8000 tcp:$API_PORT"
fi
[ -f console/dist/index.html ] && echo "Analyst console: http://127.0.0.1:$API_PORT/console/"
echo "Ready. Logs in logs/. Stop: sh scripts/dev.sh stop"
