/* Veredicto: una oración que sintetiza el carácter solar de una parcela.
   Módulo puro (sin dependencias del bundler) para poder testearlo con node.

   Entrada: horasPorAltura = { "3": { "2026-12-21": { h, iv: ["HH:MM-HH:MM"] },
   ... }, ... } (la estructura de doc.caras[cara].horas) y la altura elegida.
   Salida: { arquetipo, texto, corto } o null (sin veredicto).

   Umbrales calibrados contra la distribución real de la Comuna 6 (13.4k
   parcelas): ver DECISIONES.md — no son constantes mágicas. */

export const FECHAS = {
  verano: "2026-12-21",
  otoño: "2026-03-21",
  invierno: "2026-06-21",
  primavera: "2026-09-21",
};

export const UMBRALES = {
  cuevaAnualMax: 1.5,        // 0.7 % de los frentes de la comuna
  cuevaInvernalInvierno: 1.0, // p25 invierno = 0.5 h; ≤1 h ≈ 28 %
  cuevaInvernalVerano: 3.0,   // el 95 % supera 3 h en verano: filtra falsos positivos
  dependePisoDif: 1.5,        // p90 frente = 1.2 h / contrafrente = 1.8 h → ~4 % / ~14 %
  manianaTardeFrac: 0.7,      // ≥70 % de las horas de un lado de las 13:00 (~38 % / ~27 %)
  manianaTardeMinAnual: 1.5,  // sin sol apreciable no hay "sol de mañana"
  luminosoInvierno: 4.0,      // ~p72 del invierno a 6 m: el tercio realmente luminoso
};

const aHoras = (hhmm) => {
  const [h, m] = hhmm.split(":").map(Number);
  return h + m / 60;
};

const pisoDe = (altura) => Math.max(0, Math.round((altura - 1) / 3));

function horasEn(datos, altura, fecha) {
  return datos?.[String(altura)]?.[fecha]?.h ?? null;
}

function anualEn(datos, altura) {
  const porFecha = datos?.[String(altura)];
  if (!porFecha) return null;
  const vals = Object.values(FECHAS).map((f) => porFecha[f]?.h).filter((v) => v != null);
  return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
}

// horas de sol antes y después de las 13:00, sumadas sobre las 4 estaciones
function manianaTarde(datos, altura) {
  let maniana = 0, tarde = 0;
  for (const f of Object.values(FECHAS)) {
    for (const tramo of datos?.[String(altura)]?.[f]?.iv || []) {
      const [a, b] = tramo.split("-").map(aHoras);
      maniana += Math.max(0, Math.min(b, 13) - a);
      tarde += Math.max(0, b - Math.max(a, 13));
    }
  }
  return [maniana, tarde];
}

/* Prioridad (máximo UNA oración; documentada en DECISIONES.md):
   cueva > cueva_invernal > depende_del_piso > sol_de_mañana/tarde > luminoso.
   La condición más limitante le gana a la más halagadora: si algo es cueva,
   no importa que además tenga sesgo de tarde. */
export function veredicto(horasPorAltura, alturaSel) {
  if (!horasPorAltura || !Object.keys(horasPorAltura).length) return null;
  const U = UMBRALES;
  const alturas = Object.keys(horasPorAltura).map(Number).sort((a, b) => a - b);
  if (!alturas.includes(alturaSel)) return null;

  // cueva: ni subiendo hasta el tope hay sol
  const maxAnual = Math.max(...alturas.map((a) => anualEn(horasPorAltura, a) ?? 0));
  if (maxAnual <= U.cuevaAnualMax) {
    return {
      arquetipo: "cueva",
      corto: "muy poco sol",
      texto: "Muy poco sol directo a cualquier altura: los edificios vecinos " +
             "lo bloquean casi todo el año.",
    };
  }

  const invierno = horasEn(horasPorAltura, alturaSel, FECHAS.invierno);
  const verano = horasEn(horasPorAltura, alturaSel, FECHAS.verano);
  if (invierno != null && verano != null &&
      invierno <= U.cuevaInvernalInvierno && verano >= U.cuevaInvernalVerano) {
    return {
      arquetipo: "cueva_invernal",
      corto: "cueva en invierno",
      texto: "Sol en verano, cueva en invierno: de mayo a agosto casi no " +
             "recibe sol directo.",
    };
  }

  // depende del piso: mirando hasta 9 m por encima de la altura elegida
  const tope = Math.min(alturaSel + 9, alturas[alturas.length - 1]);
  const anualSel = anualEn(horasPorAltura, alturaSel);
  const anualTope = anualEn(horasPorAltura, tope);
  if (anualSel != null && anualTope != null &&
      anualTope - anualSel > U.dependePisoDif) {
    // piso de quiebre: el mayor salto de la curva anual por encima de la altura elegida
    let quiebre = tope, saltoMax = -1;
    for (const a of alturas) {
      if (a <= alturaSel || a > tope) continue;
      const salto = (anualEn(horasPorAltura, a) ?? 0) - (anualEn(horasPorAltura, a - 3) ?? 0);
      if (salto > saltoMax) { saltoMax = salto; quiebre = a; }
    }
    const piso = pisoDe(quiebre);
    return {
      arquetipo: "depende_del_piso",
      corto: `luminoso desde el ${piso}°`,
      texto: `Depende del piso: por debajo del ${piso}° es oscuro; de ahí ` +
             "para arriba, luminoso.",
    };
  }

  const [maniana, tarde] = manianaTarde(horasPorAltura, alturaSel);
  const total = maniana + tarde;
  if (anualSel != null && anualSel >= U.manianaTardeMinAnual && total > 0) {
    if (maniana / total >= U.manianaTardeFrac) {
      return {
        arquetipo: "sol_de_maniana",
        corto: "sol de mañana",
        texto: "Sol de mañana: la luz se va después del mediodía.",
      };
    }
    if (tarde / total >= U.manianaTardeFrac) {
      return {
        arquetipo: "sol_de_tarde",
        corto: "sol de tarde",
        texto: "Sol de tarde: las mañanas son a la sombra.",
      };
    }
  }

  if (invierno != null && invierno >= U.luminosoInvierno) {
    return {
      arquetipo: "luminoso_todo_el_anio",
      corto: "luminoso todo el año",
      texto: "Luminoso todo el año: recibe sol directo incluso en pleno invierno.",
    };
  }

  return null;
}
