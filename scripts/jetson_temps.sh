#!/usr/bin/env bash
# Log Jetson thermal zone temps (millidegrees C) every 10s to a local file.
# Usage: scripts/jetson_temps.sh [outfile]
set -euo pipefail

HOST="jetson@192.168.1.35"
OUT="${1:-jetson_temps_$(date +%Y%m%d_%H%M%S).txt}"
INTERVAL=10

echo "Logging to $OUT (Ctrl-C to stop)"

# One SSH session; the loop runs on the Jetson and streams lines back.
ssh "$HOST" "
  echo \"# timestamp \$(cat /sys/devices/virtual/thermal/thermal_zone*/type | tr '\n' ' ')\"
  while true; do
    echo \"\$(date '+%Y-%m-%d %H:%M:%S') \$(cat /sys/devices/virtual/thermal/thermal_zone*/temp | tr '\n' ' ')\"
    sleep $INTERVAL
  done
" | tee -a "$OUT"
