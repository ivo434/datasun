"""
Export de la base precomputada (output/comuna6.sqlite) a artefactos estáticos
para el frontend (web/public/data/). Sin backend: todo archivos.

Genera:
  web/public/data/smp/{smp}.json      un JSON por parcela: caras × alturas ×
                                      fechas con horas e intervalos, flags,
                                      confianza por cara, centroide y bbox
  web/public/data/indice_direcciones.json   calle normalizada → número → smp
  web/public/data/sol.json            posiciones solares de las 4 fechas (10 min)
  web/public/data/resumen.json        metadatos (n parcelas, fechas, alturas)

Correr desde la raíz del repo: .venv/bin/python scripts/export_frontend.py
"""

import json
import math
import os
import sqlite3
import sys
import zoneinfo
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fase0 import (CONFIG, CRS_METRICO, ELEVACION_MIN, FRENTES_ZIP, LAT_CABA,
                   LON_CABA, PARCELAS_ZIP, PASO_MINUTOS, TZ, palabras_calle,
                   smp_norm)

import geopandas as gpd
import pandas as pd
import pvlib

DB = os.environ.get("DATASUN_DB", "output/comuna6.sqlite")
DESTINO = "web/public/data"
COMUNA = "Comuna 6"
ALTURAS = list(range(3, 31, 3))

FLAGS_AMBAS = {"matching_fallback", "discrepancia_orientacion", "sin_construccion",
               "esquina"}


def exportar_heatmap(con):
    """SMP → horas de sol del FRENTE por (altura × estación), arrays paralelos
    minificados, valores en decihoras (int; -1 = sin dato). Es la capa de color
    del mapa: viaja entera, así que compacta. Incluye la dirección legible para
    el tooltip de hover."""
    horas = pd.read_sql(
        "SELECT smp, altura_m, fecha, horas FROM horas WHERE cara = 'frente'", con)
    fechas = CONFIG["fechas"]
    pos = {(a, f): i for i, (a, f) in
           enumerate((a, f) for a in ALTURAS for f in fechas)}
    tabla = {}
    for r in horas.itertuples():
        tabla.setdefault(r.smp, [-1] * len(pos))[pos[(int(r.altura_m), r.fecha)]] = \
            int(round(r.horas * 10))
    dirs = dict(con.execute("SELECT smp, direcciones FROM parcelas"))
    smps = sorted(tabla)
    doc = {"alturas": ALTURAS, "fechas": fechas, "smp": smps,
           "dir": [dirs.get(s) or "" for s in smps],
           "horas": [tabla[s] for s in smps]}
    ruta = f"{DESTINO}/heatmap_frente.json"
    with open(ruta, "w") as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(",", ":"))
    import gzip as _gz
    crudo = os.path.getsize(ruta)
    comprimido = len(_gz.compress(open(ruta, "rb").read()))
    print(f"  heatmap_frente.json: {len(smps)} parcelas, {crudo / 1e6:.2f} MB "
          f"({comprimido / 1e6:.2f} MB gzip)")


def exportar_suelo():
    """Contexto urbano del suelo (estética susurro): calzadas del callejero con
    ancho por jerarquía, vías de la red ferroviaria y espacios verdes públicos,
    recortados al entorno de la comuna y simplificados. Solo geometría — los
    colores/luminancias viven en la config visual del frontend."""
    parcelas = gpd.read_file(PARCELAS_ZIP, where=f"comuna = '{COMUNA}'").to_crs(CRS_METRICO)
    bx = parcelas.total_bounds
    margen = 900
    bbox = tuple(gpd.GeoSeries.from_xy([bx[0] - margen, bx[2] + margen],
                                       [bx[1] - margen, bx[3] + margen],
                                       crs=CRS_METRICO).to_crs("EPSG:4326").total_bounds)

    def lineas_de(gdf, simplificar=1.5):
        out = []
        for g in gdf.geometry:
            partes = g.geoms if g.geom_type.startswith("Multi") else [g]
            for L in partes:
                s = L.simplify(simplificar)
                out.append([[round(x, 6), round(y, 6)] for x, y in s.coords])
        return out

    ej = gpd.read_file("zip://data/callejero.zip!calles.shp", bbox=bbox).to_crs(CRS_METRICO)
    anchas = {"AVENIDA", "AUTOPISTA", "BOULEVARD"}
    calles = []
    for jerarquia, ancho in [(anchas, 20), (None, 12)]:
        sel = ej[ej["tipo_c"].isin(jerarquia)] if jerarquia else ej[~ej["tipo_c"].isin(anchas)]
        sel4326 = sel.to_crs("EPSG:4326")
        sel4326 = gpd.GeoDataFrame(geometry=sel.geometry.simplify(1.5), crs=CRS_METRICO).to_crs("EPSG:4326")
        for p in lineas_de(sel4326, 0):
            calles.append({"w": ancho, "p": p})

    ff = gpd.read_file("zip://data/red_ferrocarril.zip!red_ferrocarriles.shp",
                       bbox=bbox).to_crs(CRS_METRICO)
    vias = lineas_de(gpd.GeoDataFrame(geometry=ff.geometry.simplify(1.5),
                                      crs=CRS_METRICO).to_crs("EPSG:4326"), 0)

    ev = gpd.read_file("zip://data/espacios_verdes.zip!espacio_verde_publico.shp",
                       bbox=bbox).to_crs(CRS_METRICO)
    parques = []
    for g in gpd.GeoDataFrame(geometry=ev.geometry.simplify(1.5),
                              crs=CRS_METRICO).to_crs("EPSG:4326").geometry:
        partes = g.geoms if g.geom_type == "MultiPolygon" else [g]
        for pl in partes:
            if pl.is_empty:
                continue
            parques.append([[round(x, 6), round(y, 6)] for x, y in pl.exterior.coords])

    doc = {"calles": calles, "vias": vias, "parques": parques}
    ruta = f"{DESTINO}/suelo_urbano.json"
    with open(ruta, "w") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    print(f"  suelo_urbano.json: {len(calles)} calzadas, {len(vias)} tramos de vía, "
          f"{len(parques)} espacios verdes ({os.path.getsize(ruta) / 1e6:.2f} MB)")


