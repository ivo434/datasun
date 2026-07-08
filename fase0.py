"""
Motor de asoleamiento para CABA — fases 0 y 1.

Dado un punto (una ventana de un depto) calcula cuántas horas de sol directo
recibe en un día dado, considerando la sombra de los edificios vecinos.

Modelo 2.5D: cada edificio es un prisma (huella + altura). El test de oclusión
proyecta un rayo 2D hacia el azimut solar y compara la altura del rayo
(h_obs + d·tan(elevación)) contra la altura de cada edificio interceptado.

Datos (BA Data, en data/): tejido.zip (prismas), parcelas.zip (polígonos de
parcela), frentes_parcelas.zip (líneas de frente con calle y números de puerta).

Uso:  python fase0.py            ventana única (editar CONFIG abajo)
      python fase0.py curva      horas de sol anuales barriendo altura 3–30 m
      python fase0.py barrido    todas las parcelas en 500 m, frente a 6 m
"""

import json
import math
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
import zoneinfo
from collections import defaultdict
from datetime import datetime

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pvlib
from shapely.geometry import LineString, Point, box
from shapely.ops import nearest_points, unary_union
from shapely.strtree import STRtree

# ─── CONFIG: cambiar de ventana = cambiar estas tres/cuatro líneas ───────────

CONFIG = {
    "direccion": "Av. Rivadavia 4602",  # dirección CABA, se geocodifica vía USIG
    "altura_obs_m": 4.0,              # altura de la ventana en metros (fuente primaria)
    "piso": 2,                        # fallback si no hay altura_obs_m: piso × 3 m + 1 m (estimación)
    "modo": "contrafrente",           # "frente": ventana a la calle, usa 'orientacion'
                                      # "contrafrente": ventana al pulmón, orientación deducida
    "orientacion": None,              # None = deducir del eje de calle; para modo "frente" se puede
                                      # forzar con N NE E SE S SO O NO o azimut en grados
    "fechas": ["2026-12-21", "2026-03-21", "2026-06-21", "2026-09-21"],  # la fecha de hoy se agrega sola
}

# ─── Constantes del modelo ───────────────────────────────────────────────────

ALTURA_PISO = 3.0        # m por piso (el tejido trae pisos, no metros)
ALTURA_ANTEPECHO = 1.0   # m sobre el nivel de piso del depto (altura de la ventana)
OFFSET_FACHADA = 1.0     # m que corremos el punto "hacia afuera" de la fachada de la construcción
AREA_MIN_CONSTRUCCION = 25.0  # m²: componentes de huella más chicos (quincho, pared de
                              # fondo, tanque) no cuentan como "la construcción principal"
LARGO_RAYO = 200.0       # m: más allá de esto un edificio debería ser altísimo para tapar
ELEVACION_MIN = 5.0      # grados: bajo esto el sol está pegado al horizonte, lo descartamos
PASO_MINUTOS = 10
RADIO_CARGA = 300.0      # m: bbox de tejido a cargar alrededor del punto (> LARGO_RAYO)

LAT_CABA, LON_CABA = -34.60, -58.38
TZ = "America/Argentina/Buenos_Aires"
CRS_METRICO = "EPSG:5347"  # POSGAR 2007 / Argentina faja 5, unidades en metros.
# Nota: el meridiano central de la faja 5 es -60°; en CABA (-58.4°) la convergencia
# de meridianos es ~0.9°, o sea el "norte de la grilla" difiere del norte verdadero
# en menos de 1°. Para fase 0 (rayos de 200 m) el error lateral es < 2 m: ignorable.

# El shapefile viene adentro de una subcarpeta del zip.
TEJIDO_ZIP = "zip://data/tejido.zip!tejido/tejido.shp"
# 'altura' viene en METROS (fotogrametría, Sec. Planeamiento). La columna 'altos'
# (pisos) viene en 0 en todo el dataset, no sirve. Si algún día se usa una fuente
# en pisos, poner UNIDAD_ALTURA = "pisos" y se multiplica por ALTURA_PISO.
COL_ALTURA = "altura"
UNIDAD_ALTURA = "metros"

# Fase 1: parcelas catastrales (polígonos) y frentes de parcela (líneas con
# nombre de calle y números de puerta — fuente primaria de matching por dirección).
PARCELAS_ZIP = "zip://data/parcelas.zip!parcelas_catastrales.shp"
FRENTES_ZIP = "zip://data/frentes_parcelas.zip!frente-parcelas/frente-parcelas.shp"
CALLEJERO_ZIP = "zip://data/callejero.zip!calles.shp"  # ejes de calle, para el barrido

PUNTOS_CARDINALES = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SO": 225, "O": 270, "NO": 315}


def altura_observador(cfg):
    """Altura del observador en metros: altura_obs_m directo si está, si no
    se estima desde el piso (con warning: 3 m/piso es una convención)."""
    if cfg.get("altura_obs_m") is not None:
        return float(cfg["altura_obs_m"])
    h = cfg["piso"] * ALTURA_PISO + ALTURA_ANTEPECHO
    print(f"WARNING: sin altura_obs_m en CONFIG; estimo piso {cfg['piso']} × "
          f"{ALTURA_PISO:.0f} m + {ALTURA_ANTEPECHO:.0f} m de antepecho = {h:.1f} m. "
          f"Es una estimación: si podés medir la altura real, usá altura_obs_m.")
    return h


def azimut_orientacion(orientacion):
    if isinstance(orientacion, (int, float)):
        return float(orientacion) % 360
    return PUNTOS_CARDINALES[orientacion.upper()]


def direccion_hacia_sol(az_grados):
    """Vector unitario 2D DESDE el observador HACIA el sol, en la grilla métrica.

    Convención (única en todo el script — si hay que corregir, es acá):
    - pvlib devuelve el azimut solar en grados desde el NORTE, en sentido horario
      (0°=N, 90°=E, 180°=S, 270°=O).
    - En el CRS métrico x crece hacia el ESTE e y hacia el NORTE, así que el
      vector hacia el sol es (sin(az), cos(az)).
    El rayo de oclusión se proyecta desde el observador hacia el sol (mismo
    azimut, NO el opuesto): si un prisma corta ese segmento y es más alto que
    el rayo en el punto de entrada, tapa el sol.
    """
    az = math.radians(az_grados)
    return math.sin(az), math.cos(az)


def test_azimut():
    """Asserts sobre la convención de azimut de pvlib vs. la del test de oclusión.

    En CABA (hemisferio sur) el sol culmina al NORTE, por eso al mediodía solar
    el azimut debe ser ~0°/360° tanto en verano como en invierno.
    """
    # direccion_hacia_sol: los cuatro puntos cardinales caen donde deben
    for az, (ex, ey) in [(0, (0, 1)), (90, (1, 0)), (180, (0, -1)), (270, (-1, 0))]:
        dx, dy = direccion_hacia_sol(az)
        assert abs(dx - ex) < 1e-9 and abs(dy - ey) < 1e-9, f"direccion_hacia_sol({az})"

    tz = zoneinfo.ZoneInfo(TZ)
    for fecha in ["2026-12-21", "2026-06-21"]:
        d = datetime.fromisoformat(fecha).date()
        times = pd.date_range(datetime(d.year, d.month, d.day, tzinfo=tz),
                              periods=24 * 6, freq="10min")
        sp = pvlib.solarposition.get_solarposition(times, LAT_CABA, LON_CABA)
        az, elev = sp["azimuth"], sp["apparent_elevation"]

        mediodia = elev.idxmax()  # mediodía solar = máxima elevación
        az_m = az.loc[mediodia]
        assert min(az_m, 360 - az_m) < 5, \
            f"{fecha}: azimut al mediodía solar = {az_m:.1f}°, esperaba ~0/360 (norte)"

        de_dia = elev > ELEVACION_MIN
        az_manana = az[de_dia & (az.index < mediodia)]
        az_tarde = az[de_dia & (az.index > mediodia)]
        assert az_manana.between(45, 135).any(), \
            f"{fecha}: a la mañana el sol nunca pasa por el cuadrante este (45–135°)"
        assert az_tarde.between(225, 315).any(), \
            f"{fecha}: a la tarde el sol nunca pasa por el cuadrante oeste (225–315°)"
    print("test_azimut OK: pvlib usa azimut desde el norte horario y el rayo "
          "se proyecta hacia el sol; culminación al norte en ambas estaciones.")


def geocodificar_usig(direccion, verbose=True):
    """Dirección CABA → (lon, lat, nombre_calle, altura) vía el normalizador de USIG."""
    url = "https://servicios.usig.buenosaires.gob.ar/normalizar/?" + urllib.parse.urlencode(
        {"direccion": direccion, "geocodificar": "true"}
    )
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.loads(resp.read())
    for d in data.get("direccionesNormalizadas", []):
        if d.get("cod_partido") == "caba" and "coordenadas" in d:
            c = d["coordenadas"]
            if verbose:
                print(f"Dirección normalizada: {d['direccion']}  (lon {c['x']}, lat {c['y']})")
            return float(c["x"]), float(c["y"]), d["nombre_calle"], d["altura"]
    sys.exit(f"USIG no pudo geocodificar '{direccion}' dentro de CABA.")


def a_metrico(lon, lat):
    return gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(CRS_METRICO).iloc[0]


