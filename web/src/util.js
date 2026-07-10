// Rutas de datos con base configurable (GitHub Pages sirve bajo subdirectorio)
export const DATA = (ruta) => `${import.meta.env.BASE_URL}data/${ruta}`;

// ── Capa de datos particionada por comuna ────────────────────────────────
// Gemelo EXACTO de fnv1a() en scripts/export_frontend.py (los SMP son ASCII,
// charCode == byte). Si se toca uno hay que tocar el otro.
export function fnv1a(s) {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < s.length; i++) {
    h = Math.imul(h ^ s.charCodeAt(i), 16777619) >>> 0;
  }
  return h;
}

// SMP → comunas candidatas, vía la sección (primer segmento). OJO: las
// secciones catastrales NO respetan límites de comuna (la 6 tiene parcelas
// en la Comuna 1 y en la 4), así que es una LISTA y hay que probar packs.
export const comunasDeSmp = (smp, meta) =>
  meta?.secciones?.[smp.split("-")[0]] || [];

// Documento de una parcela: para cada comuna candidata resuelve shard → pack
// y devuelve el doc del primero que lo tenga. `cache` es un Map compartido de
// promesas de packs (una descarga por shard).
export async function cargarDoc(smp, meta, cache) {
  const candidatas = comunasDeSmp(smp, meta);
  if (!candidatas.length) throw new Error(`sección desconocida para ${smp}`);
  for (const nro of candidatas) {
    const com = meta.comunas.find((c) => c.n === nro);
    if (!com) continue;
    const shard = (fnv1a(smp) % com.S).toString(16).padStart(3, "0");
    const ruta = `c${nro}/p/${shard}.json`;
    if (!cache.has(ruta)) {
      cache.set(ruta, fetch(DATA(ruta)).then((r) => {
        if (!r.ok) throw new Error("pack no encontrado");
        return r.json();
      }).catch((e) => { cache.delete(ruta); throw e; }));
    }
    const pack = await cache.get(ruta).catch(() => null);
    if (pack && pack[smp]) return pack[smp];
  }
  throw new Error("sin datos");
}

// ¿Qué comunas toca una caja [w,s,e,n] en 4326? (para carga por viewport)
export function comunasEnCaja(caja, meta) {
  if (!meta) return [];
  const [w, s, e, n] = caja;
  return meta.comunas
    .filter(({ bbox: [bw, bs, be, bn] }) => bw <= e && be >= w && bs <= n && bn >= s)
    .map((c) => c.n);
}

// Cuadrantes (0..3) del bbox de una comuna que toca la caja del viewport.
export function cuadrantesEnCaja(caja, com) {
  const [w, s, e, n] = caja;
  const [bw, bs, be, bn] = com.bbox;
  const cx = (bw + be) / 2, cy = (bs + bn) / 2;
  const tiles = [[bw, bs, cx, cy], [cx, bs, be, cy], [bw, cy, cx, bn], [cx, cy, be, bn]];
  return tiles.flatMap(([tw, ts, te, tn], q) =>
    tw <= e && te >= w && ts <= n && tn >= s ? [q] : []);
}

// Misma normalización de nombre de calle que palabras_calle() en fase0.py:
// mayúsculas, sin puntuación ni acentos, palabras ordenadas.
// "GAONA AV." (USIG) ≡ "AV. GAONA" (frentes-parcelas).
export function claveCalle(nombre) {
  return String(nombre || "")
    .normalize("NFD")
    .replace(/\p{M}/gu, "")
    .toUpperCase()
    .replace(/[.,]/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .sort()
    .join(" ");
}

export const FECHAS = [
  { fecha: "2026-12-21", estacion: "verano", corto: "21 dic" },
  { fecha: "2026-03-21", estacion: "otoño", corto: "21 mar" },
  { fecha: "2026-06-21", estacion: "invierno", corto: "21 jun" },
  { fecha: "2026-09-21", estacion: "primavera", corto: "21 sep" },
];

// "HH:MM" → horas decimales
export const aHoras = (hhmm) => {
  const [h, m] = hhmm.split(":").map(Number);
  return h + m / 60;
};

// altura de observador (m) → piso aproximado
export const pisoDe = (altura) => Math.max(0, Math.round((altura - 1) / 3));

// azimut → punto cardinal de 8
export const punto8 = (az) => ["N", "NE", "E", "SE", "S", "SO", "O", "NO"][
  Math.round(((az % 360) + 360) % 360 / 45) % 8];

export const ALTURAS = [3, 6, 9, 12, 15, 18, 21, 24, 27, 30];

export const COLOR_CARA = { frente: "#c98500", contrafrente: "#3987e5" };

export const CONFIANZA = {
  alta: { texto: "confianza alta", color: "#199e70" },
  media: { texto: "confianza media", color: "#c98500" },
  baja: { texto: "confianza baja", color: "#e66767" },
};

// ── Rampa del heatmap ────────────────────────────────────────────────────
// Azul profundo (0 h) → teal → ámbar (máximo), interpolada en OKLab: espacio
// perceptualmente uniforme y lightness monótona (0.35 → 0.87) por construcción,
// que es el criterio de validez para rampas secuenciales.
// v4 (método emisivo): saturación plena — el color es autoiluminado y la luz
// solo matiza. Indigo profundo → cian → ámbar vivo, lightness monótona.
export const PARADAS_DEFAULT = ["#16336e", "#2ea8a0", "#ffb843"];
export const GRIS_SIN_DATO = [26, 31, 40]; // silencio, apenas sobre el suelo

const aLin = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const deLin = (c) => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055);

