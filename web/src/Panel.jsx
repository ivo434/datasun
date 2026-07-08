import React from "react";
import Curva from "./Curva.jsx";
import { ICONO_ESTACION } from "./Iconos.jsx";
import { aHoras, COLOR_CARA, FECHAS, notasConfianza, pisoDe } from "./util.js";

const H_INI = 6, H_FIN = 20, CELDAS = 42; // grilla 6:00–20:00, celdas de 20 min

function BarrasEstacion({ caraDatos, altura, fechaActiva }) {
  const ejes = ["6:00", "9:00", "12:00", "15:00", "18:00", "20:00"];
  return (
    <div className="barras-estacion">
      <div className="eje">
        <span />
        <div className="horas-eje">{ejes.map((h) => <span key={h}>{h}</span>)}</div>
      </div>
      {FECHAS.map(({ fecha, estacion }) => {
        const Ico = ICONO_ESTACION[estacion];
        const iv = (caraDatos?.[String(altura)]?.[fecha]?.iv || [])
          .map((s) => s.split("-").map(aHoras));
        return (
          <div key={fecha} className={`fila-estacion ${fecha === fechaActiva ? "activa" : ""}`}>
            <span className="nombre"><Ico /> {estacion}</span>
            <div className="celdas" role="img"
                 aria-label={`${estacion}: ${iv.length ? iv.map(([a, b]) =>
                   `${Math.floor(a)}:${String(Math.round((a % 1) * 60)).padStart(2, "0")}–${Math.floor(b)}:${String(Math.round((b % 1) * 60)).padStart(2, "0")}`).join(", ") : "sin sol directo"}`}>
              {Array.from({ length: CELDAS }, (_, i) => {
                const centro = H_INI + (i + 0.5) * (H_FIN - H_INI) / CELDAS;
                const sol = iv.some(([a, b]) => centro >= a && centro <= b);
                return <span key={i} className={`celda ${sol ? "sol" : ""}`} />;
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function Panel({ sel, cargando, altura, setAltura, cara, setCara,
                                fecha, estacion, expandida, setExpandida, onCerrar }) {
  if (cargando && !sel) return <aside className="panel"><p>Cargando…</p></aside>;
  const { doc, etiqueta, nota } = sel;
  const caraDoc = doc?.caras?.[cara];
  const horasAhora = caraDoc?.horas?.[String(altura)]?.[fecha]?.h;
  const notas = doc ? notasConfianza(doc, cara) : [];
  const obstruida = notas.some((n) => n.tono === "serio");
  const causa = notas.length ? notas[0].texto : null;

  return (
    <aside className={`panel ${expandida ? "expandida" : ""}`} aria-label="resultados">
      <button className="agarre" onClick={() => setExpandida(!expandida)}
              aria-label={expandida ? "contraer panel" : "expandir panel"} />
      <button className="cerrar" onClick={onCerrar} aria-label="cerrar panel">×</button>

      <h2>{etiqueta}</h2>
      <p className="barrio">{doc ? `${doc.barrio} · SMP ${doc.smp}` : "sin datos"}</p>
      {nota && <p className="nota">{nota}</p>}

      {!doc && (
        <p className="vacio">Esta parcela no tiene datos en la base. El edificio
        igual está en el mapa.</p>
      )}
      {doc?.estado === "fallada" && (
        <p className="vacio">El motor no pudo posicionar un observador acá:
        {" "}{doc.error}. Preferimos un vacío honesto a un número inventado.</p>
      )}

      {doc?.estado === "ok" && (
        <>
          <div className="caras-toggle" role="tablist" aria-label="cara del edificio">
            {["frente", "contrafrente"].map((c) => (
              <button key={c} role="tab" aria-selected={c === cara}
                      className={c === cara ? "activa" : ""}
                      style={c === cara ? { color: COLOR_CARA[c] } : undefined}
                      onClick={() => setCara(c)}>
                <span className="punto" style={{ background: COLOR_CARA[c] }} />
                {c}{doc.caras[c]?.orient ? ` · ${doc.caras[c].orient}` : ""}
              </button>
            ))}
          </div>
          {cara === "contrafrente" && caraDoc?.en_validacion && (
            <p className="nota-validacion">estimación en validación: el posicionamiento
            de contrafrentes todavía no está verificado contra observación real</p>
          )}

          {obstruida || horasAhora == null ? (
            <div className="heroe sin-dato">
              <div className="numero">sin estimación</div>
              <div className="bajo">
                {obstruida ? "geometría irregular: no hay una posición confiable donde calcular"
                           : "sin datos para esta cara a esta altura"}
              </div>
            </div>
          ) : (
            <>
              <div className="heroe">
                <span className="numero">{horasAhora.toFixed(1)}</span>
                <span className="unidad">h</span>
                <div className="bajo">
                  de sol directo en {estacion} · {cara} · piso {pisoDe(altura)} ({altura} m)
                </div>
              </div>

              <h4 className="bloque-titulo">horas de sol directo por estación</h4>
              <BarrasEstacion caraDatos={caraDoc?.horas} altura={altura}
                              fechaActiva={fecha} />
            </>
          )}

          <h4 className="bloque-titulo">horas de sol según piso</h4>
          <Curva caras={doc.caras} alturaSel={altura} onAltura={setAltura} />

          <p className={`linea-confianza ${obstruida ? "seria" : ""}`}>
            estimación por modelo solar sobre datos públicos
            {causa ? ` · ${causa}` : ""}
          </p>
        </>
      )}
    </aside>
  );
}
