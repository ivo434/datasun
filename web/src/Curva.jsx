import React, { useRef, useState } from "react";
import { ALTURAS, COLOR_CARA, horasAnuales, pisoDe } from "./util.js";

/* "Horas de sol según piso": dos líneas (ámbar=frente, azul=contrafrente) con
   marcador de piso ARRASTRABLE que actualiza todo el panel (spec imagen 2). */
const W = 360, H = 168, M = { t: 16, r: 20, b: 30, l: 30 };

export default function Curva({ caras, alturaSel, onAltura }) {
  const [hover, setHover] = useState(null);
  const arrastrando = useRef(false);
  const series = ["frente", "contrafrente"].map((nombre) => ({
    nombre,
    color: COLOR_CARA[nombre],
    puntos: ALTURAS.map((a) => ({ a, h: horasAnuales(caras[nombre], a) }))
      .filter((p) => p.h != null),
  })).filter((s) => s.puntos.length > 1);
  if (!series.length) return null;

  const yMax = Math.max(6, ...series.flatMap((s) => s.puntos.map((p) => p.h))) * 1.1;
  const x = (a) => M.l + ((a - 3) / 27) * (W - M.l - M.r);
  const y = (h) => H - M.b - (h / yMax) * (H - M.t - M.b);
  const camino = (pts) => pts.map((p, i) => `${i ? "L" : "M"}${x(p.a)},${y(p.h)}`).join(" ");

  function alturaDesdeEvento(e) {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const a = 3 + Math.round(((px - M.l) / (W - M.l - M.r)) * 27 / 3) * 3;
    return Math.min(30, Math.max(3, a));
  }

  const aFoco = hover ?? alturaSel;

  return (
    <figure className="curva" style={{ margin: 0 }}>
      <div className="leyenda">
        {series.map((s) => (
          <span key={s.nombre}>
            <span className="chip" style={{ background: s.color }} aria-hidden />{s.nombre}
          </span>
        ))}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} role="slider" tabIndex={0}
           aria-label="piso seleccionado" aria-valuemin={1} aria-valuemax={10}
           aria-valuenow={pisoDe(alturaSel)}
           onPointerDown={(e) => {
             arrastrando.current = true;
             e.currentTarget.setPointerCapture(e.pointerId);
             onAltura(alturaDesdeEvento(e));
           }}
           onPointerMove={(e) => {
             const a = alturaDesdeEvento(e);
             if (arrastrando.current) onAltura(a);
             else setHover(a);
           }}
           onPointerUp={() => { arrastrando.current = false; }}
           onPointerLeave={() => { arrastrando.current = false; setHover(null); }}
           onKeyDown={(e) => {
             if (e.key === "ArrowLeft") onAltura(Math.max(3, alturaSel - 3));
             if (e.key === "ArrowRight") onAltura(Math.min(30, alturaSel + 3));
           }}>
        {[0, 2.5, 5, 7.5, 10].filter((v) => v < yMax).map((v) => (
          <g key={v}>
            <line x1={M.l} x2={W - M.r} y1={y(v)} y2={y(v)} className="grilla" />
            <text x={M.l - 5} y={y(v) + 3} className="tick" textAnchor="end">{v}</text>
          </g>
        ))}
        {[3, 12, 21, 30].map((a) => (
          <text key={a} x={x(a)} y={H - 12} className="tick" textAnchor="middle">
            {pisoDe(a)}
          </text>
        ))}
        <text x={W - M.r} y={H - 12} className="tick" textAnchor="end">piso</text>
        <line x1={x(aFoco)} x2={x(aFoco)} y1={M.t} y2={H - M.b} className="foco" />
        <circle cx={x(aFoco)} cy={M.t} r="4" fill="#fff" />
        {series.map((s) => (
          <g key={s.nombre}>
            <path d={camino(s.puntos)} fill="none" stroke={s.color} strokeWidth="2" />
            {s.puntos.filter((p) => p.a === aFoco).map((p) => (
              <circle key={p.a} cx={x(p.a)} cy={y(p.h)} r="4.5" fill={s.color}
                      stroke="#0a0e14" strokeWidth="2" />
            ))}
          </g>
        ))}
      </svg>
      <div className="lectura" aria-live="polite">
        piso {pisoDe(aFoco)} ({aFoco} m): {series.map((s) => {
          const p = s.puntos.find((q) => q.a === aFoco);
          return `${s.nombre} ${p ? p.h.toFixed(1) : "—"} h`;
        }).join(" · ")}
      </div>
      <details className="tabla-alt">
        <summary>ver como tabla</summary>
        <table>
          <thead><tr><th>piso</th>{series.map((s) => <th key={s.nombre}>{s.nombre}</th>)}</tr></thead>
          <tbody>
            {ALTURAS.map((a) => (
              <tr key={a}><th>{pisoDe(a)}</th>
                {series.map((s) => {
                  const p = s.puntos.find((q) => q.a === a);
                  return <td key={s.nombre}>{p ? p.h.toFixed(1) : "—"}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