def bearing_calle(calle, altura):
    """Bearing del eje de la calle: se geocodifican dos alturas de la misma calle,
    una a cada lado del objetivo, y se toma el vector entre ambas. Devuelve grados
    desde el norte, horario. El eje es bidireccional: bearing y bearing+180 dan lo mismo."""
    def intentar(candidatas):
        for a in candidatas:
            if a <= 0:
                continue
            try:
                lon, lat, _, _ = geocodificar_usig(f"{calle} {a}", verbose=False)
                return a, a_metrico(lon, lat)
            except SystemExit:
                continue
        sys.exit(f"ABORTO: no pude geocodificar ninguna altura vecina de '{calle}' "
                 f"para calcular el eje de la calle.")

    a1, p1 = intentar([altura - 32, altura - 20, altura - 40, altura - 12, altura - 52])
    a2, p2 = intentar([altura + 38, altura + 20, altura + 40, altura + 12, altura + 52])
    if p1.distance(p2) < 10:
        sys.exit("ABORTO: las dos alturas geocodificadas caen casi en el mismo punto; "
                 "no se puede estimar el eje de la calle.")
    b = math.degrees(math.atan2(p2.x - p1.x, p2.y - p1.y)) % 360
    print(f"Eje de calle: {calle} {a1} → {a2} ({p1.distance(p2):.0f} m), bearing {b:.0f}° (±180°).")
    dx, dy = direccion_hacia_sol(b)
    eje = LineString([(p1.x - 150 * dx, p1.y - 150 * dy),
                      (p2.x + 150 * dx, p2.y + 150 * dy)])  # eje extendido, para distancias
    return b, eje


def normales_por_eje(punto_usig, bearing, huella):
    """Normal del frente/contrafrente a partir del eje de la calle.

    La perpendicular al eje tiene dos sentidos; el edificio está del lado donde
    el rayo perpendicular desde el punto USIG entra antes a la huella del SMP.
    Devuelve (az_contrafrente, az_frente): la ventana de contrafrente mira en el
    sentido eje→edificio (hacia el pulmón); la de frente, el opuesto exacto."""
    candidatos = []
    for s in (90, -90):
        az = (bearing + s) % 360
        dx, dy = direccion_hacia_sol(az)
        rayo = LineString([(punto_usig.x, punto_usig.y),
                           (punto_usig.x + 60 * dx, punto_usig.y + 60 * dy)])
        corte = huella.intersection(rayo)
        if not corte.is_empty:
            candidatos.append((punto_usig.distance(corte), az))
    if candidatos:
        az_hacia_edificio = min(candidatos)[1]
    else:
        # El punto de USIG puede estar corrido a lo largo de la cuadra y el rayo
        # perpendicular pasar al costado de la huella. Fallback: el lado se decide
        # por la proyección del centroide sobre la perpendicular (el centroide es
        # mal estimador del ángulo pero inequívoco para el lado de la calle).
        c = huella.centroid
        az = (bearing + 90) % 360
        dx, dy = direccion_hacia_sol(az)
        proy = (c.x - punto_usig.x) * dx + (c.y - punto_usig.y) * dy
        az_hacia_edificio = az if proy > 0 else (bearing - 90) % 360
        print("aviso: la perpendicular desde el punto USIG no cruza la huella; "
              "el lado de la calle se decidió por la proyección del centroide.")
    return az_hacia_edificio, (az_hacia_edificio + 180) % 360


def cargar_tejido(lon, lat, radio=RADIO_CARGA):
    """Carga las huellas de edificios en un bbox de `radio` alrededor del punto,
    reproyectadas a CRS métrico. Devuelve (gdf, punto_metrico: Point)."""
    punto_m = a_metrico(lon, lat)

    # bbox en el CRS del shapefile para no cargar toda la ciudad
    buf = gpd.GeoSeries([punto_m.buffer(radio)], crs=CRS_METRICO).to_crs("EPSG:4326")
    gdf = gpd.read_file(TEJIDO_ZIP, bbox=tuple(buf.total_bounds))
    if gdf.empty:
        sys.exit("El bbox no trajo ningún edificio: ¿el punto cae dentro de CABA?")
    gdf = gdf.to_crs(CRS_METRICO)

    if COL_ALTURA not in gdf.columns:
        sys.exit(f"No existe la columna '{COL_ALTURA}'. Columnas: {list(gdf.columns)}")
    gdf["altura_m"] = pd.to_numeric(gdf[COL_ALTURA], errors="coerce").fillna(0.0)
    if UNIDAD_ALTURA == "pisos":
        gdf["altura_m"] *= ALTURA_PISO
    gdf["smp_n"] = gdf["smp"].map(smp_norm)
    print(f"Tejido cargado: {len(gdf)} edificios en un radio de ~{radio:.0f} m "
          f"(alturas {gdf['altura_m'].min():.0f}–{gdf['altura_m'].max():.0f} m).")
    return gdf.reset_index(drop=True), punto_m


def etiqueta_edificio(fila):
    """Identificador humano de un edificio del tejido (para verificar en la realidad)."""
    smp = fila.get("smp") or ""
    return f"SMP {smp} (sección-manzana-parcela)" if smp else f"huella #{fila.name}"


def punto_cardinal(az):
    """Azimut en grados → punto cardinal más cercano de los 8 (N, NE, ...)."""
    return min(PUNTOS_CARDINALES, key=lambda k: abs((az - PUNTOS_CARDINALES[k] + 180) % 360 - 180))


def smp_norm(s):
    """Normaliza SMP entre datasets: tejido '57-012-026B', frentes '057-012-026c',
    parcelas '057 - 012 - 026c' → todos a '57-012-026C'."""
    t = [p.strip() for p in str(s).split("-")]
    if len(t) != 3 or not t[0].isdigit():
        return str(s).strip().upper()
    return f"{int(t[0])}-{t[1]}-{t[2].upper()}"


def palabras_calle(nombre):
    """Nombre de calle → conjunto de palabras, para matchear 'GAONA AV.'
    (USIG) con 'AV. GAONA' (frentes-parcelas), acentos y orden incluidos."""
    return frozenset(re.sub(r"[.,]", " ", str(nombre).upper()).split())


def cargar_parcelas(lon, lat, radio=RADIO_CARGA):
    """Parcelas catastrales y frentes de parcela en un bbox alrededor del punto,
    en CRS métrico y con SMP normalizado en la columna smp_n."""
    centro = a_metrico(lon, lat)
    bbox = tuple(gpd.GeoSeries([centro.buffer(radio)], crs=CRS_METRICO)
                 .to_crs("EPSG:4326").total_bounds)
    parcelas = gpd.read_file(PARCELAS_ZIP, bbox=bbox).to_crs(CRS_METRICO).reset_index(drop=True)
    frentes = gpd.read_file(FRENTES_ZIP, bbox=bbox).to_crs(CRS_METRICO).reset_index(drop=True)
    parcelas["smp_n"] = parcelas["smp"].map(smp_norm)
    frentes["smp_n"] = frentes["smp"].map(smp_norm)
    print(f"Parcelas cargadas: {len(parcelas)}; frentes de parcela: {len(frentes)}.")
    return parcelas, frentes


def parcela_objetivo(parcelas, frentes, calle, altura, gdf, punto_usig):
    """Parcela del objetivo. Fuente primaria: matching exacto por dirección en
    frentes-parcelas (nombre de calle + número de puerta en num_dom). Fallback:
    heurística fase 0 (prisma más cercano al punto USIG) — menos confiable,
    puede elegir la parcela vecina (le pasó al caso de validación: 026B vs 026C).
    Devuelve (smp normalizado, polígono de parcela, línea de frente o None)."""
    obj = palabras_calle(calle)
    sobre_calle = frentes[frentes["lindero"] == "CALLE"]

    def poligono_de(smp):
        pol = parcelas[parcelas["smp_n"] == smp]
        return unary_union(list(pol.geometry)) if not pol.empty else None

    # Pase 1: número de puerta exacto; pase 2: dentro del rango de la parcela
    for exacto in (True, False):
        for _, f in sobre_calle.iterrows():
            if palabras_calle(f["frente"]) != obj:
                continue
            nums = [int(n) for n in str(f["num_dom"]).split(".") if n.isdigit()]
            if not nums:
                continue
            if (altura in nums) if exacto else (min(nums) <= altura <= max(nums)):
                pol = poligono_de(f["smp_n"])
                if pol is not None:
                    print(f"Parcela por dirección ({'puerta exacta' if exacto else 'rango'}): "
                          f"SMP {f['smp_n']}, frente sobre {f['frente']} {f['num_dom']}.")
                    return f["smp_n"], pol, f.geometry

    smp_tejido, _, huella = huella_smp_objetivo(gdf, punto_usig)
    smp = smp_norm(smp_tejido)
    pol = poligono_de(smp)
    print(f"aviso: sin match por dirección en frentes-parcelas; fallback por prisma "
          f"más cercano → SMP {smp} (menos confiable: puede ser la parcela vecina).")
    return smp, pol if pol is not None else huella, None


def huella_smp_objetivo(gdf, punto_usig):
    """SMP objetivo: el del prisma más cercano al punto de USIG (radio 30 m).
    Devuelve (smp, idx_smp, huella combinada de todos sus prismas)."""
    dists = gdf.geometry.distance(punto_usig)
    i_prox = int(dists.idxmin())
    if dists.iloc[i_prox] > 30:
        sys.exit(f"ABORTO: no hay ningún prisma a menos de 30 m del punto de USIG "
                 f"(el más cercano está a {dists.iloc[i_prox]:.0f} m). Revisá la dirección.")
    smp = gdf.iloc[i_prox]["smp"]
    idx_smp = list(gdf.index[gdf["smp"] == smp])
    return smp, idx_smp, unary_union(list(gdf.geometry.loc[idx_smp]))


def orientacion_por_centroide(huella, punto_usig):
    """MÉTODO VIEJO: normal del contrafrente = vector punto USIG → centroide de
    la huella. Falla con lotes profundos o construcción irregular (confirmado
    contra observación real en el caso de validación: dio S cuando la ventana real
    tiene componente oeste). Se conserva SOLO para el sanity check contra el
    método del eje de calle. Devuelve azimut o None si es degenerado."""
    c = huella.centroid
    vx, vy = c.x - punto_usig.x, c.y - punto_usig.y
    if math.hypot(vx, vy) < 1.0:
        return None
    return math.degrees(math.atan2(vx, vy)) % 360