def exportar_avenidas():
    """Etiquetas de avenidas para el TextLayer: puntos cada ~400 m sobre los
    ejes tipo AVENIDA de la comuna, con el ángulo del texto (CCW desde el este,
    plegado a ±90° para que nunca quede cabeza abajo)."""
    parcelas = gpd.read_file(PARCELAS_ZIP, where=f"comuna = '{COMUNA}'").to_crs(CRS_METRICO)
    bx = parcelas.total_bounds
    bbox = tuple(gpd.GeoSeries.from_xy([bx[0] - 100, bx[2] + 100],
                                       [bx[1] - 100, bx[3] + 100],
                                       crs=CRS_METRICO).to_crs("EPSG:4326").total_bounds)
    ej = gpd.read_file("zip://data/callejero.zip!calles.shp", bbox=bbox).to_crs(CRS_METRICO)
    av = ej[ej["tipo_c"] == "AVENIDA"]
    etiquetas = []
    for nombre, grupo in av.groupby("nomoficial"):
        puestos = []
        for g in grupo.geometry:
            partes = g.geoms if g.geom_type == "MultiLineString" else [g]
            for L in partes:
                m = L.interpolate(0.5, normalized=True)
                if any(m.distance(p) < 700 for p in puestos):
                    continue
                puestos.append(m)
                (x1, y1), (x2, y2) = L.coords[0], L.coords[-1]
                import math
                b180 = math.degrees(math.atan2(x2 - x1, y2 - y1)) % 180
                ang = 90 - b180
                if ang > 90: ang -= 180
                if ang < -90: ang += 180
                lonlat = gpd.GeoSeries([m], crs=CRS_METRICO).to_crs("EPSG:4326").iloc[0]
                etiquetas.append({"n": nombre, "p": [round(lonlat.x, 6), round(lonlat.y, 6)],
                                  "a": round(ang, 1)})
    with open(f"{DESTINO}/avenidas.json", "w") as fh:
        json.dump(etiquetas, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"  avenidas.json: {len(etiquetas)} etiquetas de "
          f"{av.nomoficial.nunique()} avenidas")


def confianza(cara, flags, metodo, tiene_horas):
    """Niveles de confianza del badge, derivados de los flags por cara."""
    propios = {f for f in flags if f in FLAGS_AMBAS or f == f"{cara}_obstruido"}
    if f"{cara}_obstruido" in propios or not tiene_horas:
        return "baja"
    if propios & {"sin_construccion", "discrepancia_orientacion"}:
        return "baja"
    if propios == {"matching_fallback"}:
        return "media"
    return "alta" if metodo == "linea_frente" else "media"


