"""
Export de las bases precomputadas (output/comuna{N}.sqlite) a artefactos
estáticos para el frontend (web/public/data/). Sin backend: todo archivos,
particionados por comuna para escalar a CABA completa.

Layout generado:
  data/comunas.json          metadatos: bbox por comuna, sección→comuna,
                             shards por comuna, percentiles citywide p5/p95
                             por (altura × fecha) para la rampa estable
  data/sol.json              posiciones solares (compartido)
  data/idx/{letra}.json      índice dirección→SMP particionado por primera
                             letra de la clave normalizada (lazy en el buscador)
  data/ciudad.json           agregado por manzana (huella + altura + decihoras
                             por altura×fecha) — capa de la vista ciudad
  data/barrios.json          etiquetas de barrios (nombre + centroide)
  data/suelo_urbano.json     susurro del suelo citywide (calles/vías/parques)
  data/avenidas.json         etiquetas de avenidas citywide
  data/c{N}/heatmap.json     heatmap del frente de la comuna (misma forma que antes)
  data/c{N}/p/{shard}.json   packs de ~64 parcelas: {smp: doc}; shard =
                             fnv1a(smp) % S con S potencia de 2 por comuna
  data/c{N}/tejido.geojson   prismas de la comuna (via fase0.exportar_tejido_geojson)

Uso (desde la raíz):
  .venv/bin/python scripts/export_frontend.py comuna 6     una comuna
  .venv/bin/python scripts/export_frontend.py ciudad       artefactos globales
  .venv/bin/python scripts/export_frontend.py tamanios     reporte de pesos
"""

import glob
import gzip
import json
import math
import os
import sqlite3
import sys
import zoneinfo
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fase0 import (CONFIG, CRS_METRICO, FRENTES_ZIP, LAT_CABA, LON_CABA,
                   PARCELAS_ZIP, PASO_MINUTOS, TEJIDO_ZIP, TZ, COL_ALTURA,
                   palabras_calle, smp_norm)

import geopandas as gpd
import numpy as np
import pandas as pd
import pvlib
from shapely.geometry import box
from shapely.ops import unary_union

DESTINO = "web/public/data"
ALTURAS = list(range(3, 31, 3))
COMUNAS = list(range(1, 16))
PARCELAS_POR_SHARD = 64

FLAGS_AMBAS = {"matching_fallback", "discrepancia_orientacion", "sin_construccion",
               "esquina"}


def fnv1a(s):
    """Hash del shard. Tiene un gemelo EXACTO en web/src/util.js (Math.imul):
    si se toca uno hay que tocar el otro."""
    h = 2166136261
    for b in s.encode():
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h


