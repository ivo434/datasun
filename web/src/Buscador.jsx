import React, { useEffect, useRef, useState } from "react";
import { IcoLupa } from "./Iconos.jsx";
import { claveCalle, DATA } from "./util.js";

const USIG = "https://servicios.usig.buenosaires.gob.ar/normalizar/";

/* Autocompletado CABA completa: USIG normaliza lo tipeado y acá se valida
   contra el índice local dirección→SMP, particionado por primera letra de la
   clave normalizada y cargado perezosamente (el buscador nunca trae el índice
   entero). Si USIG no responde, fallback: prefijo contra la letra cargada. */
export default function Buscador({ onElegir, onMensaje }) {
  const [q, setQ] = useState("");
  const [sugerencias, setSugerencias] = useState([]);
  const [abierto, setAbierto] = useState(false);
  const timer = useRef(null);
  const omitir = useRef(false); // q seteado programáticamente al elegir: no re-buscar
  const letras = useRef(new Map()); // letra → promesa del diccionario

  function letraDe(clave) {
    return /^[a-z]/i.test(clave[0] || "") ? clave[0].toLowerCase() : "0";
  }

  function diccionario(letra) {
    if (!letras.current.has(letra)) {
      letras.current.set(letra,
        fetch(DATA(`idx/${letra}.json`)).then((r) => (r.ok ? r.json() : {}))
          .catch(() => { letras.current.delete(letra); return {}; }));
    }
    return letras.current.get(letra);
  }

  async function entrada(calle) {
    const clave = claveCalle(calle);
    const dic = await diccionario(letraDe(clave));
    return dic[clave];
  }

  useEffect(() => {
    if (omitir.current) { omitir.current = false; return; }
    if (q.trim().length < 3) { setSugerencias([]); return; }
    clearTimeout(timer.current);
    timer.current = setTimeout(() => buscar(q), 280);
    return () => clearTimeout(timer.current);
  }, [q]);

  async function buscar(texto) {
    let candidatas = [];
    let hayFuera = false;
    try {
      const r = await fetch(`${USIG}?direccion=${encodeURIComponent(texto)}&geocodificar=false&maxOptions=8`);
      const data = await r.json();
      const todas = data.direccionesNormalizadas || [];
      candidatas = todas
        .filter((d) => d.cod_partido === "caba")
        .map((d) => ({ calle: d.nombre_calle, altura: d.altura || null }));
      hayFuera = todas.length > 0 && candidatas.length === 0;
    } catch {
      /* USIG caída: seguimos con el fallback local */
    }
    if (!candidatas.length && !hayFuera) {
      const m = texto.match(/^(.*?)(\d+)?\s*$/);
      const nombre = (m[1] || "").trim();
      const numero = m[2] ? +m[2] : null;
      const clave = claveCalle(nombre);
      const dic = await diccionario(letraDe(clave));
      candidatas = Object.entries(dic)
        .filter(([k, v]) => k.includes(clave.split(" ")[0] || "") ||
                            v.nombre.toUpperCase().includes(nombre.toUpperCase()))
        .slice(0, 8)
        .map(([, v]) => ({ calle: v.nombre, altura: numero }));
    }
    // la calle tiene que existir en la base de frentes local
    const enBase = [];
    for (const c of candidatas) {
      if (await entrada(c.calle)) enBase.push(c);
    }
    setSugerencias(hayFuera || (candidatas.length && !enBase.length)
      ? [{ fuera: true }] : enBase.slice(0, 8));
    setAbierto(true);
  }

  async function elegir(s) {
    setAbierto(false);
    setSugerencias([]);
    if (s.fuera) {
      onMensaje("Esa dirección no aparece en la base de frentes de CABA. " +
                "Probá con otra escritura o un número vecino.");
      return;
    }
    const ent = await entrada(s.calle);
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
      onMensaje(`${s.calle} está en la base pero el número ${s.altura} no aparece ` +
                `en los frentes. Probá con un número vecino.`);
    }
  }

  return (
    <div className="buscador">
      <span className="lupa"><IcoLupa /></span>
      <input
        value={q}
        placeholder="Buscar dirección en CABA"
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
              {s.fuera ? "Dirección sin datos en la base — ver cobertura"
                       : `${s.calle}${s.altura ? " " + s.altura : "…"}`}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
