import {
  AmbientLight,
  DirectionalLight,
  FlyToInterpolator,
  LightingEffect,
  WebMercatorViewport,
} from "@deck.gl/core";
import { GeoJsonLayer, LineLayer, PathLayer, ScatterplotLayer, SolidPolygonLayer, TextLayer } from "@deck.gl/layers";
import DeckGL from "@deck.gl/react";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { DATA, GRIS_SIN_DATO, hexRgb, tonoSuelo } from "./util.js";

// tonos base del suelo "susurro" (multiplicados por cfg.*Lum)
const TONO_CALLE = "#141a24";
const TONO_PARQUE = "#0f1c16";
const TONO_VIA = "#0a0e14";
// offsets de elevación para evitar z-fighting con el suelo (top a z=1):
// parques 1.2, calles 1.45, vías 1.7 (la vía gana donde cruza una calzada)
const Z_PARQUE = 1.2, Z_CALLE = 1.45, Z_VIA = 1.7;

// "Home": la ciudad llena el cuadro en diagonal, con el sol arriba.
const VISTA_OPERATIVA = {
  longitude: -58.4405, latitude: -34.6175,
  zoom: 14.0, pitch: 60, bearing: -50,
};
const VISTA_INTRO = { ...VISTA_OPERATIVA, zoom: 13.2, pitch: 60, bearing: -62 };

const REDUCIDO = matchMedia("(prefers-reduced-motion: reduce)").matches;
const MOVIL = matchMedia("(max-width: 780px)").matches;
const DEBIL = MOVIL && (navigator.deviceMemory || 8) < 4;
const CON_SOMBRAS = new URLSearchParams(location.search).get("sombras") === "1";

// suelo acotado al entorno de la trama (+3 km): más allá, el canvas queda
// transparente y aparece el cielo — el sol (overlay bajo el canvas) asoma
// recién detrás de la silueta; el velo del borde funde la transición
const SUELO = [[
  [-58.4745, -34.6455], [-58.4065, -34.6455],
  [-58.4065, -34.5895], [-58.4745, -34.5895],
]];

const ZONAS = [
  { n: "PARQUE RIVADAVIA", p: [-58.4380, -34.6188] },
  { n: "PARQUE CENTENARIO", p: [-58.4358, -34.6067] },
  { n: "CID CAMPEADOR", p: [-58.4438, -34.6106] },
];

const rad = (d) => (d * Math.PI) / 180;
const AVENIDAS_PRINCIPALES = ["RIVADAVIA", "GAONA", "DIAZ VELEZ", "AVELLANEDA", "DIRECTORIO"];

// punto 3D del cielo para (azimut, elevación); R chico proyecta más abajo
function posCielo(lon, lat, az, elev, R) {
  const mLat = 111320, mLon = 111320 * Math.cos(rad(lat));
  return [lon + (R * Math.sin(rad(az))) / mLon,
          lat + (R * Math.cos(rad(az))) / mLat,
          R * Math.tan(rad(Math.max(elev, 0.5)))];
}

// semiejes aproximados de la comuna (m), para saber dónde termina el tejido
const COMUNA_RX = 2100, COMUNA_RY = 1900;
const COMUNA_CENTRO = [-58.4405, -34.6175];

/* Sol como overlay CSS, renderizado DEBAJO del canvas: el canvas de deck es
   transparente en el cielo, así que la ciudad dibujada lo ocluye — el disco
   queda siempre detrás de la silueta del tejido, nunca sobre los techos.
   Ancla: punto de suelo MÁS ALLÁ del borde del tejido en la dirección del
   azimut (borde de la comuna + 1.6 km, dentro de la franja de niebla); con
   elevación baja el disco besa esa línea, y sube 4.5 px/grado. */
