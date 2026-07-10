#!/bin/bash
# Exporta cada comuna al frontend apenas su precómputo aparece completo en el
# log. Corre en paralelo a correr_ciudad.sh; termina cuando exportó las 15.
cd "$(dirname "$0")/.."
LOG=output/ciudad_run.log
for n in 14 13 1 2 3 4 5 7 8 9 10 11 12 15 6; do
  if [ -d "web/public/data/c$n/p" ] && [ -f "web/public/data/c$n/tejido-q0.geojson" ]; then
    echo "c$n ya exportada"
    continue
  fi
  until [ "$n" = 6 ] || grep -q "Comuna $n precomputada" "$LOG" 2>/dev/null; do
    sleep 60
  done
  echo "exportando c$n…"
  .venv/bin/python scripts/export_frontend.py comuna "$n" >> output/export_run.log 2>&1 \
    && echo "c$n exportada" || echo "c$n FALLO (ver output/export_run.log)"
done
echo "EXPORTS COMPLETOS"
