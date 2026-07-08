import React, { useEffect, useRef, useState } from "react";
import { IcoLupa } from "./Iconos.jsx";
import { claveCalle } from "./util.js";

const USIG = "https://servicios.usig.buenosaires.gob.ar/normalizar/";

/* Autocompletado: USIG normaliza lo tipeado (API pública, CORS abierto) y acá
   se restringe a la Comuna 6 filtrando contra el índice local de calles.
   Si USIG no responde, fallback: prefijo contra los nombres del índice. */
export default function Buscador({ indice, onElegir, onMensaje }) {
  const [q, setQ] = useState("");
  const [sugerencias, setSugerencias] = useState([]);
  const [abierto, setAbierto] = useState(false);
  const timer = useRef(null);
  const omitir = useRef(false); // q seteado programáticamente al elegir: no re-buscar

  useEffect(() => {
    if (omitir.current) { omitir.current = false; return; }
    if (!indice || q.trim().length < 3) { setSugerencias([]); return; }
    clearTimeout(timer.current);
    timer.current = setTimeout(() => buscar(q), 280);
    return () => clearTimeout(timer.current);
  }, [q, indice]);

  async function buscar(texto) {
    let candidatas = [];
    try {
      const r = await fetch(`${USIG}?direccion=${encodeURIComponent(texto)}&geocodificar=false&maxOptions=8`);
      const data = await r.json();
      candidatas = (data.direccionesNormalizadas || [])
        .filter((d) => d.cod_partido === "caba")
        .map((d) => ({ calle: d.nombre_calle, altura: d.altura || null }));
    } catch {
      /* USIG caída: seguimos con el fallback local */
    }
    if (!candidatas.length) {
      const m = texto.match(/^(.*?)(\d+)?\s*$/);
      const nombre = (m[1] || "").trim();
      const numero = m[2] ? +m[2] : null;
      const clave = claveCalle(nombre);
      candidatas = Object.entries(indice)
        .filter(([k, v]) => k.includes(clave.split(" ")[0] || "") ||
                            v.nombre.toUpperCase().includes(nombre.toUpperCase()))
        .slice(0, 8)
        .map(([, v]) => ({ calle: v.nombre, altura: numero }));
    }
    // restricción a la comuna: la calle tiene que existir en el índice local
    const enComuna = candidatas.filter((c) => indice[claveCalle(c.calle)]);
    const fueraDeComuna = candidatas.length > 0 && enComuna.length === 0;
    setSugerencias(fueraDeComuna ? [{ fuera: true }] : enComuna.slice(0, 8));
    setAbierto(true);
  }

  function elegir(s) {
    setAbierto(false);
    setSugerencias([]);
    if (s.fuera) {
      onMensaje("Esa dirección está fuera de la Comuna 6 (Caballito). Por ahora la " +
                "cobertura es solo esa comuna — piloto.");
      return;
    }
    const ent = indice[claveCalle(s.calle)];
    if (!s.altura) { setQ(`${s.calle} `); return; }
    omitir.current = true;
    setQ(`${s.calle} ${s.altura}`);
    const exacto = ent.numeros[String(s.altura)];
    if (exacto) { onElegir(exacto, `${s.calle} ${s.altura}`, null); return; }
    // sin puerta exacta: la más cercana de la misma calle, con nota
    let mejor = null;
    for (const [num, smp] of Object.entries(ent.numeros)) {
      const d = Math.abs(+num - s.altura);
      if (!mejor || d < mejor.d) mejor = { d, num, smp };
    }
    if (mejor && mejor.d <= 10) {
      onElegir(mejor.smp, `${s.calle} ${s.altura}`,
               `sin puerta ${s.altura} en la base; se muestra la parcela del ${mejor.num}`);
    } else {
      onMensaje(`${s.calle} está en la comuna pero el número ${s.altura} no aparece ` +
                `en la base de frentes. Probá con un número vecino.`);
    }
  }

  return (
    <div className="buscador">
      <span className="lupa"><IcoLupa /></span>
      <input
        value={q}
        placeholder="Buscar dirección en la Comuna 6"
        onChange={(e) => setQ(e.target.value)}
        onFocus={() => sugerencias.length && setAbierto(true)}
        onKeyDown={(e) => { if (e.key === "Enter" && sugerencias[0]) elegir(sugerencias[0]); }}
        aria-label="buscar dirección"
        autoComplete="off"
      />
      {abierto && sugerencias.length > 0 && (
        <ul className="sugerencias" role="listbox">
          {sugerencias.map((s, i) => (
            <li key={i} role="option" onClick={() => elegir(s)}>
              {s.fuera ? "Fuera de la Comuna 6 — ver cobertura"
                       : `${s.calle}${s.altura ? " " + s.altura : "…"}`}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