function SolOverlay({ vista, luz, cfg, momento }) {
  if (!luz || luz[2] <= 0) return null;
  const [, az, elev] = luz;
  // en modo día el sol respira: grande y tenue en el horizonte, chico y
  // brillante en lo alto (momento: 0 = horizonte, 1 = mediodía)
  const esc = momento == null ? 1 : 1.45 - 0.55 * momento;
  const bri = momento == null ? 1 : 0.78 + 0.34 * momento;
  const vp = new WebMercatorViewport({
    ...vista, width: window.innerWidth, height: window.innerHeight,
  });
  const r = (COMUNA_RX * COMUNA_RY) /
    Math.hypot(COMUNA_RY * Math.sin(rad(az)), COMUNA_RX * Math.cos(rad(az)));
  // el borde mismo del tejido: la oclusión por canvas garantiza que aunque
  // quede bajo, el disco jamás se apoya sobre los techos
  const D = r + 100;
  const mLat = 111320, mLon = 111320 * Math.cos(rad(COMUNA_CENTRO[1]));
  const lejos = [COMUNA_CENTRO[0] + (D * Math.sin(rad(az))) / mLon,
                 COMUNA_CENTRO[1] + (D * Math.cos(rad(az))) / mLat];
  const [x, yBorde] = vp.project(lejos);
  const y = yBorde - 14 - elev * 4.5;
  const detras = Math.abs(((az - vista.bearing + 540) % 360) - 180) > 105;
  return (
    <div className="sol-overlay" style={{
      left: x, top: y, opacity: detras ? 0 : 1,
      "--disco": `${Math.round(cfg.discoTam * 0.62 * esc)}px`,
      "--disco-op": Math.min(1, cfg.discoOp * bri),
      "--halo": `${Math.round(cfg.haloTam * esc)}px`,
      "--halo-op": Math.min(1, cfg.haloOp * bri),
    }}>
      <div className="resplandor" />
      <div className="halo" />
      <div className="disco" />
    </div>
  );
}

/* Velo del borde del mundo: oscurecimiento radial ANCLADO A LA COMUNA (se
   proyecta su centro y escala con el zoom) que se traga la costura donde el
   tejido 3D termina y sigue la trama plana del suelo. */
function VeloBorde({ vista }) {
  const vp = new WebMercatorViewport({
    ...vista, width: window.innerWidth, height: window.innerHeight,
  });
  const [cx, cy] = vp.project([...COMUNA_CENTRO, 1]);
  const mpp = (156543.03392 * Math.cos(rad(vista.latitude))) / 2 ** vista.zoom;
  const w = Math.max((COMUNA_RX * 7) / mpp, window.innerWidth * 2.5);
  const h = Math.max(w * Math.cos(rad(vista.pitch)), window.innerHeight * 1.6);
  return (
    <div className="velo-borde" style={{ left: cx, top: cy, width: w, height: h }} />
  );
}

