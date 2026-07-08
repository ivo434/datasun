import React from "react";

const base = { width: 14, height: 14, viewBox: "0 0 24 24", fill: "none",
               stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round" };

export const IcoSol = () => (
  <svg {...base} aria-hidden>
    <circle cx="12" cy="12" r="4.2" />
    {[0, 45, 90, 135, 180, 225, 270, 315].map((a) => (
      <line key={a} x1={12 + 7 * Math.sin(a * Math.PI / 180)} y1={12 - 7 * Math.cos(a * Math.PI / 180)}
            x2={12 + 9.5 * Math.sin(a * Math.PI / 180)} y2={12 - 9.5 * Math.cos(a * Math.PI / 180)} />
    ))}
  </svg>
);

export const IcoHoja = () => (
  <svg {...base} aria-hidden>
    <path d="M5 19C5 10 10 5 19 5c0 9-5 14-14 14z" />
    <path d="M5 19c4-4 8-8 11-11" />
  </svg>
);

export const IcoCopo = () => (
  <svg {...base} aria-hidden>
    {[0, 60, 120].map((a) => (
      <line key={a} x1={12 + 8.5 * Math.sin(a * Math.PI / 180)} y1={12 - 8.5 * Math.cos(a * Math.PI / 180)}
            x2={12 - 8.5 * Math.sin(a * Math.PI / 180)} y2={12 + 8.5 * Math.cos(a * Math.PI / 180)} />
    ))}
    <circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" />
  </svg>
);

export const IcoFlor = () => (
  <svg {...base} aria-hidden>
    {[0, 72, 144, 216, 288].map((a) => (
      <circle key={a} cx={12 + 5.4 * Math.sin(a * Math.PI / 180)}
              cy={12 - 5.4 * Math.cos(a * Math.PI / 180)} r="2.6" />
    ))}
    <circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none" />
  </svg>
);

export const IcoLupa = () => (
  <svg {...base} width="16" height="16" aria-hidden>
    <circle cx="10.5" cy="10.5" r="6.5" />
    <line x1="15.5" y1="15.5" x2="20.5" y2="20.5" />
  </svg>
);

export const ICONO_ESTACION = {
  verano: IcoSol, otoño: IcoHoja, invierno: IcoCopo, primavera: IcoFlor,
};