def shards_de(n_parcelas):
    """Potencia de 2 tal que los shards ronden PARCELAS_POR_SHARD parcelas."""
    return max(1, 1 << max(0, (n_parcelas // PARCELAS_POR_SHARD).bit_length()))


def db_de(nro):
    return os.environ.get("DATASUN_DB", f"output/comuna{nro}.sqlite")


def confianza(cara, flags, metodo, tiene_horas):
    propios = {f for f in flags if f in FLAGS_AMBAS or f == f"{cara}_obstruido"}
    if f"{cara}_obstruido" in propios or not tiene_horas:
        return "baja"
    if propios & {"sin_construccion", "discrepancia_orientacion"}:
        return "baja"
    if propios == {"matching_fallback"}:
        return "media"
    return "alta" if metodo == "linea_frente" else "media"


def bbox4326_de(parcelas):
    return [round(v, 6) for v in parcelas.to_crs("EPSG:4326").total_bounds]


# ── por comuna ───────────────────────────────────────────────────────────

def exportar_heatmap(con, nro):
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
    ruta = f"{DESTINO}/c{nro}/heatmap.json"
    with open(ruta, "w") as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(",", ":"))
    gz = len(gzip.compress(open(ruta, "rb").read()))
    print(f"  heatmap.json: {len(smps)} parcelas, "
          f"{os.path.getsize(ruta) / 1e6:.2f} MB ({gz / 1e6:.2f} MB gz)")


def exportar_comuna(nro):
    """Packs de parcelas + heatmap + índice parcial de la comuna."""
    con = sqlite3.connect(db_de(nro))
    os.makedirs(f"{DESTINO}/c{nro}/p", exist_ok=True)

    parcelas = gpd.read_file(PARCELAS_ZIP, where=f"comuna = 'Comuna {nro}'")
    parcelas["smp_n"] = parcelas["smp"].map(smp_norm)
    p4326 = parcelas.to_crs("EPSG:4326")
    geo = {}
    for f in p4326.itertuples():
        c = f.geometry.centroid
        b = f.geometry.bounds
        prev = geo.get(f.smp_n)
        if prev is None:
            geo[f.smp_n] = [c.x, c.y, list(b)]
        else:
            prev[2] = [min(prev[2][0], b[0]), min(prev[2][1], b[1]),
                       max(prev[2][2], b[2]), max(prev[2][3], b[3])]

    horas = pd.read_sql("SELECT * FROM horas", con)
    horas_por_smp = defaultdict(list)
    for r in horas.itertuples():
        horas_por_smp[r.smp].append(r)

    filas = con.execute("SELECT smp, barrio, direcciones, az_frente, az_contrafrente, "
                        "orient_frente, orient_contrafrente, metodo_frente, "
                        "calle_frente, flags, estado, error FROM parcelas").fetchall()
    S = shards_de(len(filas))
    packs = defaultdict(dict)
    bytes_docs = 0
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
        packs[fnv1a(smp) % S][smp] = doc
        bytes_docs += len(json.dumps(doc).encode())

    for sh, docs in packs.items():
        with open(f"{DESTINO}/c{nro}/p/{sh:03x}.json", "w") as fh:
            json.dump(docs, fh, ensure_ascii=False, separators=(",", ":"))
    pesos = [os.path.getsize(p) for p in glob.glob(f"{DESTINO}/c{nro}/p/*.json")]
    gz_medio = np.mean([len(gzip.compress(open(p, "rb").read()))
                        for p in sorted(glob.glob(f"{DESTINO}/c{nro}/p/*.json"))[:20]])
    print(f"  packs: {len(packs)} shards (S={S}), {sum(pesos) / 1e6:.1f} MB total, "
          f"~{np.mean(pesos) / 1024:.0f} KB/shard ({gz_medio / 1024:.0f} KB gz)")

    exportar_heatmap(con, nro)

    # índice parcial de la comuna (se mergea en `ciudad`)
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
    os.makedirs("output/idx_parcial", exist_ok=True)
    with open(f"output/idx_parcial/c{nro}.json", "w") as fh:
        json.dump(indice, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"  índice parcial: {len(indice)} calles")

    # metadatos para comunas.json
    meta = {"n": nro, "parcelas": len(filas), "S": S,
            "bbox": bbox4326_de(parcelas),
            "secciones": sorted({s.split("-")[0] for s in smps})}
    os.makedirs("output/meta_comuna", exist_ok=True)
    with open(f"output/meta_comuna/c{nro}.json", "w") as fh:
        json.dump(meta, fh)
    con.close()
    return meta


def exportar_tejido(nro, tolerancia=0.6):
    """Prismas de la comuna en 4 tiles por cuadrante del bbox (asignación por
    centroide): el frontend trae solo los cuadrantes del viewport (~1/4 del
    peso por fetch). Simplificación 0.6 m, precisión 6 decimales."""
    from shapely.strtree import STRtree
    parcelas = gpd.read_file(PARCELAS_ZIP, where=f"comuna = 'Comuna {nro}'") \
        .to_crs(CRS_METRICO)
    zona = unary_union(list(parcelas.geometry)).buffer(10)
    bx = parcelas.total_bounds
    caja = box(bx[0] - 50, bx[1] - 50, bx[2] + 50, bx[3] + 50)
    bbox = tuple(gpd.GeoSeries([caja], crs=CRS_METRICO).to_crs("EPSG:4326").total_bounds)
    gdf = gpd.read_file(TEJIDO_ZIP, bbox=bbox).to_crs(CRS_METRICO).reset_index(drop=True)
    gdf["altura"] = pd.to_numeric(gdf[COL_ALTURA], errors="coerce").fillna(0.0).round(1)

    tree = STRtree(list(gdf.geometry.values))
    idx = sorted(set(tree.query(zona, predicate="intersects")))
    sel = gdf.iloc[idx][["altura", "smp", "geometry"]].copy()
    sel["smp"] = sel["smp"].map(smp_norm)
    sel["geometry"] = sel.geometry.simplify(tolerancia, preserve_topology=True)

    cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
    cen = sel.geometry.centroid
    sel["q"] = (cen.x >= cx).astype(int) + 2 * (cen.y >= cy).astype(int)
    total = 0
    for q in range(4):
        tile = sel[sel["q"] == q][["altura", "smp", "geometry"]].to_crs("EPSG:4326")
        ruta = f"{DESTINO}/c{nro}/tejido-q{q}.geojson"
        if not len(tile):
            with open(ruta, "w") as fh:
                fh.write('{"type":"FeatureCollection","features":[]}')
            continue
        try:
            tile.to_file(ruta, driver="GeoJSON", COORDINATE_PRECISION=6, RFC7946="YES")
        except Exception:
            tile.to_file(ruta, driver="GeoJSON")
        total += os.path.getsize(ruta)
    print(f"  tejido: {len(sel)} prismas en 4 tiles ({total / 1e6:.1f} MB total, "
          f"~{total / 4e6:.1f} MB/tile)")


# ── globales (corren cuando todas las comunas están exportadas) ─────────

def exportar_indice_global():
    """Mergea los índices parciales y particiona por primera letra de la
    clave normalizada (misma primera letra que computa el buscador)."""
    os.makedirs(f"{DESTINO}/idx", exist_ok=True)
    por_letra = defaultdict(dict)
    for ruta in sorted(glob.glob("output/idx_parcial/c*.json")):
        for clave, ent in json.load(open(ruta)).items():
            letra = clave[0].lower() if clave and clave[0].isalpha() else "0"
            dest = por_letra[letra].setdefault(clave, {"nombre": ent["nombre"],
                                                       "numeros": {}})
            # una calle puede cruzar comunas: los números se suman
            for n, smp in ent["numeros"].items():
                dest["numeros"].setdefault(n, smp)
    total = 0
    for letra, dic in sorted(por_letra.items()):
        ruta = f"{DESTINO}/idx/{letra}.json"
        with open(ruta, "w") as fh:
            json.dump(dic, fh, ensure_ascii=False, separators=(",", ":"))
        total += os.path.getsize(ruta)
    mayor = max(por_letra, key=lambda l: os.path.getsize(f"{DESTINO}/idx/{l}.json"))
    print(f"  idx/: {len(por_letra)} letras, {total / 1e6:.2f} MB total, "
          f"mayor '{mayor}' "
          f"{os.path.getsize(f'{DESTINO}/idx/{mayor}.json') / 1e6:.2f} MB")


def exportar_comunas_json():
    """Metadatos globales: bbox y shards por comuna, sección→comuna y
    percentiles citywide por columna (altura×fecha) para rampa estable."""
    metas = []
    for ruta in sorted(glob.glob("output/meta_comuna/c*.json"),
                       key=lambda p: int(p.split("/c")[-1].split(".")[0])):
        metas.append(json.load(open(ruta)))
    # OJO: las secciones catastrales NO respetan límites de comuna (p.ej. la
    # sección 6 tiene parcelas en la 1 y en la 4) → lista de candidatas; el
    # frontend prueba los packs en orden
    secciones = {}
    for m in metas:
        for s in m.pop("secciones"):
            secciones.setdefault(s, []).append(m["n"])

    # percentiles citywide p5/p95 por columna, desde los heatmaps exportados
    cols = None
    valores = None
    for m in metas:
        hm = json.load(open(f"{DESTINO}/c{m['n']}/heatmap.json"))
        arr = np.array(hm["horas"], dtype=np.int16)
        cols = len(hm["alturas"]) * len(hm["fechas"])
        valores = arr if valores is None else np.vstack([valores, arr])
    p5, p95 = [], []
    for c in range(cols):
        col = valores[:, c]
        col = col[col >= 0]
        a = int(np.percentile(col, 5)) if len(col) else 0
        b = int(np.percentile(col, 95)) if len(col) else 1
        p5.append(a)
        p95.append(max(b, a + 1))

    doc = {"alturas": ALTURAS, "fechas": CONFIG["fechas"],
           "comunas": metas, "secciones": secciones,
           "p5": p5, "p95": p95}
    with open(f"{DESTINO}/comunas.json", "w") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    print(f"  comunas.json: {len(metas)} comunas, {len(secciones)} secciones "
          f"({os.path.getsize(DESTINO + '/comunas.json') / 1024:.0f} KB)")
    return doc


def exportar_ciudad():
    """Agregado por manzana para la vista ciudad: huella (unión de parcelas de
    la manzana, simplificada), altura representativa (p75 del tejido) y
    decihoras promedio del frente por (altura × fecha)."""
    fechas = CONFIG["fechas"]
    n_cols = len(ALTURAS) * len(fechas)
    manzanas = []
    for nro in COMUNAS:
        ruta_hm = f"{DESTINO}/c{nro}/heatmap.json"
        if not os.path.exists(ruta_hm):
            print(f"  (comuna {nro} sin heatmap todavía: se saltea)")
            continue
        hm = json.load(open(ruta_hm))
        arr = np.array(hm["horas"], dtype=np.float32)
        arr[arr < 0] = np.nan
        idx_smp = {s: i for i, s in enumerate(hm["smp"])}

        parcelas = gpd.read_file(PARCELAS_ZIP, where=f"comuna = 'Comuna {nro}'") \
            .to_crs(CRS_METRICO)
        parcelas["smp_n"] = parcelas["smp"].map(smp_norm)
        parcelas["mz"] = parcelas["smp_n"].map(lambda s: "-".join(s.split("-")[:2]))

        bx = parcelas.total_bounds
        caja = box(bx[0] - 30, bx[1] - 30, bx[2] + 30, bx[3] + 30)
        bbox = tuple(gpd.GeoSeries([caja], crs=CRS_METRICO)
                     .to_crs("EPSG:4326").total_bounds)
        tej = gpd.read_file(TEJIDO_ZIP, bbox=bbox, columns=["smp", COL_ALTURA]) \
            .to_crs(CRS_METRICO)
        tej["smp_n"] = tej["smp"].map(smp_norm)
        tej["alt"] = pd.to_numeric(tej[COL_ALTURA], errors="coerce").fillna(0.0)
        alt_smp = tej.groupby("smp_n")["alt"].max()

        for mz, grupo in parcelas.groupby("mz"):
            geom = unary_union(list(grupo.geometry)).buffer(2).buffer(-2)
            geom = geom.simplify(3)
            if geom.is_empty:
                continue
            polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
            anillo = max(polys, key=lambda p: p.area).exterior
            ring4326 = gpd.GeoSeries([anillo], crs=CRS_METRICO).to_crs("EPSG:4326")[0]
            p = [[round(x, 5), round(y, 5)] for x, y in ring4326.coords]

            filas = [idx_smp[s] for s in grupo["smp_n"].unique() if s in idx_smp]
            if not filas:
                continue
            with np.errstate(invalid="ignore"):
                v = np.nanmean(arr[filas, :], axis=0)
            v = [-1 if math.isnan(x) else int(round(x)) for x in v]
            alturas = alt_smp.reindex(grupo["smp_n"].unique()).dropna()
            alto = float(np.percentile(alturas, 75)) if len(alturas) else 6.0
            manzanas.append({"m": mz, "c": nro, "h": round(alto, 1), "p": p, "v": v})
        print(f"  comuna {nro}: {len(manzanas)} manzanas acumuladas")

    doc = {"alturas": ALTURAS, "fechas": fechas, "manzanas": manzanas}
    ruta = f"{DESTINO}/ciudad.json"
    with open(ruta, "w") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    gz = len(gzip.compress(open(ruta, "rb").read()))
    print(f"  ciudad.json: {len(manzanas)} manzanas, "
          f"{os.path.getsize(ruta) / 1e6:.2f} MB ({gz / 1e6:.2f} MB gz)")


def exportar_sol():
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
    print("  sol.json listo")


def exportar_suelo():
    """Susurro del suelo CITYWIDE. Las calles comunes se simplifican fuerte;
    la jerarquía (ancho) se conserva. Un solo archivo lazy tras la 1ª pintura."""
    def lineas_de(gdf_4326):
        out = []
        for g in gdf_4326.geometry:
            partes = g.geoms if g.geom_type.startswith("Multi") else [g]
            for L in partes:
                out.append([[round(x, 5), round(y, 5)] for x, y in L.coords])
        return out

    ej = gpd.read_file("zip://data/callejero.zip!calles.shp").to_crs(CRS_METRICO)
    ej = ej[ej["tipo_c"] != "FFCC"]
    anchas = {"AVENIDA", "AUTOPISTA", "BOULEVARD"}
    calles = []
    for jerarquia, ancho, tol in [(anchas, 20, 2), (None, 12, 4)]:
        sel = ej[ej["tipo_c"].isin(jerarquia)] if jerarquia else ej[~ej["tipo_c"].isin(anchas)]
        s4326 = gpd.GeoDataFrame(geometry=sel.geometry.simplify(tol),
                                 crs=CRS_METRICO).to_crs("EPSG:4326")
        for p in lineas_de(s4326):
            calles.append({"w": ancho, "p": p})

    ff = gpd.read_file("zip://data/red_ferrocarril.zip!red_ferrocarriles.shp") \
        .to_crs(CRS_METRICO)
    vias = lineas_de(gpd.GeoDataFrame(geometry=ff.geometry.simplify(2),
                                      crs=CRS_METRICO).to_crs("EPSG:4326"))

    ev = gpd.read_file("zip://data/espacios_verdes.zip!espacio_verde_publico.shp") \
        .to_crs(CRS_METRICO)
    parques = []
    for g in gpd.GeoDataFrame(geometry=ev.geometry.simplify(2),
                              crs=CRS_METRICO).to_crs("EPSG:4326").geometry:
        partes = g.geoms if g.geom_type == "MultiPolygon" else [g]
        for pl in partes:
            if not pl.is_empty and pl.area > 0:
                parques.append([[round(x, 5), round(y, 5)] for x, y in pl.exterior.coords])

    doc = {"calles": calles, "vias": vias, "parques": parques}
    ruta = f"{DESTINO}/suelo_urbano.json"
    with open(ruta, "w") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    gz = len(gzip.compress(open(ruta, "rb").read()))
    print(f"  suelo_urbano.json: {len(calles)} calzadas, {len(vias)} vías, "
          f"{len(parques)} parques ({os.path.getsize(ruta) / 1e6:.2f} MB, "
          f"{gz / 1e6:.2f} MB gz)")


def exportar_avenidas():
    ej = gpd.read_file("zip://data/callejero.zip!calles.shp").to_crs(CRS_METRICO)
    av = ej[ej["tipo_c"] == "AVENIDA"]
    etiquetas = []
    for nombre, grupo in av.groupby("nomoficial"):
        puestos = []
        for g in grupo.geometry:
            partes = g.geoms if g.geom_type == "MultiLineString" else [g]
            for L in partes:
                m = L.interpolate(0.5, normalized=True)
                if any(m.distance(p) < 900 for p in puestos):
                    continue
                puestos.append(m)
                (x1, y1), (x2, y2) = L.coords[0], L.coords[-1]
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
          f"{av.nomoficial.nunique()} avenidas "
          f"({os.path.getsize(DESTINO + '/avenidas.json') / 1e6:.2f} MB)")


def exportar_barrios():
    """Etiquetas de barrios para la vista ciudad: nombre + centroide, desde el
    dataset de barrios de BA Data (data/barrios.*)."""
    for cand in ["data/barrios.geojson", "zip://data/barrios.zip!barrios.shp"]:
        try:
            b = gpd.read_file(cand)
            break
        except Exception:
            b = None
    if b is None:
        print("  barrios: dataset no encontrado en data/ — saltear")
        return
    col = next(c for c in b.columns if c.lower() in ("barrio", "nombre", "name"))
    b = b.to_crs(CRS_METRICO)
    etiquetas = []
    for f in b.itertuples():
        c = f.geometry.representative_point()
        lonlat = gpd.GeoSeries([c], crs=CRS_METRICO).to_crs("EPSG:4326").iloc[0]
        etiquetas.append({"n": str(getattr(f, col)).upper(),
                          "p": [round(lonlat.x, 5), round(lonlat.y, 5)],
                          "a": round(f.geometry.area / 1e4)})  # ha, para prioridad
    with open(f"{DESTINO}/barrios.json", "w") as fh:
        json.dump(etiquetas, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"  barrios.json: {len(etiquetas)} barrios")


def reporte_tamanios():
    fam = {
        "packs de parcelas (c*/p)": "c*/p/*.json",
        "heatmaps por comuna": "c*/heatmap.json",
        "tejido por comuna (4 tiles c/u)": "c*/tejido-q*.geojson",
        "índice de direcciones (idx)": "idx/*.json",
        "globales (ciudad, comunas, sol, suelo, avenidas, barrios)":
            "[cbsa]*.json",
    }
    print(f"{'familia':55s} {'archivos':>9s} {'MB':>9s} {'MB gz':>9s}")
    for nombre, patron in fam.items():
        rutas = glob.glob(f"{DESTINO}/{patron}")
        crudo = sum(os.path.getsize(p) for p in rutas)
        gz = sum(len(gzip.compress(open(p, "rb").read())) for p in rutas)
        print(f"{nombre:55s} {len(rutas):9d} {crudo / 1e6:9.1f} {gz / 1e6:9.1f}")


if __name__ == "__main__":
    que = sys.argv[1] if len(sys.argv) > 1 else "ayuda"
    if que == "comuna":
        nro = int(sys.argv[2])
        os.makedirs(f"{DESTINO}/c{nro}", exist_ok=True)
        print(f"Comuna {nro}:")
        exportar_comuna(nro)
        exportar_tejido(nro)
    elif que == "ciudad":
        print("Globales:")
        exportar_comunas_json()
        exportar_indice_global()
        exportar_sol()
        exportar_ciudad()
        exportar_barrios()
        exportar_avenidas()
        exportar_suelo()
    elif que == "tejido":
        nro = int(sys.argv[2])
        os.makedirs(f"{DESTINO}/c{nro}", exist_ok=True)
        exportar_tejido(nro)
    elif que == "tamanios":
        reporte_tamanios()
    else:
        sys.exit("uso: export_frontend.py comuna N | tejido N | ciudad | tamanios")