function hexAOklab(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => aLin(parseInt(hex.slice(i, i + 2), 16) / 255));
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
          1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
          0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s];
}

function oklabARgb([L, a, b]) {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const rgb = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
  return rgb.map((c) => Math.round(255 * Math.min(1, Math.max(0, deLin(c)))));
}

// Fábrica de rampas: 3 paradas hex → función t∈[0,1] → [r,g,b], interpolada
// en OKLab con LUT de 64 pasos. Parametrizable desde el panel de afinación.
export function crearRampa(paradas) {
  const ok = paradas.map(hexAOklab);
  const n = 64, lut = [];
  for (let i = 0; i < n; i++) {
    const t = i / (n - 1);
    const seg = t < 0.5 ? 0 : 1;
    const u = (t - seg * 0.5) * 2;
    lut.push(oklabARgb(ok[seg].map((v, k) => v + (ok[seg + 1][k] - v) * u)));
  }
  return (t) => lut[Math.min(n - 1, Math.max(0, Math.round(t * (n - 1))))];
}

export const colorRampa = crearRampa(PARADAS_DEFAULT);

// "#rrggbb" → [r,g,b]
export const hexRgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));

// Config visual por defecto. El panel ?tune=1 (solo dev) permite ajustarla
// en vivo y exportar el JSON para actualizar estos valores.
export const VISUAL_DEFAULT = {
  ambiente: 1.1,
  solIntensidad: 0.85,
  solColor: "#ffce8f",
  paradas: PARADAS_DEFAULT,   // ["#16336e", "#2ea8a0", "#ffb843"]
  nieblaDensidad: 0.9,
  nieblaAlcance: 0.58,
  nieblaTejido: 0.35,         // velo reducido sobre el tejido (la densa es del suelo)
  discoTam: 77,
  discoOp: 1,
  haloTam: 430,
  haloOp: 0.6,
  cieloIntensidad: 1.05,
  vinetaOp: 0.6,
  calleLum: 2.2,
  parqueLum: 2.45,
  viaLum: 2.2,
};

// tono base × multiplicador de luminancia → [r,g,b] clampeado
export function tonoSuelo(hex, lum) {
  return hexRgb(hex).map((c) => Math.min(255, Math.round(c * lum)));
}

// Copy de confianza por causa: tono informativo,
// nunca rojo salvo el caso sin estimación.
export function notasConfianza(doc, cara) {
  const f = doc?.flags || [];
  const notas = [];
  if (f.includes(`${cara}_obstruido`)) {
    notas.push({ tono: "serio", texto: "geometría irregular, sin estimación confiable" });
    return notas;
  }
  if (f.includes("esquina")) {
    notas.push({ tono: "suave",
      texto: `esquina: orientación estimada para la cara sobre ${doc.calle_frente || "la calle más cercana"}` });
  }
  if (f.includes("matching_fallback")) {
    notas.push({ tono: "neutro", texto: "posicionado por eje de calle" });
  }
  if (f.includes("sin_construccion")) {
    notas.push({ tono: "suave", texto: "lote sin construcción registrada (2021)" });
  }
  if (f.includes("discrepancia_orientacion")) {
    notas.push({ tono: "suave", texto: "lote irregular: orientación estimada" });
  }
  return notas;
}

// Horas anuales (promedio de las 4 fechas) de una cara a una altura dada
export function horasAnuales(cara, altura) {
  const porFecha = cara?.horas?.[String(altura)];
  if (!porFecha) return null;
  const vals = FECHAS.map((f) => porFecha[f.fecha]?.h).filter((v) => v != null);
  if (!vals.length) return null;
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}
