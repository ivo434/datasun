import React, { useState } from "react";

/* Panel de afinación visual (?tune=1, solo dev): sliders en vivo sobre la
   config; "copiar config" exporta el JSON para hardcodear en VISUAL_DEFAULT. */

const RANGOS = [
  ["ambiente", "ambiente", 0, 2, 0.05],
  ["solIntensidad", "direccional", 0, 4, 0.05],
  ["nieblaDensidad", "niebla · densidad", 0, 1, 0.05],
  ["nieblaTejido", "niebla · sobre tejido", 0, 1, 0.05],
  ["nieblaAlcance", "niebla · alcance", 0.3, 0.9, 0.02],
  ["discoTam", "sol · disco px", 8, 80, 1],
  ["discoOp", "sol · disco op", 0, 1, 0.05],
  ["haloTam", "sol · halo px", 100, 800, 10],
  ["haloOp", "sol · halo op", 0, 1, 0.05],
  ["cieloIntensidad", "cielo", 0, 1.5, 0.05],
  ["vinetaOp", "viñeta", 0, 1, 0.05],
  ["calleLum", "suelo · calles", 0, 3, 0.05],
  ["parqueLum", "suelo · parques", 0, 3, 0.05],
  ["viaLum", "suelo · vías", 0, 3, 0.05],
];

export default function Afinar({ cfg, setCfg }) {
  const [copiado, setCopiado] = useState(false);
  const num = (k) => (e) => setCfg({ ...cfg, [k]: +e.target.value });
  const col = (k) => (e) => setCfg({ ...cfg, [k]: e.target.value });
  const parada = (i) => (e) => {
    const p = [...cfg.paradas];
    p[i] = e.target.value;
    setCfg({ ...cfg, paradas: p });
  };
  const copiar = async () => {
    await navigator.clipboard.writeText(JSON.stringify(cfg, null, 2));
    setCopiado(true);
    setTimeout(() => setCopiado(false), 1500);
  };

  return (
    <div className="afinar" aria-label="afinación visual">
      <h5>afinación</h5>
      {RANGOS.map(([k, rotulo, min, max, paso]) => (
        <label key={k}>
          <span>{rotulo} <em>{cfg[k]}</em></span>
          <input type="range" min={min} max={max} step={paso}
                 value={cfg[k]} onChange={num(k)} />
        </label>
      ))}
      <label className="color">
        <span>direccional · color</span>
        <input type="color" value={cfg.solColor} onChange={col("solColor")} />
      </label>
      <div className="paradas">
        <span>rampa</span>
        {cfg.paradas.map((p, i) => (
          <input key={i} type="color" value={p} onChange={parada(i)} />
        ))}
      </div>
      <button onClick={copiar}>{copiado ? "¡copiada!" : "copiar config"}</button>
    </div>
  );
}