def ubicar_en_parcela(gdf, punto_usig, parcela, idx_smp, eje, az_ventana, contrafrente):
    """Fase 1: observador sobre el borde real de la parcela — el punto del
    perímetro más cercano al eje de calle (frente) o el más lejano
    (contrafrente) — corrido OFFSET_FACHADA hacia afuera de la construcción,
    mirando az_ventana. Devuelve (obs, excluidos).

    Si el punto elegido corrido cae dentro de una huella construida (típico en
    contrafrente: el vértice trasero está sobre la medianera y el vecino de
    atrás construyó hasta ahí), se prueban los siguientes puntos del perímetro
    en orden de preferencia, con aviso. Aborta si ninguno queda libre."""
    dx, dy = direccion_hacia_sol(az_ventana)
    polys = parcela.geoms if parcela.geom_type == "MultiPolygon" else [parcela]
    verts = [Point(p) for poly in polys for p in dict.fromkeys(poly.exterior.coords)]
    if contrafrente:
        candidatos = sorted(verts, key=eje.distance, reverse=True)
    else:
        borde = unary_union([LineString(poly.exterior.coords) for poly in polys])
        candidatos = [nearest_points(borde, eje)[0]] + sorted(verts, key=eje.distance)

    tree = STRtree(list(gdf.geometry.values))
    elegido = None
    for v in candidatos:
        p = Point(v.x + OFFSET_FACHADA * dx, v.y + OFFSET_FACHADA * dy)
        if len(tree.query(p, predicate="within")) == 0:
            elegido = (v, p)
            break
    if elegido is None:
        sys.exit(f"ABORTO: todos los puntos del perímetro de la parcela, corridos "
                 f"{OFFSET_FACHADA:.0f} m hacia {az_ventana:.0f}°, caen dentro de una "
                 f"huella construida; el posicionamiento no es confiable acá.")
    v, obs = elegido
    if not v.equals_exact(candidatos[0], 1e-6):
        print(f"aviso: el punto {'más lejano' if contrafrente else 'más cercano'} al eje "
              f"caía dentro de una construcción; se usó otro punto del perímetro "
              f"(a {eje.distance(v):.0f} m del eje).")

    # Solo para contrafrente: la calle (punto USIG) debe quedar a espaldas de la ventana
    if contrafrente and (punto_usig.x - obs.x) * dx + (punto_usig.y - obs.y) * dy > 0:
        sys.exit(f"ABORTO: la normal de contrafrente ({az_ventana:.0f}°) apunta hacia "
                 f"la calle en vez del pulmón de manzana; revisá la geometría del lote.")

    # Exclusión del edificio propio — criterio sin cambios desde fase 0: del SMP
    # propio se excluyen SOLO los prismas que contienen/tocan al observador
    # ("tocar" = estar a <= OFFSET_FACHADA + 0.1 m, por el corrimiento hacia
    # afuera). El resto del SMP —cuerpo delantero, construcciones del patio—
    # sigue pudiendo bloquear: un contrafrente puede tener sombra propia.
    excluidos = {i for i in idx_smp
                 if gdf.geometry.loc[i].distance(obs) <= OFFSET_FACHADA + 0.1}
    return obs, excluidos


def construccion_principal(geoms_smp):
    """Componente construido contiguo de mayor huella entre los prismas del SMP.
    Los prismas chicos separados (quincho, pared de fondo, tanque) no cuentan
    como 'la construcción': umbral AREA_MIN_CONSTRUCCION. None si no hay nada."""
    geoms_smp = list(geoms_smp)
    if not geoms_smp:
        return None
    u = unary_union(geoms_smp)
    partes = list(u.geoms) if u.geom_type == "MultiPolygon" else [u]
    grandes = [p for p in partes if p.area >= AREA_MIN_CONSTRUCCION]
    return max(grandes, key=lambda p: p.area) if grandes else None


def ubicar_en_fachada(gdf, punto_usig, parcela, idx_smp, eje, az_ventana, contrafrente):
    """Posicionamiento v3: observador en el punto medio de la arista TRASERA
    (contrafrente) o DELANTERA (frente) de la construcción principal del SMP,
    corrido OFFSET_FACHADA hacia afuera (al patio o a la calle).

    Las ventanas están en las paredes de la construcción, no en el límite del
    lote: la v2 (borde de parcela) ponía al observador de contrafrente pegado a
    la pared del fondo del patio, confirmado incorrecto contra observación real.
    La pared del fondo sigue siendo un bloqueador legítimo (no se excluye).
    Fallback a borde de parcela (v2) si el SMP no tiene construcción."""
    princ = construccion_principal(gdf.geometry.loc[i] for i in idx_smp)
    if princ is None:
        print("aviso: SMP sin construcción principal en el tejido; "
              "observador sobre el borde de la parcela (v2).")
        return ubicar_en_parcela(gdf, punto_usig, parcela, idx_smp, eje, az_ventana,
                                 contrafrente)
    # Aristas del perímetro de la construcción (>= 2 m), ordenadas por distancia
    # de su punto medio al eje de calle: la más lejana es la fachada trasera,
    # la más cercana la delantera.
    coords = list(princ.exterior.coords)
    aristas = [LineString([a, b]) for a, b in zip(coords[:-1], coords[1:])
               if math.dist(a, b) >= 2.0]
    if not aristas:
        sys.exit("ABORTO: la construcción principal no tiene aristas de más de 2 m.")
    dist_eje = lambda A: eje.distance(A.interpolate(0.5, normalized=True))
    aristas.sort(key=dist_eje, reverse=contrafrente)

    dx, dy = direccion_hacia_sol(az_ventana)
    geoms = list(gdf.geometry.values)
    tree = STRtree(geoms)

    def metros_libres(p):
        """Distancia libre desde p en la dirección de la ventana (hasta 60 m),
        ignorando prismas que tocan p."""
        rayo = LineString([(p.x, p.y), (p.x + 60 * dx, p.y + 60 * dy)])
        d_min = 60.0
        for i in tree.query(rayo):
            if geoms[i].distance(p) <= 0.15:
                continue
            corte = geoms[i].intersection(rayo)
            if not corte.is_empty:
                d_min = min(d_min, p.distance(corte))
        return d_min

    # Desempate entre aristas igual de traseras/delanteras (±2 m): la ventana
    # está donde hay vista — se prefiere la arista con más aire libre adelante.
    # (Caso real: dos aristas traseras empatadas, una contra la medianera del
    # vecino a 3 m y otra al patio con 7 m libres; la ventana está en la segunda.)
    empatadas = [A for A in aristas if abs(dist_eje(A) - dist_eje(aristas[0])) <= 2.0]
    if len(empatadas) > 1:
        empatadas.sort(key=lambda A: metros_libres(
            Point(A.interpolate(0.5, normalized=True).x + OFFSET_FACHADA * dx,
                  A.interpolate(0.5, normalized=True).y + OFFSET_FACHADA * dy)),
            reverse=True)
    candidatas = empatadas + [A for A in aristas if A not in empatadas]

    elegida = None
    for A in candidatas:
        m = A.interpolate(0.5, normalized=True)
        p = Point(m.x + OFFSET_FACHADA * dx, m.y + OFFSET_FACHADA * dy)
        if len(tree.query(p, predicate="within")) == 0:
            elegida = (A, p)
            break
    if elegida is None:
        sys.exit("ABORTO: el punto medio de todas las aristas de la construcción, "
                 "corrido hacia afuera, cae dentro de una huella construida.")
    A, obs = elegida
    if A is not candidatas[0]:
        lado = "trasera" if contrafrente else "delantera"
        print(f"aviso: el punto medio de la arista {lado} preferida quedaba dentro "
              f"de una construcción; se usó la siguiente arista.")

    # Solo para contrafrente: la calle (punto USIG) debe quedar a espaldas
    if contrafrente and (punto_usig.x - obs.x) * dx + (punto_usig.y - obs.y) * dy > 0:
        sys.exit(f"ABORTO: la normal de contrafrente ({az_ventana:.0f}°) apunta hacia "
                 f"la calle en vez del pulmón de manzana; revisá la geometría del lote.")

    # Regla de exclusión sin cambios: solo prismas del SMP propio que tocan al
    # observador (<= OFFSET_FACHADA + 0.1 m). La pared del fondo del patio y el
    # resto del SMP siguen pudiendo bloquear.
    excluidos = {i for i in idx_smp
                 if gdf.geometry.loc[i].distance(obs) <= OFFSET_FACHADA + 0.1}
    return obs, excluidos


def chequeo_pared(gdf, obs, h_obs, az_ventana, excluidos):
    """Chequeo de coherencia del posicionamiento: barre un abanico de rayos de
    ±60° alrededor de la normal de la ventana, y reporta el obstáculo MÁS
    restrictivo (el que exige mayor elevación solar para superarlo desde obs).
    Devuelve (índice de ese prisma, elevación mínima en grados)."""
    geoms = list(gdf.geometry.values)
    tree = STRtree(geoms)
    peor = None  # (elev_req, i, d, az): el punto más alto del horizonte visto desde obs
    for delta in range(-60, 61, 10):
        dx, dy = direccion_hacia_sol((az_ventana + delta) % 360)
        rayo = LineString([(obs.x, obs.y), (obs.x + 60 * dx, obs.y + 60 * dy)])
        for i in tree.query(rayo):
            if i in excluidos:
                continue
            corte = geoms[i].intersection(rayo)
            if corte.is_empty:
                continue
            d = obs.distance(corte)
            alt = float(gdf["altura_m"].iloc[i])
            elev_req = math.degrees(math.atan2(alt - h_obs, d)) if alt > h_obs else 0.0
            if peor is None or elev_req > peor[0]:
                peor = (elev_req, i, d, (az_ventana + delta) % 360)
    if peor is None:
        print("Chequeo de coherencia: ningún prisma enfrenta la ventana en 60 m.")
        return None, 0.0
    elev_req, i, d, az = peor
    print(f"Chequeo de coherencia: el obstáculo más restrictivo frente a la ventana "
          f"es {etiqueta_edificio(gdf.iloc[i])}, {gdf['altura_m'].iloc[i]:.0f} m de alto "
          f"a {d:.1f} m (azimut {az:.0f}°); desde h_obs {h_obs:.1f} m se supera con "
          f"elevación solar > {elev_req:.0f}°.")
    return i, elev_req


