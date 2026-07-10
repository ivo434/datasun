"""
Cierre del precómputo CABA: tabla resumen por comuna + histograma de la ciudad
a 6 m — el primer mapa estadístico de sol de Buenos Aires.

  .venv/bin/python scripts/resumen_ciudad.py            usa las 15 comunas
  .venv/bin/python scripts/resumen_ciudad.py --parcial  las que existan
"""

import os
import re
import sqlite3
import sys
from collections import Counter

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

COMUNAS = list(range(1, 16))
FONDO = "#0a0e14"
TINTA = "#eef1f6"
TINTA_2 = "#aab3c2"
TINTA_3 = "#6d7889"
# par validado contra la superficie oscura (validate_palette: todos los checks)
AMBAR = "#c98500"
AZUL = "#5a8fd6"


def tiempos_del_log():
    """min por comuna, parseados de los logs de corrida."""
    t = {}
    for ruta in ["output/ciudad_run.log", "output/comuna6_run2.log"]:
        if not os.path.exists(ruta):
            continue
        for m in re.finditer(r"Comuna (\d+) precomputada en ([\d.]+) min", open(ruta).read()):
            t[int(m.group(1))] = float(m.group(2))
    return t


def main(parcial=False):
    tiempos = tiempos_del_log()
    filas, horas_ciudad = [], []
    for n in COMUNAS:
        db = f"output/comuna{n}.sqlite"
        if not os.path.exists(db):
            if parcial:
                continue
            sys.exit(f"falta {db} (usar --parcial para un corte)")
        con = sqlite3.connect(db)
        p = pd.read_sql("SELECT flags, estado FROM parcelas", con)
        flags = Counter()
        for fs in p["flags"]:
            for f in (fs or "").split(","):
                if f and f != "contrafrente_v3_no_validado":
                    flags[f] += 1
        falladas = int((p.estado == "fallada").sum())
        limpias = int(sum(1 for fs in p["flags"]
                          if set(filter(None, (fs or "").split(","))) <=
                          {"contrafrente_v3_no_validado"}))
        h = pd.read_sql("SELECT smp, cara, AVG(horas) horas FROM horas "
                        "WHERE altura_m = 6 GROUP BY smp, cara", con)
        h["comuna"] = n
        horas_ciudad.append(h)
        dominantes = ", ".join(f"{k} {100 * v / len(p):.0f}%" for k, v in flags.most_common(2))
        filas.append({"comuna": n, "parcelas": len(p), "limpias": limpias,
                      "%limpias": round(100 * limpias / len(p)),
                      "flags dominantes": dominantes, "falladas": falladas,
                      "min": tiempos.get(n, float("nan"))})
        con.close()

    tabla = pd.DataFrame(filas)
    total = {"comuna": "CABA", "parcelas": tabla.parcelas.sum(),
             "limpias": tabla.limpias.sum(),
             "%limpias": round(100 * tabla.limpias.sum() / tabla.parcelas.sum()),
             "flags dominantes": "", "falladas": tabla.falladas.sum(),
             "min": round(tabla["min"].sum(), 1)}
    tabla = pd.concat([tabla, pd.DataFrame([total])], ignore_index=True)
    texto = tabla.to_string(index=False)
    print(texto)
    with open("output/ciudad_resumen.txt", "w") as fh:
        fh.write(texto + "\n")

    # ── histograma ciudad a 6 m ──────────────────────────────────────────
    h = pd.concat(horas_ciudad, ignore_index=True)
    plt.rcParams.update({
        "figure.facecolor": FONDO, "axes.facecolor": FONDO,
        "text.color": TINTA, "axes.edgecolor": TINTA_3,
        "xtick.color": TINTA_2, "ytick.color": TINTA_2,
        "font.family": "Helvetica Neue",
    })
    fig, ax = plt.subplots(figsize=(12.8, 7.2), dpi=200)
    bins = np.arange(0, 12.5, 0.25)
    # frente relleno; contrafrente solo contorno: el solape no se embarra
    series = [("frente", AMBAR, dict(histtype="stepfilled", alpha=0.6,
                                     edgecolor=AMBAR, linewidth=1.2)),
              ("contrafrente", AZUL, dict(histtype="step", linewidth=1.8))]
    for i, (cara, color, estilo) in enumerate(series):
        v = h[h.cara == cara]["horas"]
        ax.hist(v, bins=bins, color=color, label=cara, zorder=3 + i, **estilo)
        med = v.median()
        ax.axvline(med, color=color, linewidth=1.1, alpha=0.85, ymax=0.84, zorder=5)
        lado = 1 if cara == "frente" else -1
        ax.annotate(f"mediana {cara}\n{med:.1f} h",
                    xy=(med, 0), xycoords=("data", "axes fraction"),
                    xytext=(med + lado * 0.15, 0.90 if cara == "frente" else 0.79),
                    textcoords=("data", "axes fraction"),
                    ha="left" if lado > 0 else "right",
                    fontsize=10.5, color=color, fontweight="medium")

    ax.set_xlim(0, 12)
    ax.grid(axis="y", color="white", alpha=0.06, zorder=0)
    for lado in ["top", "right", "left"]:
        ax.spines[lado].set_visible(False)
    ax.spines["bottom"].set_alpha(0.35)
    ax.tick_params(length=0, labelsize=10.5)
    ax.set_xlabel("horas de sol directo por día (promedio anual, planta a 6 m)",
                  fontsize=11.5, color=TINTA_2, labelpad=10)
    ax.set_ylabel("parcelas", fontsize=11.5, color=TINTA_2, labelpad=10)
    ax.legend(frameon=False, fontsize=11, loc="upper right",
              labelcolor=TINTA, borderaxespad=1.2)

    n_parcelas = h.smp.nunique()
    fig.suptitle("cuánto sol recibe buenos aires",
                 fontsize=21, fontweight="normal", color=TINTA, x=0.065,
                 ha="left", y=0.955)
    n_comunas = h["comuna"].nunique()
    ax.set_title(f"distribución de sol directo del frente y contrafrente de "
                 f"{n_parcelas:,} parcelas · {n_comunas} comunas".replace(",", "."),
                 fontsize=11.5, color=TINTA_2, loc="left", pad=16)
    fig.text(0.065, 0.02, "datasun · modelo solar 2.5D sobre tejido urbano de BA Data "
                          "(huellas y alturas ~2021) · posiciones solares pvlib",
             fontsize=9, color=TINTA_3)
    fig.tight_layout(rect=[0.02, 0.05, 0.98, 0.93])
    fig.savefig("output/ciudad_hist.png", facecolor=FONDO)
    plt.close(fig)
    print(f"\nHistograma: output/ciudad_hist.png ({n_parcelas:,} parcelas)".replace(",", "."))


if __name__ == "__main__":
    main(parcial="--parcial" in sys.argv)