def main():
    os.makedirs(f"{DESTINO}/smp", exist_ok=True)
    con = sqlite3.connect(DB)

    print("Centroides y bbox de parcelas…")
    parcelas = gpd.read_file(PARCELAS_ZIP, where=f"comuna = '{COMUNA}'")
    parcelas["smp_n"] = parcelas["smp"].map(smp_norm)
    p4326 = parcelas.to_crs("EPSG:4326")
    geo = {}
    for f in p4326.itertuples():
        c = f.geometry.centroid
        b = f.geometry.bounds
        prev = geo.get(f.smp_n)
        if prev is None:
            geo[f.smp_n] = [c.x, c.y, list(b)]
        else:  # varios polígonos por smp: agrandar bbox
            prev[2] = [min(prev[2][0], b[0]), min(prev[2][1], b[1]),
                       max(prev[2][2], b[2]), max(prev[2][3], b[3])]

    print("Filas de horas…")
    horas = pd.read_sql("SELECT * FROM horas", con)
    horas_por_smp = defaultdict(list)
    for r in horas.itertuples():
        horas_por_smp[r.smp].append(r)

    print("JSON por SMP…")
    try:
        filas = con.execute("SELECT smp, barrio, direcciones, az_frente, az_contrafrente, "
                            "orient_frente, orient_contrafrente, metodo_frente, "
                            "calle_frente, flags, estado, error FROM parcelas").fetchall()
    except sqlite3.OperationalError:  # base v1 sin calle_frente
        filas = [(*f[:8], None, *f[8:]) for f in con.execute(
            "SELECT smp, barrio, direcciones, az_frente, az_contrafrente, "
            "orient_frente, orient_contrafrente, metodo_frente, flags, "
            "estado, error FROM parcelas")]
    n_archivos, bytes_smp = 0, 0
    for (smp, barrio, direcciones, azf, azc, orf, orc, metodo, calle_frente,
         flags_s, estado, error) in filas:
        flags = [f for f in (flags_s or "").split(",") if f]
        caras = {}
        for cara, az, orient in [("frente", azf, orf), ("contrafrente", azc, orc)]:
            datos = defaultdict(dict)
            for r in horas_por_smp.get(smp, []):
                if r.cara == cara:
                    datos[str(int(r.altura_m))][r.fecha] = {
                        "h": r.horas,
                        "iv": ["-".join(par) for par in json.loads(r.intervalos)],
                    }
            caras[cara] = {
                "az": az, "orient": orient,
                "confianza": confianza(cara, flags, metodo, bool(datos)),
                "en_validacion": cara == "contrafrente"
                                 and "contrafrente_v3_no_validado" in flags,
                "horas": datos,
            }
        g = geo.get(smp)
        doc = {"smp": smp, "barrio": barrio, "direcciones": direcciones or None,
               "estado": estado, "error": error, "flags": flags,
               "metodo": metodo, "calle_frente": calle_frente,
               "centro": g[:2] if g else None, "bbox": g[2] if g else None,
               "caras": caras}
        cuerpo = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
        with open(f"{DESTINO}/smp/{smp}.json", "w") as fh:
            fh.write(cuerpo)
        n_archivos += 1
        bytes_smp += len(cuerpo.encode())

    print("Índice dirección → SMP…")
    smps = set(geo)
    frentes = pd.DataFrame(gpd.read_file(FRENTES_ZIP, columns=["smp", "frente", "num_dom"],
                                         ignore_geometry=True))
    frentes["smp_n"] = frentes["smp"].map(smp_norm)
    frentes = frentes[frentes["smp_n"].isin(smps)]
    indice = {}
    for f in frentes.itertuples():
        nombre = str(f.frente or "").strip()
        if not nombre or str(f.num_dom) in ("N", "None", "nan"):
            continue
        clave = " ".join(sorted(palabras_calle(nombre)))
        ent = indice.setdefault(clave, {"nombre": nombre, "numeros": {}})
        for n in str(f.num_dom).split("."):
            if n.isdigit():
                ent["numeros"].setdefault(n, f.smp_n)
    with open(f"{DESTINO}/indice_direcciones.json", "w") as fh:
        json.dump(indice, fh, ensure_ascii=False, separators=(",", ":"))

    print("Posiciones solares…")
    tz = zoneinfo.ZoneInfo(TZ)
    sol = {}
    for fecha in CONFIG["fechas"]:
        d = datetime.fromisoformat(fecha).date()
        times = pd.date_range(datetime(d.year, d.month, d.day, tzinfo=tz),
                              periods=24 * 60 // PASO_MINUTOS, freq=f"{PASO_MINUTOS}min")
        sp = pvlib.solarposition.get_solarposition(times, LAT_CABA, LON_CABA)
        sol[fecha] = [[f"{t:%H:%M}", round(a, 1), round(e, 1)]
                      for t, a, e in zip(times, sp["azimuth"], sp["apparent_elevation"])]
    with open(f"{DESTINO}/sol.json", "w") as fh:
        json.dump(sol, fh, separators=(",", ":"))

    with open(f"{DESTINO}/resumen.json", "w") as fh:
        json.dump({"comuna": COMUNA, "parcelas": n_archivos,
                   "fechas": CONFIG["fechas"],
                   "alturas_m": sorted({int(a) for a in horas.altura_m.unique()}),
                   "paso_minutos": PASO_MINUTOS}, fh)

    mb = lambda p: os.path.getsize(p) / 1e6
    print(f"\nExport listo en {DESTINO}/")
    print(f"  smp/: {n_archivos} archivos, {bytes_smp / 1e6:.1f} MB "
          f"(promedio {bytes_smp / max(n_archivos, 1) / 1024:.1f} KB)")
    print(f"  indice_direcciones.json: {mb(DESTINO + '/indice_direcciones.json'):.2f} MB "
          f"({len(indice)} calles)")
    print(f"  sol.json: {mb(DESTINO + '/sol.json'):.2f} MB")
    exportar_heatmap(con)
    exportar_avenidas()
    exportar_suelo()


if __name__ == "__main__":
    que = sys.argv[1] if len(sys.argv) > 1 else "todo"
    if que == "todo":
        main()
    elif que == "heatmap":
        exportar_heatmap(sqlite3.connect(DB))
        exportar_avenidas()
    elif que == "suelo":
        exportar_suelo()
    else:
        sys.exit(f"uso: export_frontend.py [todo|heatmap]")
