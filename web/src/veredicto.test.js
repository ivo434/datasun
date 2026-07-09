import assert from "node:assert/strict";
import { test } from "node:test";
import { FECHAS, UMBRALES, veredicto } from "./veredicto.js";

/* Constructor de datos sintéticos con la forma de doc.caras[cara].horas:
   porAltura = { 3: {verano: [h, iv], invierno: [...], ...}, ... } */
function datos(porAltura) {
  const out = {};
  for (const [altura, estaciones] of Object.entries(porAltura)) {
    out[String(altura)] = {};
    for (const [estacion, [h, iv]] of Object.entries(estaciones)) {
      out[String(altura)][FECHAS[estacion]] = { h, iv: iv || [] };
    }
  }
  return out;
}

const ALTURAS = [3, 6, 9, 12, 15, 18, 21, 24, 27, 30];

// mismas horas en todas las alturas y estaciones
function plano(h, iv) {
  const porAltura = {};
  for (const a of ALTURAS) {
    porAltura[a] = { verano: [h, iv], otoño: [h, iv], invierno: [h, iv], primavera: [h, iv] };
  }
  return datos(porAltura);
}

test("cueva: nada de sol a ninguna altura", () => {
  const v = veredicto(plano(0.8, ["12:00-12:48"]), 6);
  assert.equal(v.arquetipo, "cueva");
  assert.match(v.texto, /cualquier altura/);
});

test("cueva_invernal: verano decente, invierno en cero", () => {
  const porAltura = {};
  for (const a of ALTURAS) {
    porAltura[a] = {
      verano: [5.0, ["10:00-15:00"]], otoño: [3.0, ["10:00-13:00"]],
      invierno: [0.5, ["12:00-12:30"]], primavera: [3.0, ["10:00-13:00"]],
    };
  }
  const v = veredicto(datos(porAltura), 6);
  assert.equal(v.arquetipo, "cueva_invernal");
});

test("depende_del_piso: salto fuerte por encima de la altura elegida, con piso de quiebre", () => {
  const porAltura = {};
  for (const a of ALTURAS) {
    const h = a >= 12 ? 5.0 : 1.8; // quiebre en 12 m ≈ piso 4
    porAltura[a] = {
      verano: [h, ["11:00-16:00"]], otoño: [h, ["11:00-16:00"]],
      invierno: [h, ["11:00-16:00"]], primavera: [h, ["11:00-16:00"]],
    };
  }
  const v = veredicto(datos(porAltura), 6);
  assert.equal(v.arquetipo, "depende_del_piso");
  assert.match(v.texto, /del 4°/);
});

test("sol_de_maniana: ≥70% de las horas antes de las 13", () => {
  const v = veredicto(plano(3.0, ["08:00-11:00"]), 6);
  assert.equal(v.arquetipo, "sol_de_maniana");
});

test("sol_de_tarde: ≥70% de las horas después de las 13", () => {
  const v = veredicto(plano(3.0, ["14:00-17:00"]), 6);
  assert.equal(v.arquetipo, "sol_de_tarde");
});

test("luminoso: invierno alto sin sesgo horario", () => {
  // 12:00-16:30 = 4.5h: 1h mañana / 3.5h tarde = 78% tarde… usar tramos balanceados
  const v = veredicto(plano(4.5, ["09:00-11:15", "13:00-15:15"]), 6);
  assert.equal(v.arquetipo, "luminoso_todo_el_anio");
});

test("prioridad: cueva le gana a cueva_invernal", () => {
  const v = veredicto(plano(0.9, ["12:00-12:54"]), 6);
  assert.equal(v.arquetipo, "cueva");
});

test("prioridad: cueva_invernal le gana al sesgo de tarde", () => {
  const porAltura = {};
  for (const a of ALTURAS) {
    porAltura[a] = {
      verano: [5.0, ["13:00-18:00"]], otoño: [2.0, ["14:00-16:00"]],
      invierno: [0.4, ["14:00-14:24"]], primavera: [2.0, ["14:00-16:00"]],
    };
  }
  const v = veredicto(datos(porAltura), 6);
  assert.equal(v.arquetipo, "cueva_invernal");
});

test("prioridad: sesgo horario le gana a luminoso", () => {
  const v = veredicto(plano(4.5, ["13:30-18:00"]), 6);
  assert.equal(v.arquetipo, "sol_de_tarde");
});

test("sin datos → null", () => {
  assert.equal(veredicto(null, 6), null);
  assert.equal(veredicto({}, 6), null);
});

test("sin la altura pedida → null", () => {
  const v = veredicto(datos({ 6: { verano: [5, []], otoño: [5, []],
                                   invierno: [5, []], primavera: [5, []] } }), 12);
  assert.equal(v, null);
});

test("umbrales exportados y coherentes", () => {
  assert.ok(UMBRALES.cuevaAnualMax < UMBRALES.manianaTardeMinAnual + 1);
  assert.ok(UMBRALES.cuevaInvernalInvierno < UMBRALES.luminosoInvierno);
});