export default function Mapa({ smpSel, centro, compSmp, compCentro,
                               luz, solDia, calor, rampa, cfg,
                               avenidas, suelo, hoverSmp, onHover, onPick,
                               cine = false, soleado = false }) {
  const [vista, setVista] = useState(REDUCIDO || DEBIL ? VISTA_OPERATIVA : VISTA_INTRO);
  const [tejido, setTejido] = useState(null);
  const introHecha = useRef(REDUCIDO || DEBIL);
  const deckRef = useRef(null);

  useEffect(() => {
    const t = setInterval(() => {
      if (deckRef.current?.deck) { window._deck = deckRef.current.deck; clearInterval(t); }
    }, 250);
    if (import.meta.env.DEV) {
      window.__qaVista = (v) => setVista((a) => {
        const { transitionInterpolator, transitionDuration, transitionEasing,
                ...base } = a;
        return { ...base, ...v };
      });
    }
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    fetch(DATA("tejido_comuna6.geojson"))
      .then((r) => r.json())
      .then(setTejido)
      .catch((e) => console.error("tejido no cargó:", e));
  }, []);

  useEffect(() => {
    if (introHecha.current || !tejido) return;
    introHecha.current = true;
    const t = setTimeout(() => {
      setVista({
        ...VISTA_OPERATIVA,
        transitionDuration: MOVIL ? 1300 : 2500,
        transitionInterpolator: new FlyToInterpolator({ curve: 1.05 }),
        transitionEasing: (x) => 1 - Math.pow(1 - x, 3),
      });
    }, 400);
    return () => clearTimeout(t);
  }, [tejido]);

  useEffect(() => {
    if (centro) {
      setVista((v) => ({
        ...v,
        longitude: centro[0], latitude: centro[1],
        zoom: 16.6, pitch: 55,
        transitionDuration: 1500,
        transitionInterpolator: new FlyToInterpolator({ curve: 1.4 }),
      }));
    }
  }, [centro?.[0], centro?.[1]]);

  // "ver un día": la cámara baja y se acerca a la parcela; al salir, vuelve.
  // Con prefers-reduced-motion no se mueve la cámara (solo corre el slider).
  const vistaPrevia = useRef(null);
  useEffect(() => {
    if (REDUCIDO) return;
    if (cine) {
      setVista((v) => {
        // no pisar una vista previa pendiente (re-entrada durante la vuelta)
        if (!vistaPrevia.current) {
          vistaPrevia.current = { longitude: v.longitude, latitude: v.latitude,
                                  zoom: v.zoom, pitch: Math.min(v.pitch, 60),
                                  bearing: v.bearing };
        }
        return {
          ...v,
          longitude: centro?.[0] ?? v.longitude,
          latitude: centro?.[1] ?? v.latitude,
          zoom: Math.min(v.zoom + 1, 17.6), pitch: 65,
          transitionDuration: 1800,
          transitionInterpolator: new FlyToInterpolator({ curve: 1.2 }),
        };
      });
    } else if (vistaPrevia.current) {
      const prev = vistaPrevia.current;
      vistaPrevia.current = null;
      setVista((v) => ({
        ...v, ...prev,
        transitionDuration: 1200,
        transitionInterpolator: new FlyToInterpolator({ curve: 1.2 }),
      }));
    }
  }, [cine]);

  // momento del día para el modo cine: 0 = sol en el horizonte, 1 = cénit.
  // Se normaliza por la elevación máxima de la estación (capada a 35°) para
  // que el invierno también recorra la curva completa.
  const momento = useMemo(() => {
    if (!cine || !luz || !solDia) return null;
    const maxElev = Math.min(35, Math.max(...solDia.map((s) => s[2])));
    return Math.max(0, Math.min(1, luz[2] / maxElev));
  }, [cine, luz, solDia]);

  // MÉTODO EMISIVO: la ambiente alta hace que el color de dato se lea a plena
  // luminancia; la direccional queda como matiz cálido de las caras al sol.
  // En modo día la atmósfera acompaña la hora: direccional cálido-rojiza y
  // más floja cerca del horizonte, neutra y más intensa al mediodía; la
  // ambiente cae apenas en los extremos (sutil: el look sigue emisivo).
  const efectos = useMemo(() => {
    if (!luz) return [];
    const [, az, elev] = luz;
    const dia = elev > 0;
    const dir = [
      -Math.sin(rad(az)) * Math.cos(rad(elev)),
      Math.cos(rad(az)) * Math.cos(rad(elev)),
      -Math.max(Math.sin(rad(elev)), 0.03),
    ];
    const m = momento;
    const CALIDO = [255, 157, 92]; // #ff9d5c: amanecer/atardecer
    const neutro = hexRgb(cfg.solColor);
    const solar = new DirectionalLight({
      color: m == null ? neutro
        : CALIDO.map((c, i) => Math.round(c + (neutro[i] - c) * m)),
      intensity: !dia ? 0.0
        : cfg.solIntensidad * (m == null ? 1 : 0.78 + 0.45 * m),
      direction: dir,
      _shadow: CON_SOMBRAS,
    });
    const ambiente = new AmbientLight({
      color: [235, 240, 252],
      intensity: !dia ? cfg.ambiente * 0.55
        : cfg.ambiente * (m == null ? 1 : 0.82 + 0.18 * m),
    });
    const ef = new LightingEffect({ ambiente, solar });
    if (CON_SOMBRAS) ef.shadowColor = [0, 0, 0, 0.45];
    return [ef];
  }, [luz, momento, cfg.ambiente, cfg.solIntensidad, cfg.solColor]);

  // contorno de la parcela en modo día: anillos del techo de cada prisma del
  // SMP — el fill conserva el color de heatmap y la selección se lee por línea
  const contorno = useMemo(() => {
    if (!cine || !smpSel || !tejido) return null;
    const anillos = [];
    for (const f of tejido.features) {
      if (f.properties.smp !== smpSel) continue;
      const z = (f.properties.altura || 3) + 1.5;
      const polys = f.geometry.type === "Polygon"
        ? [f.geometry.coordinates] : f.geometry.coordinates;
      polys.forEach((p) => anillos.push(p[0].map(([x, y]) => [x, y, z])));
    }
    return anillos.length ? anillos : null;
  }, [cine, smpSel, tejido]);

  const lonC = Math.round(vista.longitude * 400) / 400;
  const latC = Math.round(vista.latitude * 400) / 400;

  // arco del recorrido solar (3D, punteado); labels solo en los extremos,
  // cerca del horizonte (R grande proyecta pegado a la línea de fuga)
  const arco = useMemo(() => {
    if (!solDia) return null;
    const dia = solDia.filter((s) => s[2] > 0);
    if (dia.length < 3) return null;
    const puntos = dia.filter((_, i) => i % 2 === 0)
      .map((s) => posCielo(lonC, latC, s[1], s[2], 6000));
    const extremos = [
      { p: posCielo(lonC, latC, dia[0][1], 1.5, 9000), t: "AMANECER" },
      { p: posCielo(lonC, latC, dia.at(-1)[1], 1.5, 9000), t: "ATARDECER" },
    ];
    return { puntos, extremos };
  }, [solDia, lonC, latC]);

  // datos del suelo con la z de anti z-fighting precalculada una sola vez
  const sueloZ = useMemo(() => {
    if (!suelo) return null;
    return {
      calles: suelo.calles.map((c) => ({ w: c.w, p: c.p.map(([x, y]) => [x, y, Z_CALLE]) })),
      vias: suelo.vias.map((v) => v.map(([x, y]) => [x, y, Z_VIA])),
      parques: suelo.parques.map((a) => a.map(([x, y]) => [x, y, Z_PARQUE])),
    };
  }, [suelo]);

  const avPrincipales = useMemo(() => {
    if (!avenidas) return null;
    const c = [VISTA_OPERATIVA.longitude, VISTA_OPERATIVA.latitude];
    const d2 = (p) => (p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2;
    return AVENIDAS_PRINCIPALES.flatMap((nombre) => {
      const cand = avenidas.filter((a) => a.n.includes(nombre))
        .sort((a, b) => d2(a.p) - d2(b.p));
      return cand.length ? [cand[0]] : [];
    });
  }, [avenidas]);

  const capas = useMemo(() => {
    const colorDe = (f) => {
      const smp = f.properties.smp;
      const v = calor ? calor.valores.get(smp) : undefined;
      const base = (v === undefined || v < 0)
        ? GRIS_SIN_DATO
        : rampa((v - calor.p5) / (calor.p95 - calor.p5));
      // modo día: la escena es la protagonista — todo el mundo (incluida la
      // selección) con su color de heatmap respondiendo a la luz; la parcela
      // se distingue por contorno, no por fill
      if (cine) return [base[0], base[1], base[2], 255];
      if (smp === smpSel) return [255, 232, 178, 255];
      if (smp === compSmp) return [168, 205, 255, 255]; // segundo tono: frío
      if (smp === hoverSmp) {
        return [Math.min(255, base[0] + 55), Math.min(255, base[1] + 55),
                Math.min(255, base[2] + 55), 255];
      }
      if (smpSel) return [base[0] * 0.5, base[1] * 0.5, base[2] * 0.5, 235];
      return [base[0], base[1], base[2], 255];
    };
    // pins: seleccionada (cálido) y comparada (frío)
    const pins = [
      smpSel && centro && { p: centro, c: [255, 244, 220, 235] },
      compSmp && compCentro && { p: compCentro, c: [168, 205, 255, 235] },
    ].filter(Boolean);
    const zoomBajo = vista.zoom < 14.8;
    return [
      new SolidPolygonLayer({
        id: "suelo", data: SUELO, getPolygon: (d) => d,
        extruded: true, getElevation: 1,
        getFillColor: [16, 21, 30, 255],
        material: { ambient: 0.5, diffuse: 0.4, shininess: 10, specularColor: [0, 0, 0] },
      }),
      // ── suelo "susurro": parques, calzadas y vías — sin bordes, sin luz
      // (material:false / PathLayer no iluminado): la luminancia es exacta
      sueloZ && new SolidPolygonLayer({
        id: "parques", data: sueloZ.parques,
        getPolygon: (d) => d, material: false,
        getFillColor: tonoSuelo(TONO_PARQUE, cfg.parqueLum),
        updateTriggers: { getFillColor: [cfg.parqueLum] },
      }),
      sueloZ && new PathLayer({
        id: "calles", data: sueloZ.calles,
        getPath: (d) => d.p, getWidth: (d) => d.w,
        widthUnits: "meters", widthMinPixels: 0.6,
        capRounded: true, jointRounded: true,
        // a zoom alto se aclaran un toque
        getColor: tonoSuelo(TONO_CALLE, cfg.calleLum * (vista.zoom > 15.2 ? 1.3 : 1)),
        updateTriggers: { getColor: [cfg.calleLum, vista.zoom > 15.2] },
      }),
      sueloZ && new PathLayer({
        id: "vias", data: sueloZ.vias,
        getPath: (d) => d, getWidth: 16,
        widthUnits: "meters", widthMinPixels: 0.5,
        getColor: tonoSuelo(TONO_VIA, cfg.viaLum),
        updateTriggers: { getColor: [cfg.viaLum] },
      }),
      tejido && new GeoJsonLayer({
        id: "tejido",
        data: tejido,
        extruded: true,
        getElevation: (f) => f.properties.altura,
        getFillColor: colorDe,
        updateTriggers: { getFillColor: [smpSel, hoverSmp, calor, rampa, cine] },
        material: { ambient: 0.62, diffuse: 0.5, shininess: 22, specularColor: [10, 10, 10] },
        pickable: true,
        onHover: (info) => onHover(info.object
          ? { smp: info.object.properties.smp, x: info.x, y: info.y } : null),
        onClick: (info) => info.object && onPick(info.object.properties.smp),
      }),
      // ── contorno cine: sub-trazo oscuro (legible sobre cualquier color de
      // rampa) + línea ámbar; el glow se enciende mientras hay sol directo y
      // decae al salir del intervalo — cuenta la historia sin tapar el color
      contorno && new PathLayer({
        id: "contorno-glow", data: contorno, getPath: (d) => d,
        widthUnits: "pixels", jointRounded: true, capRounded: true,
        getWidth: soleado ? 13 : 0,
        getColor: soleado ? [255, 196, 64, 110] : [255, 196, 64, 0],
        parameters: { depthCompare: "always" },
        transitions: { getWidth: 500, getColor: 500 },
        updateTriggers: { getWidth: [soleado], getColor: [soleado] },
      }),
      contorno && new PathLayer({
        id: "contorno-sombra", data: contorno, getPath: (d) => d,
        widthUnits: "pixels", jointRounded: true, capRounded: true,
        getWidth: 4, getColor: [8, 11, 17, 190],
        parameters: { depthCompare: "always" },
      }),
      contorno && new PathLayer({
        id: "contorno", data: contorno, getPath: (d) => d,
        widthUnits: "pixels", jointRounded: true, capRounded: true,
        getWidth: soleado ? 2.4 : 1.6,
        getColor: soleado ? [255, 222, 140, 255] : [255, 196, 64, 225],
        parameters: { depthCompare: "always" },
        transitions: { getWidth: 500, getColor: 500 },
        updateTriggers: { getWidth: [soleado], getColor: [soleado] },
      }),
      arco && new ScatterplotLayer({
        id: "arco-sol", data: arco.puntos, getPosition: (d) => d,
        radiusUnits: "pixels", getRadius: 1.8,
        getFillColor: [255, 214, 140, 150],
        parameters: { depthCompare: "always" },
      }),
      arco && new TextLayer({
        id: "arco-extremos", data: arco.extremos,
        getPosition: (d) => d.p, getText: (d) => d.t,
        getSize: 9.5, getColor: [205, 210, 222, 185],
        fontFamily: "Inter, sans-serif", fontWeight: 500, characterSet: "auto",
        fontSettings: { sdf: true }, outlineColor: [10, 14, 20, 200], outlineWidth: 2,
        getPixelOffset: [0, -10],
        parameters: { depthCompare: "always" },
      }),
      // referencias internas: susurro (chicas, ~0.35 de opacidad)
      zoomBajo && new LineLayer({
        id: "zonas-lineas", data: ZONAS,
        getSourcePosition: (d) => [d.p[0], d.p[1], 45],
        getTargetPosition: (d) => [d.p[0], d.p[1], 330],
        getColor: [200, 206, 218, 80], getWidth: 1,
      }),
      zoomBajo && new ScatterplotLayer({
        id: "zonas-puntos", data: ZONAS,
        getPosition: (d) => [d.p[0], d.p[1], 45],
        radiusUnits: "pixels", getRadius: 1.6, getFillColor: [220, 224, 232, 100],
      }),
      zoomBajo && new TextLayer({
        id: "zonas-textos", data: ZONAS,
        getPosition: (d) => [d.p[0], d.p[1], 375],
        getText: (d) => d.n,
        getSize: 9, getColor: [225, 229, 238, 90],
        fontFamily: "Inter, sans-serif", fontWeight: 500, characterSet: "auto",
        fontSettings: { sdf: true }, outlineColor: [10, 14, 20, 160], outlineWidth: 2,
      }),
      new TextLayer({
        id: "avenidas",
        data: (vista.zoom < 14.3 ? avPrincipales : avenidas) || [],
        getPosition: (d) => d.p, getText: (d) => d.n, getAngle: (d) => d.a,
        getSize: vista.zoom < 14.3 ? 9.5 : 10.5,
        getColor: [190, 196, 208, vista.zoom < 14.3 ? 128 : 140],
        billboard: false, fontFamily: "Inter, sans-serif", fontWeight: 500,
        characterSet: "auto", fontSettings: { sdf: true },
        outlineColor: [10, 14, 20, 190], outlineWidth: 2,
        updateTriggers: { getSize: [vista.zoom < 14.3], getColor: [vista.zoom < 14.3] },
        visible: vista.zoom < 16,
      }),
      pins.length && new LineLayer({
        id: "pin-linea", data: pins,
        getSourcePosition: (d) => [d.p[0], d.p[1], 6],
        getTargetPosition: (d) => [d.p[0], d.p[1], 165],
        getColor: (d) => d.c, getWidth: 1.4,
        parameters: { depthCompare: "always" },
      }),
      pins.length && new ScatterplotLayer({
        id: "pin-punto", data: pins,
        getPosition: (d) => [d.p[0], d.p[1], 168],
        radiusUnits: "pixels", getRadius: 4,
        getFillColor: (d) => d.c,
        parameters: { depthCompare: "always" },
      }),
    ].filter(Boolean);
  }, [tejido, smpSel, compSmp, soleado, contorno, cine, hoverSmp, calor, rampa, arco, avenidas,
      avPrincipales, sueloZ, cfg.calleLum, cfg.parqueLum, cfg.viaLum,
      centro, compCentro, vista.zoom < 14.8, vista.zoom < 14.3, vista.zoom < 16,
      vista.zoom > 15.2]);

  // Niebla en DOS niveles: la densa (nieblaDensidad) se concentra en la banda
  // alta —suelo lejano y costura del mundo—, y sobre la franja del tejido cae
  // rápido a un velo reducido (nieblaTejido) para que el heatmap conserve el
  // punch emisivo: los oscuros siguen oscuros.
  const nieblaEstilo = useMemo(() => {
    const d = cfg.nieblaDensidad, dt = cfg.nieblaTejido ?? 0.35, a = cfg.nieblaAlcance;
    const paso = (f, alpha) =>
      `rgba(10,14,20,${alpha.toFixed(3)}) ${Math.round(f * a * 100)}%`;
    return {
      background: `linear-gradient(180deg, ${[
        paso(0, d), paso(0.22, d * 0.85), paso(0.4, d * 0.4),
        paso(0.52, dt * 0.5), paso(0.72, dt * 0.22), paso(0.9, dt * 0.08),
        `transparent ${Math.round(a * 100)}%`,
      ].join(", ")})`,
    };
  }, [cfg.nieblaDensidad, cfg.nieblaTejido, cfg.nieblaAlcance]);

  // cielo del modo día: keyframes amanecer/atardecer (horizonte cálido) ↔
  // mediodía (azul más claro arriba) interpolados por `momento`; con el sol
  // a <3° (primeros/últimos minutos) el horizonte cae hacia lo oscuro
  const cieloCine = useMemo(() => {
    if (momento == null) return null;
    const lerp = (a, b, t) => a.map((c, i) => Math.round(c + (b[i] - c) * t));
    const css = (c) => `rgb(${c.join(",")})`;
    const t = momento;
    const osc = Math.min(1, Math.max(0, luz[2] / 3));
    const alto = lerp([9, 12, 26], [24, 42, 72], t);
    const medio = lerp([28, 24, 42], [17, 28, 48], t);
    const horiz = lerp([116, 58, 32], [14, 21, 34], t)
      .map((c) => Math.round(c * (0.35 + 0.65 * osc)));
    return {
      background: `linear-gradient(180deg, ${css(alto)} 0%, ${css(medio)} 36%,
        ${css(horiz)} 60%, #0a0e14 78%)`,
    };
  }, [momento, luz?.[2]]);

  return (
    <div className="mapa">
      <div className="cielo" style={{ opacity: cfg.cieloIntensidad }} />
      <div className="cielo-cine" style={{ ...(cieloCine || {}), opacity: cieloCine ? 1 : 0 }} />
      <DeckGL
        ref={deckRef}
        viewState={vista}
        onViewStateChange={(e) => setVista(e.viewState)}
        controller={{ touchRotate: true, maxPitch: 66 }}
        layers={capas}
        effects={efectos}
        onError={(e) => console.error("deck error:", e)}
        getCursor={({ isDragging }) =>
          isDragging ? "grabbing" : hoverSmp ? "pointer" : "grab"}
      />
      <SolOverlay vista={vista} luz={luz} cfg={cfg} momento={momento} />
      <VeloBorde vista={vista} />
      <div className="niebla" style={nieblaEstilo} />
      <div className="vineta" style={{ opacity: cfg.vinetaOp }} />
    </div>
  );
}
