import React, { useEffect, useMemo, useRef, useState } from "react";
import Afinar from "./Afinar.jsx";
import Buscador from "./Buscador.jsx";
import { ICONO_ESTACION } from "./Iconos.jsx";
import Mapa from "./Mapa.jsx";
import Panel from "./Panel.jsx";
import { cargarDoc, comunasEnCaja, crearRampa, cuadrantesEnCaja, DATA, FECHAS,
         pisoDe, punto8, VISUAL_DEFAULT } from "./util.js";

const AFINANDO = import.meta.env.DEV &&
  new URLSearchParams(location.search).get("tune") === "1";
const DURACION_DIA_MS = 18000;
// por debajo de este zoom manda el agregado por manzana; por encima se traen
// heatmap + tiles de tejido de las comunas del viewport (ver DECISIONES)
export const UMBRAL_DETALLE = 13.4;
const MAX_TILES = matchMedia("(max-width: 780px)").matches ? 6 : 12;
const RAMPA_DIA_S = 1.5; // ease-in/out en los extremos del día

// curva de tiempo del modo día: arranque suave (~1.5 s), crucero a velocidad
// constante, frenada suave — perfil trapezoidal de velocidad, C1 continuo
function easeDia(t) {
  const a = RAMPA_DIA_S / (DURACION_DIA_MS / 1000);
  const v = 1 / (1 - a); // velocidad de crucero que integra a 1
  if (t <= 0) return 0;
  if (t >= 1) return 1;
  if (t < a) return (v * t * t) / (2 * a);
  if (t > 1 - a) { const u = 1 - t; return 1 - (v * u * u) / (2 * a); }
  return v * (t - a / 2);
}

const aHoras = (s) => { const [h, m] = s.split(":").map(Number); return h + m / 60; };

function Dial({ az, dia }) {
  const C = 62, R = 47;
  const rad = (az * Math.PI) / 180;
  const px = C + R * Math.sin(rad), py = C - R * Math.cos(rad);
  return (
    <div className="dial" aria-label={`posición solar: azimut ${Math.round(az)}°`}>
      <svg width="124" height="124" viewBox="0 0 124 124">
        <circle cx={C} cy={C} r={R} fill="none" stroke="rgba(255,255,255,0.10)" />
        {Array.from({ length: 36 }, (_, i) => {
          const a = (i * 10 * Math.PI) / 180;
          const larga = i % 9 === 0;
          const r1 = R - (larga ? 6 : 3);
          return <line key={i}
            x1={C + r1 * Math.sin(a)} y1={C - r1 * Math.cos(a)}
            x2={C + R * Math.sin(a)} y2={C - R * Math.cos(a)}
            stroke={larga ? "rgba(255,255,255,0.4)" : "rgba(255,255,255,0.14)"}
            strokeWidth="1" />;
        })}
        {[["N", 0], ["E", 90], ["S", 180], ["O", 270]].map(([t, a]) => {
          const r2 = R + 9, ra = (a * Math.PI) / 180;
          return <text key={t} x={C + r2 * Math.sin(ra)} y={C - r2 * Math.cos(ra) + 3}
                       textAnchor="middle" fontSize="9" letterSpacing="1"
                       fill="rgba(200,206,218,0.8)">{t}</text>;
        })}
        <circle cx={px} cy={py} r="5" fill={dia ? "#ffc440" : "#3c4454"}
                stroke="#0a0e14" strokeWidth="1.5" />
      </svg>
      <div className="centro">
        <span className="rotulo">azimut</span>
        <span className="numero">{Math.round(az)}°</span>
        <span className="cardinal">{punto8(az)}</span>
      </div>
    </div>
  );
}

function LeyendaV({ p5, p95, rampa }) {
  const grad = Array.from({ length: 9 }, (_, i) =>
    `rgb(${rampa(1 - i / 8).join(",")})`).join(",");
  const n = 4;
  const marcas = Array.from({ length: n + 1 }, (_, i) => {
    const h = (p5 + (i * (p95 - p5)) / n) / 10;
    const t = h.toFixed(1).replace(/\.0$/, "");
    return i === 0 ? `≤${t}` : i === n ? `≥${t}` : t;
  });
  return (
    <div className="leyenda-v">
      <h4>horas<br />de sol</h4>
      <div className="cuerpo">
        <div className="barra-v" style={{ background: `linear-gradient(180deg, ${grad})` }} />
        <div className="escala">
          {marcas.map((m, i) => <span key={i}>{m}</span>)}
        </div>
      </div>
      <p>frente de cada parcela a la altura y estación elegidas · gris: sin dato</p>
    </div>
  );
}