def edificio_que_tapa(punto, h_obs, az_sol, elev_sol, tree, geoms, alturas, excluidos):
    """Test de oclusión 2.5D. Devuelve el índice del edificio más cercano que
    bloquea el rayo hacia el sol, o None si hay sol directo."""
    # Segmento DESDE el observador HACIA el sol; convención en direccion_hacia_sol()
    dx, dy = direccion_hacia_sol(az_sol)
    rayo = LineString([(punto.x, punto.y),
                       (punto.x + LARGO_RAYO * dx, punto.y + LARGO_RAYO * dy)])
    tan_e = math.tan(math.radians(elev_sol))

    bloqueo, d_min = None, float("inf")
    for i in tree.query(rayo):
        if i in excluidos:
            continue
        corte = geoms[i].intersection(rayo)
        if corte.is_empty:
            continue
        d = punto.distance(corte)  # distancia al punto de entrada del rayo en la huella
        if alturas[i] > h_obs + d * tan_e and d < d_min:
            bloqueo, d_min = i, d
    return bloqueo, d_min


def horas_de_sol(gdf, obs, h_obs, az_ventana, fecha, excluidos):
    """Muestrea el día cada PASO_MINUTOS y clasifica cada instante en:
    'sol', índice de edificio (sombra), 'espalda' (sol detrás de la ventana)
    o 'noche' (elevación < ELEVACION_MIN). Devuelve (times, estados).

    El observador (obs, ya corrido hacia afuera de la fachada, con altura
    h_obs en metros) y los prismas excluidos vienen resueltos en main()."""
    geoms = list(gdf.geometry.values)
    alturas = gdf["altura_m"].to_numpy()
    tree = STRtree(geoms)

    tz = zoneinfo.ZoneInfo(TZ)
    d = datetime.fromisoformat(fecha).date()
    times = pd.date_range(datetime(d.year, d.month, d.day, tzinfo=tz),
                          periods=24 * 60 // PASO_MINUTOS, freq=f"{PASO_MINUTOS}min")
    solpos = pvlib.solarposition.get_solarposition(times, LAT_CABA, LON_CABA)

    estados = []
    for az_sol, elev_sol in zip(solpos["azimuth"], solpos["apparent_elevation"]):
        if elev_sol < ELEVACION_MIN:
            estados.append("noche")
            continue
        dif = abs((az_sol - az_ventana + 180) % 360 - 180)
        if dif > 90:
            estados.append("espalda")
            continue
        idx, _ = edificio_que_tapa(obs, h_obs, az_sol, elev_sol, tree, geoms, alturas, excluidos)
        estados.append("sol" if idx is None else idx)
    return times, estados


def intervalos(times, estados):
    """Agrupa timestamps consecutivos con el mismo estado en (estado, t_ini, t_fin)."""
    out = []
    for t, e in zip(times, estados):
        if out and out[-1][0] == e:
            out[-1][2] = t
        else:
            out.append([e, t, t])
    return out


def reporte(gdf, obs, times, estados, fecha):
    paso = pd.Timedelta(minutes=PASO_MINUTOS)
    total = estados.count("sol") * PASO_MINUTOS / 60
    print(f"\n── {fecha} ── total de sol directo: {total:.1f} h")
    for e, t0, t1 in intervalos(times, estados):
        rango = f"{t0:%H:%M}–{(t1 + paso):%H:%M}"
        if e == "sol":
            print(f"  SOL     {rango}")
        elif isinstance(e, (int, np.integer)):
            fila = gdf.iloc[e]
            print(f"  SOMBRA  {rango}  ← {etiqueta_edificio(fila)}, "
                  f"altura {fila['altura_m']:.0f} m, a {fila.geometry.distance(obs):.0f} m")
    return total


def graficar(resultados, titulo, archivo):
    """Una barra horaria por fecha: dorado=sol, gris=sombra, azul=sol de espaldas."""
    colores = {"sol": "#f5b301", "espalda": "#9db8d2", "noche": "#22252b"}
    fig, axes = plt.subplots(len(resultados), 1, figsize=(12, 1.4 * len(resultados) + 1),
                             sharex=True)
    for ax, (fecha, times, estados, total) in zip(np.atleast_1d(axes), resultados):
        horas = times.hour + times.minute / 60
        cs = [colores.get(e, "#5a5f68") for e in estados]  # sombra de edificio = gris oscuro
        ax.bar(horas, 1, width=PASO_MINUTOS / 60, color=cs, align="edge")
        ax.set_yticks([])
        ax.set_ylabel(fecha[5:], rotation=0, ha="right", va="center")
        ax.set_title(f"{total:.1f} h de sol directo", loc="right", fontsize=9)
    plt.gca().set_xticks(range(0, 25, 2))
    plt.gca().set_xlim(0, 24)
    plt.gca().set_xlabel("hora local")
    fig.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=c) for c in
                        ("#f5b301", "#5a5f68", "#9db8d2", "#22252b")],
               labels=["sol directo", "sombra de edificio", "sol detrás de la ventana",
                       "noche / sol bajo"],
               loc="lower center", ncol=4, fontsize=8, frameon=False)
    fig.suptitle(titulo)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(archivo, dpi=130)
    print(f"\nGráfico guardado en {archivo}")


def mapa_debug(gdf, obs, h_obs, az_ventana, excluidos, fecha, hora, zoom=230, sufijo=""):
    """Planta de control para un timestamp: huellas coloreadas por altura,
    observador, flecha de orientación de la ventana, rayo hacia el sol y en
    rojo los prismas que bloquean ese rayo."""
    t = pd.Timestamp(f"{fecha} {hora}", tz=TZ)
    sp = pvlib.solarposition.get_solarposition(pd.DatetimeIndex([t]), LAT_CABA, LON_CABA)
    az_sol = float(sp["azimuth"].iloc[0])
    elev = float(sp["apparent_elevation"].iloc[0])

    geoms = list(gdf.geometry.values)
    alturas = gdf["altura_m"].to_numpy()
    tree = STRtree(geoms)

    dx, dy = direccion_hacia_sol(az_sol)
    rayo = LineString([(obs.x, obs.y),
                       (obs.x + LARGO_RAYO * dx, obs.y + LARGO_RAYO * dy)])
    tan_e = math.tan(math.radians(elev))
    bloqueantes = []
    for i in tree.query(rayo):
        if i in excluidos:
            continue
        corte = geoms[i].intersection(rayo)
        if not corte.is_empty and alturas[i] > h_obs + obs.distance(corte) * tan_e:
            bloqueantes.append(i)

    fig, ax = plt.subplots(figsize=(9, 9))
    gdf.plot(ax=ax, column="altura_m", cmap="cividis", edgecolor="white", linewidth=0.2,
             legend=True, legend_kwds={"label": "altura (m)", "shrink": 0.6})
    if bloqueantes:
        gdf.iloc[bloqueantes].plot(ax=ax, color="red", edgecolor="darkred")
    ax.plot(*rayo.xy, color="#f5b301", lw=2.5, solid_capstyle="round")
    vx, vy = direccion_hacia_sol(az_ventana)
    ax.annotate("", xy=(obs.x + 30 * vx, obs.y + 30 * vy), xytext=(obs.x, obs.y),
                arrowprops=dict(arrowstyle="-|>", color="#00c853", lw=2))
    ax.plot(obs.x, obs.y, "o", color="magenta", ms=8, mec="white", zorder=5)

    if elev < ELEVACION_MIN:
        estado = f"sol demasiado bajo (elev {elev:.1f}°)"
    elif abs((az_sol - az_ventana + 180) % 360 - 180) > 90:
        estado = "sol detrás de la ventana"
    elif not bloqueantes:
        estado = "SOL directo"
    else:
        smps = ", ".join(sorted({str(gdf.iloc[i]["smp"]) for i in bloqueantes}))
        estado = f"SOMBRA de SMP {smps}"
    ax.set_title(f"{fecha} {hora} — azimut sol {az_sol:.0f}°, elevación {elev:.0f}°\n{estado}",
                 fontsize=10)
    ax.set_xlim(obs.x - zoom, obs.x + zoom)
    ax.set_ylim(obs.y - zoom, obs.y + zoom)
    ax.set_aspect("equal")
    archivo = f"output/debug_{fecha}_{hora.replace(':', '')}{sufijo}.png"
    fig.tight_layout()
    fig.savefig(archivo, dpi=130)
    plt.close(fig)
    print(f"Mapa de debug: {archivo}  ({estado})")


def preparar_escena(cfg):
    """Todo lo que no depende del día: geocodificación, datasets, parcela
    objetivo, orientaciones y observadores de ambos modos.
    Devuelve (gdf, contexto) con contexto = {modo: (obs, az_ventana, excluidos)}."""
    lon, lat, calle, altura = geocodificar_usig(cfg["direccion"])
    gdf, punto = cargar_tejido(lon, lat)
    parcelas, frentes = cargar_parcelas(lon, lat)
    smp, parcela, _ = parcela_objetivo(parcelas, frentes, calle, altura, gdf, punto)
    idx_smp = list(gdf.index[gdf["smp_n"] == smp])

    bearing, eje = bearing_calle(calle, altura)
    az_contra, az_frente = normales_por_eje(punto, bearing, parcela)
    if cfg["modo"] == "frente" and cfg.get("orientacion") is not None:
        az_frente = azimut_orientacion(cfg["orientacion"])  # override manual

    # Sanity check: método del eje de calle vs. método viejo del centroide
    az_viejo = orientacion_por_centroide(parcela, punto)
    if az_viejo is not None:
        dif = abs((az_contra - az_viejo + 180) % 360 - 180)
        print(f"Contrafrente por eje de calle: {az_contra:.0f}° ({punto_cardinal(az_contra)})  |  "
              f"por centroide (método viejo): {az_viejo:.0f}° ({punto_cardinal(az_viejo)})  |  "
              f"diferencia: {dif:.0f}°")
        if dif > 30:
            print(f"WARNING: lote irregular — los métodos difieren {dif:.0f}° (>30°). "
                  f"Vale el del eje de calle; verificá la orientación deducida a ojo.")

    contexto = {}
    for modo, az_v in [("contrafrente", az_contra), ("frente", az_frente)]:
        obs, excluidos = ubicar_en_fachada(gdf, punto, parcela, idx_smp, eje, az_v,
                                           modo == "contrafrente")
        contexto[modo] = (obs, az_v, excluidos)
    return gdf, contexto


