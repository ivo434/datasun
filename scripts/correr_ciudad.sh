#!/bin/bash
# Precómputo CABA completa: secuencial por comuna, retomable (checkpoint en
# cada sqlite). 14 y 13 primero para desarrollar el frontend contra bases
# nuevas mientras corre el resto. La 6 ya existe.
cd "$(dirname "$0")/.."
LOG=output/ciudad_run.log
for n in 14 13 1 2 3 4 5 7 8 9 10 11 12 15; do
  echo "=== comuna $n inicio $(date '+%H:%M:%S') ==="
  .venv/bin/python fase0.py comuna "$n" >> "$LOG" 2>&1
  rc=$?
  fin=$(grep "Comuna $n precomputada" "$LOG" | tail -1)
  echo "=== comuna $n fin rc=$rc $(date '+%H:%M:%S') :: ${fin:-SIN RESUMEN, ver log} ==="
done
echo "=== CIUDAD COMPLETA $(date '+%H:%M:%S') ==="
