import React, { useEffect, useMemo, useState } from "react";
import Afinar from "./Afinar.jsx";
import Buscador from "./Buscador.jsx";
import { ICONO_ESTACION } from "./Iconos.jsx";
import Mapa from "./Mapa.jsx";
import Panel from "./Panel.jsx";
import { crearRampa, DATA, FECHAS, pisoDe, punto8, VISUAL_DEFAULT } from "./util.js";

const AFINANDO = import.meta.env.DEV &&
  new URLSearchParams(location.search).get("tune") === "1";

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
  // marcas en horas sobre el rango real (p5–p95); los extremos clampan
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

export default function App() {
  const [indice, setIndice] = useState(null);
  const [sol, setSol] = useState(null);
  const [heatmap, setHeatmap] = useState(null);
  const [avenidas, setAvenidas] = useState(null);
  const [suelo, setSuelo] = useState(null);
  const [sel, setSel] = useState(null);
  const [hover, setHover] = useState(null);
  const [mensaje, setMensaje] = useState(null);
  const [fecha, setFecha] = useState("2026-06-21");
  const [horaIdx, setHoraIdx] = useState(103); // 17:10: sol bajo de tarde, en cuadro
  const [altura, setAltura] = useState(6);
  const [cara, setCara] = useState("frente");
  const [cargandoSmp, setCargandoSmp] = useState(false);
  const [expandida, setExpandida] = useState(false);
  const [popover, setPopover] = useState(null); // 'ayuda' | 'ajustes' | null
  const [cfg, setCfg] = useState(VISUAL_DEFAULT); // config visual (?tune=1 la edita)
  const rampa = useMemo(() => crearRampa(cfg.paradas), [cfg.paradas]);

  useEffect(() => {
    fetch(DATA("indice_direcciones.json")).then((r) => r.json()).then(setIndice);
    fetch(DATA("sol.json")).then((r) => r.json()).then(setSol);
    fetch(DATA("heatmap_frente.json")).then((r) => r.json()).then(setHeatmap);
    fetch(DATA("avenidas.json")).then((r) => r.json()).then(setAvenidas);
    fetch(DATA("suelo_urbano.json")).then((r) => r.json()).then(setSuelo);
    if (import.meta.env.DEV) {
      window.__qaHover = (smp, x, y) => setHover(smp ? { smp, x, y } : null);
      window.__qaPick = (smp) => elegirSmp(smp, null, null);
    }
  }, []);

  // rango diurno de la estación activa: el slider va de amanecer a atardecer
  const rango = useMemo(() => {
    if (!sol) return null;
    const dia = sol[fecha].map((s, i) => [i, s[2]]).filter(([, e]) => e > 0);
    return [dia[0][0] - 1, dia.at(-1)[0] + 1];
  }, [sol, fecha]);

  useEffect(() => {
    if (rango) setHoraIdx((h) => Math.min(rango[1], Math.max(rango[0], h)));
  }, [rango]);

  const calor = useMemo(() => {
    if (!heatmap) return null;
    const col = heatmap.alturas.indexOf(altura) * heatmap.fechas.length +
                heatmap.fechas.indexOf(fecha);
    const valores = new Map(), dirs = new Map();
    heatmap.smp.forEach((s, i) => {
      valores.set(s, heatmap.horas[i][col]);
      dirs.set(s, heatmap.dir[i]);
    });
    // rampa sobre percentiles p5–p95 de la distribución real (el mapeo lineal
    // 0→máx desperdicia el espectro); los extremos se clampan
    const orden = [...valores.values()].filter((v) => v >= 0).sort((a, b) => a - b);
    const q = (p) => orden[Math.floor(p * (orden.length - 1))] ?? 0;
    const p5 = q(0.05);
    const p95 = Math.max(q(0.95), p5 + 1);
    return { valores, dirs, p5, p95 };
  }, [heatmap, altura, fecha]);

  async function elegirSmp(smp, etiqueta, nota) {
    setMensaje(null);
    setCargandoSmp(true);
    setExpandida(false);
    try {
      const r = await fetch(DATA(`smp/${smp}.json`));
      if (!r.ok) throw new Error("sin datos");
      const doc = await r.json();
      setSel({ smp, etiqueta: etiqueta || doc.direcciones || `SMP ${smp}`, nota, doc });
    } catch {
      setSel({ smp, etiqueta: etiqueta || `SMP ${smp}`, nota, doc: null });
    } finally {
      setCargandoSmp(false);
    }
  }

  const luzActual = useMemo(() => (sol ? sol[fecha][horaIdx] : null), [sol, fecha, horaIdx]);
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
    <div className={`app ${sel ? "con-panel" : ""}`}>
      <Mapa smpSel={sel?.smp || null} centro={sel?.doc?.centro} luz={luzActual}
            solDia={sol ? sol[fecha] : null} calor={calor} rampa={rampa} cfg={cfg}
            avenidas={avenidas} suelo={suelo}
            hoverSmp={hover?.smp || null} onHover={setHover}
            onPick={(smp) => elegirSmp(smp, null, null)} />

      <header className="cabecera">
        <h1>datasun</h1>
        <span className="sub">análisis de asoleamiento · comuna 6 · buenos aires</span>
        <Buscador indice={indice} onElegir={elegirSmp}
                  onMensaje={(m) => { setMensaje(m); setSel(null); }} />
        {mensaje && <div className="mensaje" role="status">{mensaje}</div>}
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
              <input type="range" min={rango[0]} max={rango[1]} value={horaIdx}
                     onChange={(e) => setHoraIdx(+e.target.value)}
                     aria-label="hora del día" />
              <div className="ticks">{ticks.map((t, i) => <span key={i}>{t}</span>)}</div>
              <div className="hora-flotante" style={{
                left: `calc(${(100 * (horaIdx - rango[0])) / (rango[1] - rango[0])}% + ${
                  8 - (16 * (horaIdx - rango[0])) / (rango[1] - rango[0])}px)`,
              }}>{luzActual[0]}</div>
            </div>
          </div>
          <Dial az={luzActual[1]} dia={luzActual[2] > 0} />
        </div>
      )}

      {/* controles móviles compactos */}
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
            <div className="lectura-hora">{luzActual[0]}</div>
          </div>
          <input type="range" min={rango[0]} max={rango[1]} value={horaIdx}
                 onChange={(e) => setHoraIdx(+e.target.value)} aria-label="hora del día" />
        </div>
      )}

      {hoverInfo && (
        <div className="tooltip" style={{ left: hoverInfo.x + 12, top: hoverInfo.y + 12 }}>
          <div className="tt-dir">{hoverInfo.dir}</div>
          <div className="tt-horas">
            {hoverInfo.horas >= 0
              ? `${(hoverInfo.horas / 10).toFixed(1)} h de sol · frente · ${altura} m`
              : "sin dato"}
          </div>
        </div>
      )}

      {(sel || cargandoSmp) && (
        <Panel sel={sel} cargando={cargandoSmp} altura={altura} setAltura={setAltura}
               cara={cara} setCara={setCara} fecha={fecha} estacion={estacionActiva}
               expandida={expandida} setExpandida={setExpandida}
               onCerrar={() => setSel(null)} />
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
          dirección para ver el detalle por cara y por piso. Piloto: Comuna 6.
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