def main():
    test_azimut()
    cfg = CONFIG
    print(f"Ventana: {cfg['direccion']}, modo {cfg['modo']}")
    gdf, contexto = preparar_escena(cfg)

    hoy = datetime.now(zoneinfo.ZoneInfo(TZ)).date().isoformat()
    h_obs = altura_observador(cfg)
    print(f"Altura del observador: {h_obs:.1f} m")

    # Coherencia del posicionamiento de contrafrente: qué pared enfrenta la ventana
    obs_c, az_c, excl_c = contexto["contrafrente"]
    idx_pared, _ = chequeo_pared(gdf, obs_c, h_obs, az_c, excl_c)

    # Hoy en ambos modos: dos validaciones independientes contra lo que se ve
    hoy_cfg = None  # (times, estados, total) del modo de CONFIG, para el gráfico
    for modo, (obs, az_v, excluidos) in contexto.items():
        print(f"\n=== modo {modo}: ventana mirando al {punto_cardinal(az_v)}, {az_v:.0f}° "
              f"({len(excluidos)} prismas propios excluidos) ===")
        times, estados = horas_de_sol(gdf, obs, h_obs, az_v, hoy, excluidos)
        total = reporte(gdf, obs, times, estados, f"{hoy} (HOY, {modo})")
        if modo == cfg["modo"]:
            hoy_cfg = (times, estados, total)
        soleados = [t for t, e in zip(times, estados) if e == "sol"]
        if soleados:
            hora = f"{soleados[0]:%H:%M}"  # primer timestamp con sol del modo
        else:
            print(f"  (hoy sin sol predicho en modo {modo}; mapa al mediodía para ver por qué)")
            hora = "13:00"
        mapa_debug(gdf, obs, h_obs, az_v, excluidos, hoy, hora, sufijo=f"_{modo}")
        mapa_debug(gdf, obs, h_obs, az_v, excluidos, hoy, hora, zoom=60, sufijo=f"_{modo}_zoom")

    # Simulación estacional completa para el modo de CONFIG
    obs, az_v, excluidos = contexto[cfg["modo"]]
    resultados = []
    for fecha in cfg["fechas"]:
        times, estados = horas_de_sol(gdf, obs, h_obs, az_v, fecha, excluidos)
        total = reporte(gdf, obs, times, estados, fecha)
        resultados.append((fecha, times, estados, total))
        # Aviso fuerte si al contrafrente le tapan la mayor parte del invierno:
        # contradiría la observación de campo (el sol de la tarde entra),
        # o sea que el posicionamiento seguiría mal.
        if cfg["modo"] == "contrafrente" and "-06-" in fecha:
            de_frente = [e for e in estados if e not in ("noche", "espalda")]
            tapados = [e for e in de_frente if isinstance(e, (int, np.integer))]
            if de_frente and len(tapados) / len(de_frente) > 0.5:
                dominante = max(set(map(int, tapados)), key=tapados.count)
                print(f"  ¡¡WARNING FUERTE!! Desde la posición nueva, "
                      f"{etiqueta_edificio(gdf.iloc[dominante])} "
                      f"({gdf['altura_m'].iloc[dominante]:.0f} m) sigue tapando el "
                      f"{100 * len(tapados) / len(de_frente):.0f}% del día de invierno. "
                      f"Esto contradice la observación real (el sol de la tarde entra): "
                      f"NO confiar en estos números hasta revisar el posicionamiento "
                      f"o la altura de esa pared en el tejido.")
    if hoy not in cfg["fechas"]:
        resultados.append((hoy, *hoy_cfg))

    slug = cfg["direccion"].lower().replace(" ", "_")
    graficar(resultados,
             f"Sol directo — {cfg['direccion']}, h_obs {h_obs:.0f} m, {cfg['modo']} "
             f"({punto_cardinal(az_v)}, {az_v:.0f}°)",
             f"output/asoleamiento_{slug}.png")


def curva_por_piso(gdf, contexto, fechas, archivo):
    """La curva "cuánto sol gano por piso": horas de sol anuales (promedio de
    las cuatro fechas estacionales) barriendo la altura del observador de 3 a
    30 m, para ambas caras (frente y contrafrente)."""
    alturas = list(range(3, 31, 3))
    curvas = {modo: [] for modo in contexto}
    print("\nCurva por piso — horas de sol anuales (promedio estacional):")
    print(f"{'altura':>8}  " + "".join(f"{m:>14}" for m in contexto))
    for h in alturas:
        for modo, (obs, az_v, excluidos) in contexto.items():
            tot = 0.0
            for fecha in fechas:
                _, estados = horas_de_sol(gdf, obs, h, az_v, fecha, excluidos)
                tot += estados.count("sol") * PASO_MINUTOS / 60
            curvas[modo].append(tot / len(fechas))
        print(f"{h:>6} m  " + "".join(f"{curvas[m][-1]:>14.1f}" for m in contexto))

    fig, ax = plt.subplots(figsize=(8, 5))
    for modo, ys in curvas.items():
        _, az_v, _ = contexto[modo]
        ax.plot(alturas, ys, marker="o",
                label=f"{modo} ({punto_cardinal(az_v)}, {az_v:.0f}°)")
    ax.set_xlabel("altura del observador (m)  —  piso ≈ (altura − 1) / 3")
    ax.set_ylabel("horas de sol directo anuales (promedio estacional)")
    ax.set_xticks(alturas)
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(archivo, dpi=130)
    plt.close(fig)
    print(f"Curva guardada en {archivo}")
    return curvas


def horas_estacionales_lote(obs, h_obs, az_ventana, soles, tree, geoms_arr, alturas,
                            excl_mask):
    """Horas de sol promedio de las fechas estacionales, con el test de oclusión
    vectorizado (shapely 2 + numpy): todos los rayos del día van en UNA query al
    STRtree y las intersecciones/distancias se calculan por lotes. Es la versión
    rápida para el barrido masivo; misma convención de azimut que
    direccion_hacia_sol() — (sin(az), cos(az)) — y misma geometría que
    edificio_que_tapa()."""
    import shapely

    horas = []
    for az_arr, tan_arr in soles:
        de_frente = np.abs((az_arr - az_ventana + 180) % 360 - 180) <= 90
        az_f, tan_f = az_arr[de_frente], tan_arr[de_frente]
        if len(az_f) == 0:
            horas.append(0.0)
            continue
        rad = np.radians(az_f)
        finales = np.stack([obs.x + LARGO_RAYO * np.sin(rad),
                            obs.y + LARGO_RAYO * np.cos(rad)], axis=1)
        inicios = np.broadcast_to([obs.x, obs.y], finales.shape)
        rayos = shapely.linestrings(np.stack([inicios, finales], axis=1))

        ri, gi = tree.query(rayos, predicate="intersects")
        n_bloqueados = 0
        if len(ri):
            ok = ~excl_mask[gi]
            ri, gi = ri[ok], gi[ok]
            cortes = shapely.intersection(rayos[ri], geoms_arr[gi])
            d = shapely.distance(Point(obs.x, obs.y), cortes)
            bloquea = alturas[gi] > h_obs + d * tan_f[ri]
            n_bloqueados = len(np.unique(ri[bloquea]))
        horas.append((len(az_f) - n_bloqueados) * PASO_MINUTOS / 60)
    return float(np.mean(horas))