// ¿hay sol directo en hhmm según los intervalos de esa cara/altura/fecha?
function enSol(caraDoc, altura, fecha, hhmm) {
  const t = aHoras(hhmm);
  return (caraDoc?.horas?.[String(altura)]?.[fecha]?.iv || []).some((tramo) => {
    const [a, b] = tramo.split("-").map(aHoras);
    return t >= a && t <= b;
  });
}

export default function App() {
  const [meta, setMeta] = useState(null);        // comunas.json: bboxes, secciones, p5/p95
  const [ciudad, setCiudad] = useState(null);    // agregado por manzana (vista ciudad)
  const [barrios, setBarrios] = useState(null);
  const [sol, setSol] = useState(null);
  const [heatmaps, setHeatmaps] = useState({});  // nro → heatmap de la comuna
  const [tejidos, setTejidos] = useState({});    // "N-qQ" → geojson del tile
  const [caja, setCaja] = useState(null);        // {caja:[w,s,e,n], zoom} del viewport
  const [avenidas, setAvenidas] = useState(null);
  const [suelo, setSuelo] = useState(null);
  const [sel, setSel] = useState(null);
  const [comp, setComp] = useState(null);        // segunda selección (comparar)
  const [modoComparar, setModoComparar] = useState(false);
  const [hover, setHover] = useState(null);
  const [mensaje, setMensaje] = useState(null);
  const [fecha, setFecha] = useState("2026-06-21");
  const [horaIdx, setHoraIdx] = useState(103);
  const [altura, setAltura] = useState(6);
  const [cara, setCara] = useState("frente");
  const [cargandoSmp, setCargandoSmp] = useState(false);
  const [expandida, setExpandida] = useState(false);
  const [popover, setPopover] = useState(null);
  const [cfg, setCfg] = useState(VISUAL_DEFAULT);
  const [dia, setDia] = useState(null);          // "ver un día": {horaPrev, pausado}
  const rampa = useMemo(() => crearRampa(cfg.paradas), [cfg.paradas]);
  const pRawRef = useRef(0); // progreso lineal (pre-easing), para pausar
  const packCache = useRef(new Map());     // shard → promesa del pack
  const pidiendo = useRef(new Set());      // heatmaps/tiles en vuelo
  const ordenTiles = useRef([]);           // LRU de tiles cargados

  useEffect(() => {
    // primera pintura: metadatos + agregado ciudad + sol + barrios; el resto
    // (susurro del suelo, etiquetas de avenidas) llega detrás sin bloquear
    fetch(DATA("comunas.json")).then((r) => r.json()).then(setMeta);
    fetch(DATA("ciudad.json")).then((r) => r.json()).then(setCiudad);
    fetch(DATA("sol.json")).then((r) => r.json()).then(setSol);
    fetch(DATA("barrios.json")).then((r) => r.json()).then(setBarrios);
    fetch(DATA("avenidas.json")).then((r) => r.json()).then(setAvenidas);
    fetch(DATA("suelo_urbano.json")).then((r) => r.json()).then(setSuelo);
    if (import.meta.env.DEV) {
      window.__qaHover = (smp, x, y) => setHover(smp ? { smp, x, y } : null);
      window.__qaPick = (smp) => elegirSmpRef.current(smp, null, null);
      window.__qaComparar = (smp) => elegirSmpRef.current(smp, null, null, "comp");
    }
  }, []);

  // ── carga por viewport: heatmap + tiles de tejido de las comunas visibles
  useEffect(() => {
    if (!meta || !caja || caja.zoom < UMBRAL_DETALLE) return;
    for (const nro of comunasEnCaja(caja.caja, meta)) {
      if (!heatmaps[nro] && !pidiendo.current.has(`hm${nro}`)) {
        pidiendo.current.add(`hm${nro}`);
        fetch(DATA(`c${nro}/heatmap.json`)).then((r) => r.json())
          .then((d) => setHeatmaps((h) => ({ ...h, [nro]: d })))
          .catch(() => pidiendo.current.delete(`hm${nro}`));
      }
      const com = meta.comunas.find((c) => c.n === nro);
      for (const q of cuadrantesEnCaja(caja.caja, com)) {
        const k = `${nro}-q${q}`;
        if (tejidos[k] || pidiendo.current.has(k)) continue;
        pidiendo.current.add(k);
        fetch(DATA(`c${nro}/tejido-q${q}.geojson`)).then((r) => r.json())
          .then((d) => setTejidos((t) => {
            const nuevo = { ...t, [k]: d };
            ordenTiles.current = [...ordenTiles.current.filter((x) => x !== k), k];
            // LRU: soltar los tiles más viejos que ya no están en el viewport
            while (ordenTiles.current.length > MAX_TILES) {
              const viejo = ordenTiles.current.shift();
              delete nuevo[viejo];
              pidiendo.current.delete(viejo);
            }
            return nuevo;
          }))
          .catch(() => pidiendo.current.delete(k));
      }
    }
  }, [meta, caja, heatmaps, tejidos]);

  const rango = useMemo(() => {
    if (!sol) return null;
    const diurno = sol[fecha].map((s, i) => [i, s[2]]).filter(([, e]) => e > 0);
    return [diurno[0][0] - 1, diurno.at(-1)[0] + 1];
  }, [sol, fecha]);

  useEffect(() => {
    if (rango) setHoraIdx((h) => Math.min(rango[1], Math.max(rango[0], h)));
  }, [rango]);

  // los percentiles de la rampa son CITYWIDE y fijos (viajan en comunas.json):
  // la escala no se corre cuando entran comunas nuevas al viewport
  const calor = useMemo(() => {
    if (!meta) return null;
    const col = meta.alturas.indexOf(altura) * meta.fechas.length +
                meta.fechas.indexOf(fecha);
    const valores = new Map(), dirs = new Map();
    for (const hm of Object.values(heatmaps)) {
      hm.smp.forEach((s, i) => {
        valores.set(s, hm.horas[i][col]);
        dirs.set(s, hm.dir[i]);
      });
    }
    return { valores, dirs, col, p5: meta.p5[col], p95: meta.p95[col] };
  }, [meta, heatmaps, altura, fecha]);

  async function elegirSmp(smp, etiqueta, nota, destino = "sel") {
    setMensaje(null);
    setCargandoSmp(true);
    setExpandida(false);
    try {
      const doc = await cargarDoc(smp, meta, packCache.current);
      const valor = { smp, etiqueta: etiqueta || doc.direcciones || `SMP ${smp}`, nota, doc };
      destino === "comp" ? setComp(valor) : setSel(valor);
    } catch {
      const valor = { smp, etiqueta: etiqueta || `SMP ${smp}`, nota, doc: null };
      destino === "comp" ? setComp(valor) : setSel(valor);
    } finally {
      setCargandoSmp(false);
      setModoComparar(false);
    }
  }
  const elegirSmpRef = useRef(elegirSmp);
  elegirSmpRef.current = elegirSmp;

  function alElegir(smp, etiqueta, nota) {
    // el flujo de una parcela es sagrado: solo va a comparación si el modo
    // "comparar con…" está armado explícitamente
    elegirSmp(smp, etiqueta, nota, modoComparar && sel ? "comp" : "sel");
  }

  // ── "ver un día": animar el slider de amanecer a atardecer ─────────────
  // El progreso se calcula por reloj de pared (no acumulando pasos): la
  // duración es de 15 s aunque el navegador throttlee los timers.
  useEffect(() => {
    if (!dia || dia.pausado || !rango) return;
    const t0 = Date.now() - (dia.progresoMs || 0);
    let fin = false;
    const timer = setInterval(() => {
      const pRaw = Math.min(1, (Date.now() - t0) / DURACION_DIA_MS);
      pRawRef.current = pRaw;
      const p = easeDia(pRaw);
      setHoraIdx(rango[0] + p * (rango[1] - rango[0]));
      if (pRaw >= 1 && !fin) {
        fin = true;
        clearInterval(timer);
        setTimeout(() => terminarDia(), 900);
      }
    }, 50);
    return () => clearInterval(timer);
  }, [dia?.pausado, dia?.marca, rango]);

  function verUnDia() {
    if (!sel || dia) return;
    pRawRef.current = 0;
    setDia({ horaPrev: horaIdx, pausado: false, marca: Date.now() });
    setHoraIdx(rango[0]);
  }
  function terminarDia() {
    setDia((d) => {
      if (d) setHoraIdx(d.horaPrev);
      return null;
    });
  }

  // en modo día, cualquier tecla o click fuera de los controles también sale
  useEffect(() => {
    if (!dia) return;
    const tecla = () => terminarDia();
    const click = (e) => {
      if (!e.target.closest?.(".cine-hud .controles")) terminarDia();
    };
    window.addEventListener("keydown", tecla);
    window.addEventListener("pointerdown", click);
    return () => {
      window.removeEventListener("keydown", tecla);
      window.removeEventListener("pointerdown", click);
    };
  }, [!!dia]);

  const horaEntera = sol ? sol[fecha][Math.round(horaIdx)] : null;
  // ¿la parcela recibe sol directo ahora? (enciende el contorno en modo día)
  const soleado = !!dia && !!sel?.doc &&
    enSol(sel.doc.caras?.[cara], altura, fecha, horaEntera?.[0] || "00:00");

  // intervalos de sol de la parcela como segmentos [left%, width%] de la
  // barra de progreso del día
  const tramosSol = useMemo(() => {
    if (!dia || !sel?.doc || !sol || !rango) return [];
    const iv = sel.doc.caras?.[cara]?.horas?.[String(altura)]?.[fecha]?.iv || [];
    const h0 = aHoras(sol[fecha][rango[0]][0]);
    const h1 = aHoras(sol[fecha][rango[1]][0]);
    return iv.map((tramo) => {
      const [a, b] = tramo.split("-").map(aHoras);
      return [((a - h0) / (h1 - h0)) * 100, ((b - a) / (h1 - h0)) * 100];
    });
  }, [!!dia, sel, cara, altura, fecha, sol, rango]);

  const luzActual = horaEntera;
  const estacionActiva = FECHAS.find((f) => f.fecha === fecha)?.estacion;
  const hoverInfo = hover && calor ? {
    ...hover,
    dir: calor.dirs.get(hover.smp) || `SMP ${hover.smp}`,
    horas: calor.valores.get(hover.smp),
  } : null;

  const ticks = useMemo(() => {
    if (!sol || !rango) return [];
    const n = 5;
    return Array.from({ length: n }, (_, i) =>
      sol[fecha][Math.round(rango[0] + (i * (rango[1] - rango[0])) / (n - 1))][0]);
  }, [sol, fecha, rango]);

  return (
    <div className={`app ${sel ? "con-panel" : ""} ${dia ? "cine" : ""}`}>
      <Mapa smpSel={sel?.smp || null} centro={sel?.doc?.centro}
            compSmp={comp?.smp || null} compCentro={comp?.doc?.centro}
            luz={luzActual} solDia={sol ? sol[fecha] : null}
            calor={calor} rampa={rampa} cfg={cfg}
            avenidas={avenidas} suelo={suelo}
            meta={meta} ciudad={ciudad} barrios={barrios}
            tejidos={tejidos} onCaja={setCaja}
            hoverSmp={hover?.smp || null} onHover={setHover}
            onPick={(smp) => alElegir(smp, null, null)}
            cine={!!dia} soleado={soleado} />

      <header className="cabecera">
        <h1>datasun</h1>
        <span className="sub">análisis de asoleamiento · buenos aires</span>
        <Buscador onElegir={alElegir}
                  onMensaje={(m) => { setMensaje(m); }} />
        {mensaje && <div className="mensaje" role="status">{mensaje}</div>}
        {modoComparar && (
          <div className="mensaje" role="status">
            Elegí la otra parcela: tocá un edificio o buscá una dirección.
            <button className="enlace" onClick={() => setModoComparar(false)}>cancelar</button>
          </div>
        )}
      </header>

      {calor && !sel && <LeyendaV p5={calor.p5} p95={calor.p95} rampa={rampa} />}
      {AFINANDO && <Afinar cfg={cfg} setCfg={setCfg} />}

      {sol && rango && (
        <div className="instrumento" aria-label="estación, hora y altura">
          <div className="columna">
            <div className="fila-altura">
              <label htmlFor="altura-global">
                altura <strong>{altura} m</strong> · piso {pisoDe(altura)}
              </label>
              <input id="altura-global" type="range" min={3} max={30} step={3}
                     value={altura} onChange={(e) => setAltura(+e.target.value)} />
            </div>
            <div className="estaciones">
              {FECHAS.map(({ fecha: f, estacion }) => {
                const Ico = ICONO_ESTACION[estacion];
                return (
                  <button key={f} className={f === fecha ? "activa" : ""}
                          onClick={() => setFecha(f)}>
                    <Ico /> {estacion}
                  </button>
                );
              })}
            </div>
            <div className="pista-hora">
              <input type="range" min={rango[0]} max={rango[1]} value={Math.round(horaIdx)}
                     onChange={(e) => setHoraIdx(+e.target.value)}
                     aria-label="hora del día" />
              <div className="ticks">{ticks.map((t, i) => <span key={i}>{t}</span>)}</div>
              <div className="hora-flotante" style={{
                left: `calc(${(100 * (Math.round(horaIdx) - rango[0])) / (rango[1] - rango[0])}% + ${
                  8 - (16 * (Math.round(horaIdx) - rango[0])) / (rango[1] - rango[0])}px)`,
              }}>{luzActual?.[0]}</div>
            </div>
          </div>
          <Dial az={luzActual?.[1] ?? 0} dia={(luzActual?.[2] ?? 0) > 0} />
        </div>
      )}

      {sol && rango && (
        <div className="controles-movil" aria-label="controles">
          <div className="fila-1">
            <select value={fecha} onChange={(e) => setFecha(e.target.value)}
                    aria-label="estación">
              {FECHAS.map(({ fecha: f, estacion }) => (
                <option key={f} value={f}>{estacion}</option>
              ))}
            </select>
            <div className="fila-altura" style={{ flex: 1 }}>
              <label>{altura} m</label>
              <input type="range" min={3} max={30} step={3} value={altura}
                     onChange={(e) => setAltura(+e.target.value)} aria-label="altura" />
            </div>
            <div className="lectura-hora">{luzActual?.[0]}</div>
          </div>
          <input type="range" min={rango[0]} max={rango[1]} value={Math.round(horaIdx)}
                 onChange={(e) => setHoraIdx(+e.target.value)} aria-label="hora del día" />
        </div>
      )}

      {dia && rango && (
        <div className="cine-hud">
          <div className="cine-hora" aria-live="off">
            {luzActual?.[0]}
            <span className="cine-estacion">{estacionActiva}</span>
          </div>
          <div className="cine-progreso" aria-hidden="true">
            {tramosSol.map(([izq, ancho], i) => (
              <span key={i} className="tramo-sol"
                    style={{ left: `${izq}%`, width: `${ancho}%` }} />
            ))}
            <span className="cursor" style={{
              width: `${(100 * (horaIdx - rango[0])) / (rango[1] - rango[0])}%`,
            }} />
          </div>
          <div className="controles">
            <button aria-label={dia.pausado ? "seguir" : "pausa"}
                    onClick={() => setDia((d) => ({
                      ...d, pausado: !d.pausado,
                      progresoMs: pRawRef.current * DURACION_DIA_MS,
                    }))}>
              {dia.pausado ? "▶" : "⏸"}
            </button>
            <button aria-label="salir" onClick={terminarDia}>✕</button>
          </div>
        </div>
      )}

      {hoverInfo && !dia && (
        <div className="tooltip" style={{ left: hoverInfo.x + 12, top: hoverInfo.y + 12 }}>
          <div className="tt-dir">{hoverInfo.dir}</div>
          <div className="tt-horas">
            {hoverInfo.horas >= 0
              ? `${(hoverInfo.horas / 10).toFixed(1)} h de sol · frente · ${altura} m`
              : "sin dato"}
          </div>
        </div>
      )}

      {/* en modo día el panel queda montado y se desvanece por CSS */}
      {(sel || cargandoSmp) && (
        <Panel sel={sel} comp={comp} cargando={cargandoSmp}
               altura={altura} setAltura={setAltura}
               cara={cara} setCara={setCara} fecha={fecha} estacion={estacionActiva}
               expandida={expandida} setExpandida={setExpandida}
               onCerrar={() => { setSel(comp); setComp(null); if (!comp) setModoComparar(false); }}
               onCerrarComp={() => setComp(null)}
               onComparar={() => setModoComparar(true)}
               onVerUnDia={verUnDia} />
      )}

      <button className="boton-esquina ayuda" onClick={() => setPopover(popover === "ayuda" ? null : "ayuda")}
              aria-label="ayuda">?</button>
      <button className="boton-esquina ajustes" onClick={() => setPopover(popover === "ajustes" ? null : "ajustes")}
              aria-label="ajustes">⚙</button>
      {popover === "ayuda" && (
        <div className="popover ayuda">
          <h5>qué estás viendo</h5>
          El color de cada edificio son las horas de sol directo que recibe el frente
          de su parcela, calculadas contra el tejido real (huellas + alturas, BA Data
          ~2021) para la altura y estación elegidas. Tocá un edificio o buscá una
          dirección para ver el detalle por cara y por piso. Cobertura: CABA completa.
        </div>
      )}
      {popover === "ajustes" && (
        <div className="popover ajustes">
          <h5>ajustes</h5>
          <label>
            <input type="checkbox"
                   defaultChecked={new URLSearchParams(location.search).get("sombras") === "1"}
                   onChange={(e) => {
                     location.search = e.target.checked ? "?sombras=1" : "";
                   }} />
            sombras proyectadas (experimental, puede bajar los fps)
          </label>
          <p style={{ margin: "8px 0 0" }}>datos: BA Data · sin cookies, sin analytics</p>
        </div>
      )}
    </div>
  );
}