def barrido(direccion, radio=500.0, h_obs=6.0):
    """Todas las parcelas con frente identificable en un radio: horas de sol
    estacionales (promedio de las 4 fechas) del frente a h_obs metros.

    Posicionamiento por parcela, sin geocodificar (offline): el punto medio de
    su línea de frente (dataset frentes-parcelas), corrido 1 m hacia la calle;
    la orientación es la perpendicular a esa línea, del lado opuesto al
    centroide de la parcela. El bearing de la línea de frente es localmente
    paralelo al eje de calle (mismo método validado, otra fuente)."""
    import resource

    lon, lat, _, _ = geocodificar_usig(direccion)
    centro = a_metrico(lon, lat)
    t_carga = time.perf_counter()
    gdf, _ = cargar_tejido(lon, lat, radio=radio + LARGO_RAYO + 20)
    parcelas, frentes = cargar_parcelas(lon, lat, radio=radio + 20)
    t_carga = time.perf_counter() - t_carga

    geoms = list(gdf.geometry.values)
    geoms_arr = np.array(geoms, dtype=object)
    alturas = gdf["altura_m"].to_numpy()
    tree = STRtree(geoms)
    por_smp = defaultdict(list)
    for i, s in enumerate(gdf["smp_n"]):
        por_smp[s].append(i)
    poly_smp = {}  # smp → polígono de parcela (unión si hay varios)
    for fila in parcelas.itertuples():
        prev = poly_smp.get(fila.smp_n)
        poly_smp[fila.smp_n] = fila.geometry if prev is None else unary_union([prev, fila.geometry])

    # Posiciones solares precomputadas UNA VEZ: son idénticas para todas las
    # parcelas (misma lat/lon de referencia). Por fecha: (azimut, tan(elev)).
    tz = zoneinfo.ZoneInfo(TZ)
    soles = []
    for fecha in CONFIG["fechas"]:
        d = datetime.fromisoformat(fecha).date()
        times = pd.date_range(datetime(d.year, d.month, d.day, tzinfo=tz),
                              periods=24 * 60 // PASO_MINUTOS, freq=f"{PASO_MINUTOS}min")
        sp = pvlib.solarposition.get_solarposition(times, LAT_CABA, LON_CABA)
        ok = sp["apparent_elevation"] >= ELEVACION_MIN
        soles.append((sp["azimuth"][ok].to_numpy(),
                      np.tan(np.radians(sp["apparent_elevation"][ok].to_numpy()))))

    # De frentes-parcelas solo ATRIBUTOS (direcciones); el frente se deriva del
    # polígono de la parcela contra el callejero (frente_de_parcela).
    fr_calle = frentes[frentes["lindero"] == "CALLE"].reset_index(drop=True)
    dir_smp = {}
    for f in fr_calle.itertuples():
        if f.smp_n not in dir_smp and str(f.num_dom) not in ("N", "None", "nan"):
            dir_smp[f.smp_n] = f"{f.frente} {str(f.num_dom).replace('.', '/')}"

    bbox = tuple(gpd.GeoSeries([centro.buffer(radio + 60)], crs=CRS_METRICO)
                 .to_crs("EPSG:4326").total_bounds)
    ejes_gdf = gpd.read_file(CALLEJERO_ZIP, bbox=bbox).to_crs(CRS_METRICO)
    ejes_gdf = ejes_gdf[ejes_gdf["tipo_c"] != "FFCC"].reset_index(drop=True)
    ejes = list(ejes_gdf.geometry.values)
    nombres_ejes = list(ejes_gdf["nomoficial"].fillna(""))
    tree_ej = STRtree(ejes)
    print(f"Callejero: {len(ejes)} tramos de eje cargados.")

    saltadas = {"lejos": 0, "sin_frente": 0, "obs_ocupado": 0}
    fuentes = {"frente_derivado": 0, "eje_callejero": 0, "sobre_fachada": 0}
    resultados = []
    t0 = time.perf_counter()
    for smp, poly in poly_smp.items():
        if poly.distance(centro) > radio:
            saltadas["lejos"] += 1
            continue
        linea, eje, esquina, _ = frente_de_parcela(poly, tree_ej, ejes, nombres_ejes)
        if eje.distance(poly) > 40:
            saltadas["sin_frente"] += 1  # parcela interna real (sin calle cerca)
            continue
        if linea is not None:
            L = linea
            mid = L.interpolate(0.5, normalized=True)
            (x1, y1), (x2, y2) = L.coords[0], L.coords[-1]
            fuentes["frente_derivado"] += 1
        else:
            mid = nearest_points(poly.exterior if poly.geom_type == "Polygon"
                                 else poly.boundary, eje)[0]
            s = eje.project(nearest_points(eje, mid)[0])
            pa = eje.interpolate(max(0.0, s - 5))
            pb = eje.interpolate(min(eje.length, s + 5))
            (x1, y1), (x2, y2) = (pa.x, pa.y), (pb.x, pb.y)
            fuentes["eje_callejero"] += 1
        c = poly.centroid
        b = math.degrees(math.atan2(x2 - x1, y2 - y1)) % 360
        az_frente = (b + 90) % 360
        dx, dy = direccion_hacia_sol(az_frente)
        if (c.x - mid.x) * dx + (c.y - mid.y) * dy > 0:  # apunta al centroide → dar vuelta
            az_frente = (b - 90) % 360
            dx, dy = direccion_hacia_sol(az_frente)
        # v3: si el SMP tiene construcción, el observador va a la arista de la
        # construcción principal más cercana a la calle (fachada real, cuenta el
        # retiro de frente); los baldíos quedan sobre el borde de la parcela.
        princ = construccion_principal(geoms[i] for i in por_smp.get(smp, []))
        if princ is not None:
            cds = list(princ.exterior.coords)
            aristas = [LineString([p1, p2]) for p1, p2 in zip(cds[:-1], cds[1:])
                       if math.dist(p1, p2) >= 2.0]
            if aristas:
                ref = LineString([(x1, y1), (x2, y2)])
                arista = min(aristas, key=lambda A: A.distance(ref))
                mid = arista.interpolate(0.5, normalized=True)
                fuentes["sobre_fachada"] += 1
        obs = Point(mid.x + OFFSET_FACHADA * dx, mid.y + OFFSET_FACHADA * dy)
        excl_mask = np.zeros(len(geoms), dtype=bool)
        for i in por_smp.get(smp, []):
            if geoms[i].distance(obs) <= OFFSET_FACHADA + 0.1:
                excl_mask[i] = True
        if any(not excl_mask[i] for i in tree.query(obs, predicate="within")):
            saltadas["obs_ocupado"] += 1
            continue

        horas = horas_estacionales_lote(obs, h_obs, az_frente, soles, tree,
                                        geoms_arr, alturas, excl_mask)
        resultados.append({"smp": smp, "direccion": dir_smp.get(smp, f"SMP {smp}"),
                           "orientacion": punto_cardinal(az_frente), "horas": round(horas, 2)})
    t_loop = time.perf_counter() - t0

    df = pd.DataFrame(resultados).sort_values("horas", ascending=False)
    df.to_csv("output/barrido_frentes.csv", index=False)
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6  # bytes en macOS
    print(f"\nBarrido: {len(df)} frentes evaluados en {t_loop:.1f} s "
          f"({1000 * t_loop / max(len(df), 1):.1f} ms/parcela). "
          f"Carga de datos: {t_carga:.1f} s. Memoria pico: {rss_mb:.0f} MB.")
    print(f"Fuente del frente: {fuentes['frente_derivado']} por arista derivada de la "
          f"parcela, {fuentes['eje_callejero']} por eje del callejero; "
          f"{fuentes['sobre_fachada']} reposicionados a la fachada de la construcción (v3).")
    print(f"Saltadas: {saltadas['lejos']} fuera de radio, {saltadas['sin_frente']} sin "
          f"calle a menos de 40 m (internas), {saltadas['obs_ocupado']} con el frente obstruido.")
    print(f"Horas de sol (frente, {h_obs:.0f} m): mediana {df.horas.median():.1f}, "
          f"p10 {df.horas.quantile(0.1):.1f}, p90 {df.horas.quantile(0.9):.1f}")

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.hist(df["horas"], bins=24, color="#f5b301", edgecolor="#7a5a00")
    ax.set_xlabel(f"horas de sol directo anuales del frente a {h_obs:.0f} m (promedio estacional)")
    ax.set_ylabel("cantidad de parcelas")
    ax.set_title(f"Frentes en {radio:.0f} m alrededor de {direccion} — n={len(df)}")
    fig.tight_layout()
    fig.savefig("output/barrido_hist.png", dpi=130)
    plt.close(fig)
    print("Histograma en output/barrido_hist.png; detalle en output/barrido_frentes.csv")

    print("\n5 mejores frentes:")
    print(df.head(5).to_string(index=False))
    print("\n5 peores frentes:")
    print(df.tail(5).to_string(index=False))
    return df


ALTURAS_BARRIDO = list(range(3, 31, 3))  # m: alturas de observador precomputadas
TOL_ANGULO_FRENTE = 30.0  # grados: una arista es "frente" si es ~paralela al eje (±TOL)


def frente_de_parcela(poly, tree_ej, ejes, nombres):
    """Deriva la línea de frente desde el POLÍGONO de la parcela contra el
    callejero — sin usar la geometría de frentes-parcelas, que está rota por
    la desalineación shp↔dbf de ese dataset. El frente es la arista más larga
    del perímetro que (a) es ~paralela al eje de calle más cercano (±TOL_ANGULO_FRENTE)
    y (b) está a distancia de ese eje comparable al punto más cercano del lote.

    Esquina: hay un segundo eje con OTRO nombre a distancia comparable
    (d ≤ max(d_min + 12 m, d_min × 1.6)); la orientación se estima igual con
    el eje más cercano, pero queda flaggeada.

    Devuelve (linea_frente | None, eje, es_esquina, nombre_calle)."""
    js = list(tree_ej.query(poly.buffer(35.0)))
    if not js:
        js = [tree_ej.query_nearest(poly)[0]]
    dists = sorted((ejes[j].distance(poly), j) for j in js)
    d0, j0 = dists[0]
    eje = ejes[j0]
    esquina = any(nombres[j] != nombres[j0] and d <= max(d0 + 12.0, d0 * 1.6)
                  for d, j in dists[1:])

    def bearing180(p1, p2):
        return math.degrees(math.atan2(p2[0] - p1[0], p2[1] - p1[1])) % 180

    # bearing local del eje a la altura de la parcela
    s = eje.project(nearest_points(eje, poly.centroid)[0])
    pa = eje.interpolate(max(0.0, s - 6))
    pb = eje.interpolate(min(eje.length, s + 6))
    b_eje = bearing180((pa.x, pa.y), (pb.x, pb.y))

    polys = poly.geoms if poly.geom_type == "MultiPolygon" else [poly]
    mejor = None
    for pl in polys:
        cds = list(pl.exterior.coords)
        for a, b in zip(cds[:-1], cds[1:]):
            if math.dist(a, b) < 2.0:
                continue
            dif = abs((bearing180(a, b) - b_eje + 90) % 180 - 90)
            if dif > TOL_ANGULO_FRENTE:
                continue
            L = LineString([a, b])
            if eje.distance(L.interpolate(0.5, normalized=True)) > d0 + 8.0:
                continue  # paralela pero lejos del eje: es la del fondo, no el frente
            if mejor is None or L.length > mejor.length:
                mejor = L
    return mejor, eje, esquina, nombres[j0]

ESQUEMA_DB = """
CREATE TABLE IF NOT EXISTS parcelas(
  smp TEXT PRIMARY KEY,
  barrio TEXT, comuna TEXT, direcciones TEXT,
  az_frente REAL, az_contrafrente REAL,
  orient_frente TEXT, orient_contrafrente TEXT,
  metodo_frente TEXT,           -- frente_derivado | eje_callejero
  calle_frente TEXT,            -- nombre de la calle del eje usado para la cara frente
  flags TEXT,                   -- lista separada por comas
  estado TEXT,                  -- ok | fallada
  error TEXT);
CREATE TABLE IF NOT EXISTS horas(
  smp TEXT, cara TEXT, altura_m REAL, fecha TEXT,
  horas REAL,
  intervalos TEXT,              -- JSON [["HH:MM","HH:MM"], ...] de sol directo
  PRIMARY KEY (smp, cara, altura_m, fecha));
CREATE TABLE IF NOT EXISTS meta(clave TEXT PRIMARY KEY, valor TEXT);
"""


def _intervalos_de_grilla(posiciones):
    """Índices de la grilla diaria (paso PASO_MINUTOS) con sol → [["HH:MM","HH:MM"],...]."""
    lab = lambda i: f"{(i * PASO_MINUTOS) // 60:02d}:{(i * PASO_MINUTOS) % 60:02d}"
    corridas = []
    for p in posiciones:
        if corridas and p == corridas[-1][1] + 1:
            corridas[-1][1] = p
        else:
            corridas.append([int(p), int(p)])
    return [[lab(a), lab(b + 1)] for a, b in corridas]


def _horas_por_altura(obs, az_ventana, excl_mask, soles, tree, geoms_arr, alturas_tej):
    """Para una cara: {(altura, fecha): (horas, intervalos)} para ALTURAS_BARRIDO.
    Una sola query espacial por fecha; el barrido en altura reutiliza las mismas
    intersecciones (solo cambia la comparación de alturas)."""
    import shapely

    out = {}
    for s in soles:
        de_frente = np.abs((s["az"] - az_ventana + 180) % 360 - 180) <= 90
        az_f = s["az"][de_frente]
        tan_f = s["tan"][de_frente]
        pos_f = s["pos"][de_frente]
        n = len(az_f)
        if n == 0:
            for h in ALTURAS_BARRIDO:
                out[(h, s["fecha"])] = (0.0, [])
            continue
        rad = np.radians(az_f)
        fin = np.stack([obs.x + LARGO_RAYO * np.sin(rad),
                        obs.y + LARGO_RAYO * np.cos(rad)], axis=1)
        ini = np.broadcast_to([obs.x, obs.y], fin.shape)
        rayos = shapely.linestrings(np.stack([ini, fin], axis=1))
        ri, gi = tree.query(rayos, predicate="intersects")
        if len(ri):
            okm = ~excl_mask[gi]
            ri, gi = ri[okm], gi[okm]
        if len(ri):
            cortes = shapely.intersection(rayos[ri], geoms_arr[gi])
            d = shapely.distance(Point(obs.x, obs.y), cortes)
            tapa_base = alturas_tej[gi]
        for h in ALTURAS_BARRIDO:
            soleado = np.ones(n, dtype=bool)
            if len(ri):
                bloq = tapa_base > h + d * tan_f[ri]
                soleado[np.unique(ri[bloq])] = False
            out[(h, s["fecha"])] = (round(float(soleado.sum()) * PASO_MINUTOS / 60, 2),
                                    _intervalos_de_grilla(pos_f[soleado]))
    return out


def _obs_de_cara(poly, princ, ref, mid_frente, az_v, tree, geoms, contrafrente):
    """Posición del observador para una cara (v3 sobre fachada de la construcción
    principal; v2 sobre borde de parcela si no hay construcción). None si todo
    punto candidato cae dentro de una huella."""
    dx, dy = direccion_hacia_sol(az_v)
    libre = lambda p: len(tree.query(p, predicate="within")) == 0

    if princ is not None:
        cds = list(princ.exterior.coords)
        aristas = [LineString([a, b]) for a, b in zip(cds[:-1], cds[1:])
                   if math.dist(a, b) >= 2.0]
        if aristas:
            dref = lambda A: ref.distance(A.interpolate(0.5, normalized=True))
            aristas.sort(key=dref, reverse=contrafrente)
            emp = [A for A in aristas if abs(dref(A) - dref(aristas[0])) <= 2.0]
            if contrafrente and len(emp) > 1:
                # desempate: la ventana está donde hay vista (más aire adelante)
                def libres(A):
                    m = A.interpolate(0.5, normalized=True)
                    p = Point(m.x + dx, m.y + dy)
                    rayo = LineString([(p.x, p.y), (p.x + 60 * dx, p.y + 60 * dy)])
                    d_min = 60.0
                    for i in tree.query(rayo):
                        if geoms[i].distance(p) <= 0.15:
                            continue
                        c = geoms[i].intersection(rayo)
                        if not c.is_empty:
                            d_min = min(d_min, p.distance(c))
                    return d_min
                emp.sort(key=libres, reverse=True)
            for A in emp + [A for A in aristas if A not in emp]:
                m = A.interpolate(0.5, normalized=True)
                p = Point(m.x + OFFSET_FACHADA * dx, m.y + OFFSET_FACHADA * dy)
                if libre(p):
                    return p
            return None

    # v2 (sin construcción principal): borde de parcela
    if not contrafrente:
        p = Point(mid_frente.x + OFFSET_FACHADA * dx, mid_frente.y + OFFSET_FACHADA * dy)
        return p if libre(p) else None
    polys = poly.geoms if poly.geom_type == "MultiPolygon" else [poly]
    verts = sorted((Point(c) for pl in polys for c in dict.fromkeys(pl.exterior.coords)),
                   key=ref.distance, reverse=True)
    for v in verts:
        p = Point(v.x + OFFSET_FACHADA * dx, v.y + OFFSET_FACHADA * dy)
        if libre(p):
            return p
    return None


def precomputar_comuna(nro=6, db_path="output/comuna6.sqlite", checkpoint=200):
    """Precómputo de toda una comuna como base estática para el frontend:
    por parcela, ambas caras × alturas 3–30 m × 4 fechas estacionales, con
    intervalos. SQLite con checkpoint cada `checkpoint` parcelas (retomable:
    las parcelas ya guardadas se saltean)."""
    t_ini = time.perf_counter()
    con = sqlite3.connect(db_path)
    con.executescript(ESQUEMA_DB)
    hechas = {r[0] for r in con.execute("SELECT smp FROM parcelas")}
    if hechas:
        print(f"Retomando: {len(hechas)} parcelas ya en {db_path}.")

    print(f"Cargando datos de la Comuna {nro}…")
    parcelas = gpd.read_file(PARCELAS_ZIP, where=f"comuna = 'Comuna {nro}'").to_crs(CRS_METRICO)
    parcelas["smp_n"] = parcelas["smp"].map(smp_norm)
    print(f"Parcelas de la Comuna {nro}: {len(parcelas)} "
          f"({parcelas['smp_n'].nunique()} SMP únicos).")
    bx = parcelas.total_bounds

    def bbox4326(margen):
        caja = box(bx[0] - margen, bx[1] - margen, bx[2] + margen, bx[3] + margen)
        return tuple(gpd.GeoSeries([caja], crs=CRS_METRICO).to_crs("EPSG:4326").total_bounds)

    gdf = gpd.read_file(TEJIDO_ZIP, bbox=bbox4326(LARGO_RAYO + 20)).to_crs(CRS_METRICO)
    gdf = gdf.reset_index(drop=True)
    gdf["altura_m"] = pd.to_numeric(gdf[COL_ALTURA], errors="coerce").fillna(0.0)
    gdf["smp_n"] = gdf["smp"].map(smp_norm)
    frentes = gpd.read_file(FRENTES_ZIP, bbox=bbox4326(30)).to_crs(CRS_METRICO)
    frentes["smp_n"] = frentes["smp"].map(smp_norm)
    ejes_gdf = gpd.read_file(CALLEJERO_ZIP, bbox=bbox4326(80)).to_crs(CRS_METRICO)
    ejes_gdf = ejes_gdf[ejes_gdf["tipo_c"] != "FFCC"].reset_index(drop=True)
    print(f"Tejido: {len(gdf)} prismas; frentes: {len(frentes)}; ejes: {len(ejes_gdf)}.")

    geoms = list(gdf.geometry.values)
    geoms_arr = np.array(geoms, dtype=object)
    alturas_tej = gdf["altura_m"].to_numpy()
    tree = STRtree(geoms)
    por_smp = defaultdict(list)
    for i, s in enumerate(gdf["smp_n"]):
        por_smp[s].append(i)

    poly_smp, info_smp = {}, {}
    for f in parcelas.itertuples():
        prev = poly_smp.get(f.smp_n)
        poly_smp[f.smp_n] = f.geometry if prev is None else unary_union([prev, f.geometry])
        info_smp.setdefault(f.smp_n, (f.barrio, f.comuna))

    # De frentes-parcelas solo se usan los ATRIBUTOS (direcciones legibles);
    # la geometría de esa capa no se corresponde con sus atributos y el frente se deriva
    # del polígono de la parcela contra el callejero (frente_de_parcela).
    fr_calle = frentes[frentes["lindero"] == "CALLE"].reset_index(drop=True)
    dir_smp = {}
    for f in fr_calle.itertuples():
        if f.smp_n not in dir_smp and str(f.num_dom) not in ("N", "None", "nan"):
            dir_smp[f.smp_n] = f"{f.frente} {str(f.num_dom).replace('.', '/')}"
    ejes = list(ejes_gdf.geometry.values)
    nombres_ejes = list(ejes_gdf["nomoficial"].fillna(""))
    tree_ej = STRtree(ejes)

    # posiciones solares: una sola vez, con índice de grilla diaria para intervalos
    tz = zoneinfo.ZoneInfo(TZ)
    soles = []
    for fecha in CONFIG["fechas"]:
        d = datetime.fromisoformat(fecha).date()
        times = pd.date_range(datetime(d.year, d.month, d.day, tzinfo=tz),
                              periods=24 * 60 // PASO_MINUTOS, freq=f"{PASO_MINUTOS}min")
        sp = pvlib.solarposition.get_solarposition(times, LAT_CABA, LON_CABA)
        ok = (sp["apparent_elevation"] >= ELEVACION_MIN).to_numpy()
        soles.append({"fecha": fecha,
                      "az": sp["azimuth"].to_numpy()[ok],
                      "tan": np.tan(np.radians(sp["apparent_elevation"].to_numpy()[ok])),
                      "pos": np.nonzero(ok)[0]})

    contadores = {"ok": 0, "con_flags": 0, "falladas": 0}
    pendientes = [s for s in sorted(poly_smp) if s not in hechas]
    t0 = time.perf_counter()
    for n_hechas, smp in enumerate(pendientes, 1):
        poly = poly_smp[smp]
        flags, estado, error = [], "ok", None
        filas_horas = []
        az_frente = az_contra = None
        metodo = calle_frente = None
        try:
            linea, eje, esquina, calle_frente = frente_de_parcela(poly, tree_ej, ejes,
                                                                  nombres_ejes)
            if eje.distance(poly) > 40:
                raise RuntimeError("parcela interna: sin calle a menos de 40 m")
            if linea is not None:
                L = linea
                mid = L.interpolate(0.5, normalized=True)
                (x1, y1), (x2, y2) = L.coords[0], L.coords[-1]
                metodo = "frente_derivado"
            else:
                # sin arista ~paralela al eje: punto del perímetro más cercano,
                # bearing local del eje
                mid = nearest_points(poly.exterior if poly.geom_type == "Polygon"
                                     else poly.boundary, eje)[0]
                s_ = eje.project(nearest_points(eje, mid)[0])
                pa = eje.interpolate(max(0.0, s_ - 5))
                pb = eje.interpolate(min(eje.length, s_ + 5))
                (x1, y1), (x2, y2) = (pa.x, pa.y), (pb.x, pb.y)
                metodo = "eje_callejero"
                flags.append("matching_fallback")
            if esquina:
                flags.append("esquina")
            ref = LineString([(x1, y1), (x2, y2)])
            c = poly.centroid
            b = math.degrees(math.atan2(x2 - x1, y2 - y1)) % 360
            az_frente = (b + 90) % 360
            dx, dy = direccion_hacia_sol(az_frente)
            if (c.x - mid.x) * dx + (c.y - mid.y) * dy > 0:
                az_frente = (b - 90) % 360
            az_contra = (az_frente + 180) % 360

            # en esquinas la discrepancia con el centroide es esperable: el flag
            # esquina ya la explica, no se duplica con el genérico
            oc = orientacion_por_centroide(poly, mid)
            if (not esquina and oc is not None
                    and abs((az_contra - oc + 180) % 360 - 180) > 30):
                flags.append("discrepancia_orientacion")

            princ = construccion_principal(geoms[i] for i in por_smp.get(smp, []))
            if princ is None:
                flags.append("sin_construccion")

            for cara, az_v in [("frente", az_frente), ("contrafrente", az_contra)]:
                obs = _obs_de_cara(poly, princ, ref, mid, az_v, tree, geoms,
                                   cara == "contrafrente")
                if obs is None:
                    flags.append(f"{cara}_obstruido")
                    continue
                excl_mask = np.zeros(len(geoms), dtype=bool)
                for i in por_smp.get(smp, []):
                    if geoms[i].distance(obs) <= OFFSET_FACHADA + 0.1:
                        excl_mask[i] = True
                res = _horas_por_altura(obs, az_v, excl_mask, soles, tree,
                                        geoms_arr, alturas_tej)
                for (h, fecha), (horas, inter) in res.items():
                    filas_horas.append((smp, cara, h, fecha, horas, json.dumps(inter)))
        except Exception as e:  # una parcela rota no voltea la corrida
            estado, error = "fallada", f"{type(e).__name__}: {e}"

        # contrafrentes sin validar contra realidad hasta cerrar la discrepancia
        flags.append("contrafrente_v3_no_validado")
        barrio, comuna_txt = info_smp.get(smp, ("", ""))
        con.execute("INSERT OR REPLACE INTO parcelas VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (smp, barrio, comuna_txt, dir_smp.get(smp, ""),
                     az_frente, az_contra,
                     punto_cardinal(az_frente) if az_frente is not None else None,
                     punto_cardinal(az_contra) if az_contra is not None else None,
                     metodo, calle_frente, ",".join(flags), estado, error))
        con.executemany("INSERT OR REPLACE INTO horas VALUES (?,?,?,?,?,?)", filas_horas)
        if estado == "fallada":
            contadores["falladas"] += 1
        elif len(flags) > 1:  # más que el flag universal de contrafrente
            contadores["con_flags"] += 1
        else:
            contadores["ok"] += 1
        if n_hechas % checkpoint == 0:
            con.commit()
            ritmo = (time.perf_counter() - t0) / n_hechas
            print(f"  checkpoint: {n_hechas}/{len(pendientes)} parcelas "
                  f"({1000 * ritmo:.0f} ms/parcela, faltan ~{ritmo * (len(pendientes) - n_hechas) / 60:.0f} min)")

    con.execute("INSERT OR REPLACE INTO meta VALUES ('fechas', ?)", (json.dumps(CONFIG["fechas"]),))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('alturas_m', ?)", (json.dumps(ALTURAS_BARRIDO),))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('paso_minutos', ?)", (str(PASO_MINUTOS),))
    con.commit()

    total = time.perf_counter() - t_ini
    print(f"\nComuna {nro} precomputada en {total / 60:.1f} min "
          f"({len(pendientes)} parcelas nuevas). Corrida: "
          f"{contadores['ok']} OK, {contadores['con_flags']} con flags, "
          f"{contadores['falladas']} falladas. Base: {db_path} "
          f"({os.path.getsize(db_path) / 1e6:.0f} MB).")

    # Histograma de la comuna a 6 m, ambas caras
    df = pd.read_sql("SELECT h.smp, h.cara, AVG(h.horas) horas FROM horas h "
                     "WHERE h.altura_m = 6 GROUP BY h.smp, h.cara", con)
    fig, ax = plt.subplots(figsize=(9, 5))
    for cara, color in [("frente", "#f5b301"), ("contrafrente", "#5a7fb8")]:
        ax.hist(df[df.cara == cara]["horas"], bins=24, alpha=0.65, color=color, label=cara)
    ax.set_xlabel("horas de sol directo anuales a 6 m (promedio estacional)")
    ax.set_ylabel("cantidad de parcelas")
    ax.set_title(f"Comuna {nro} — n={df.smp.nunique()} parcelas")
    ax.legend()
    fig.tight_layout()
    fig.savefig(f"output/comuna{nro}_hist.png", dpi=130)
    plt.close(fig)
    print(f"Histograma en output/comuna{nro}_hist.png")
    con.close()


def exportar_tejido_geojson(nro=6, tolerancia=0.5, salida=None):
    """Prismas del tejido de la comuna a GeoJSON simplificado (huella + altura)
    para extrusión en deck.gl. Reporta el peso del archivo."""
    salida = salida or f"output/tejido_comuna{nro}.geojson"
    parcelas = gpd.read_file(PARCELAS_ZIP, where=f"comuna = 'Comuna {nro}'").to_crs(CRS_METRICO)
    zona = unary_union(list(parcelas.geometry)).buffer(10)
    bx = parcelas.total_bounds
    caja = box(bx[0] - 50, bx[1] - 50, bx[2] + 50, bx[3] + 50)
    bbox = tuple(gpd.GeoSeries([caja], crs=CRS_METRICO).to_crs("EPSG:4326").total_bounds)
    gdf = gpd.read_file(TEJIDO_ZIP, bbox=bbox).to_crs(CRS_METRICO).reset_index(drop=True)
    gdf["altura"] = pd.to_numeric(gdf[COL_ALTURA], errors="coerce").fillna(0.0).round(1)

    tree = STRtree(list(gdf.geometry.values))
    idx = sorted(set(tree.query(zona, predicate="intersects")))
    sel = gdf.iloc[idx][["altura", "smp", "geometry"]].copy()
    sel["smp"] = sel["smp"].map(smp_norm)  # mismo formato que la base precomputada
    sel["geometry"] = sel.geometry.simplify(tolerancia, preserve_topology=True)
    sel = sel.to_crs("EPSG:4326")
    try:
        sel.to_file(salida, driver="GeoJSON", COORDINATE_PRECISION=6, RFC7946="YES")
    except Exception:
        sel.to_file(salida, driver="GeoJSON")
    peso = os.path.getsize(salida) / 1e6
    print(f"GeoJSON: {len(sel)} prismas de la Comuna {nro} → {salida} ({peso:.1f} MB, "
          f"tolerancia {tolerancia} m).")
    return peso


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ventana"
    if cmd == "ventana":
        main()
    elif cmd == "curva":
        test_azimut()
        gdf, contexto = preparar_escena(CONFIG)
        slug = CONFIG["direccion"].lower().replace(" ", "_")
        curva_por_piso(gdf, contexto, CONFIG["fechas"], f"output/curva_pisos_{slug}.png")
    elif cmd == "barrido":
        test_azimut()
        barrido(CONFIG["direccion"], radio=500.0, h_obs=6.0)
    elif cmd == "comuna":
        test_azimut()
        precomputar_comuna(nro=6)
    elif cmd == "geojson":
        exportar_tejido_geojson(nro=6)
    else:
        sys.exit(f"comando desconocido: {cmd} (usar: ventana | curva | barrido | comuna | geojson)")
