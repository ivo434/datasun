# datasun

Análisis de asoleamiento para Buenos Aires. Dada una dirección, calcula
cuántas horas de sol directo recibe cada cara de un edificio (frente y
contrafrente) a distintas alturas, considerando la sombra del tejido urbano
real. Incluye un visor web 3D con el mapa de calor solar de toda la comuna.

Piloto: Comuna 6 (Caballito).

## Cómo funciona

Modelo 2.5D: cada edificio es un prisma (huella + altura) tomado del dataset
de Tejido Urbano de BA Data. Para un punto de observación (una ventana), se
muestrea el día cada 10 minutos y se traza un rayo hacia la posición solar
(pvlib); si algún prisma vecino lo supera en altura en el punto de cruce, hay
sombra. El observador se posiciona sobre la fachada real de la construcción
principal de la parcela, con la orientación derivada del eje de la calle.

Los resultados se precomputan para toda la comuna (dos caras × alturas de
3 a 30 m × cuatro fechas estacionales, con los intervalos horarios de sol) y
se sirven como archivos estáticos: el visor no necesita backend.

## Correr el visor

```bash
cd web
npm install
npm run dev   # http://localhost:5173
```

Los datos precomputados ya están en `web/public/data/`.

## Regenerar los datos

Requiere Python 3.10+ y los datasets de BA Data en `data/` (no versionados
por tamaño):

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt

# datasets (BA Data)
curl -L -o data/tejido.zip  "https://cdn.buenosaires.gob.ar/datosabiertos/datasets/secretaria-de-desarrollo-urbano/tejido-urbano/tejido.zip"
curl -L -o data/parcelas.zip "https://cdn.buenosaires.gob.ar/datosabiertos/datasets/secretaria-de-desarrollo-urbano/parcelas/parcelas_catastrales.zip"
curl -L -o data/frentes_parcelas.zip "https://cdn.buenosaires.gob.ar/datosabiertos/datasets/secretaria-de-desarrollo-urbano/parcelas/frentes-parcelas.zip"
curl -L -o data/callejero.zip "https://cdn.buenosaires.gob.ar/datosabiertos/datasets/jefatura-de-gabinete-de-ministros/calles/callejero.zip"
curl -L -o data/espacios_verdes.zip "https://cdn.buenosaires.gob.ar/datosabiertos/datasets/secretaria-de-desarrollo-urbano/espacios-verdes/espacio_verde_publico.zip"
curl -L -o data/red_ferrocarril.zip "https://cdn.buenosaires.gob.ar/datosabiertos/datasets/transporte-y-obras-publicas/estaciones-ferrocarril/red-de-ferrocarril.zip"

.venv/bin/python fase0.py comuna                # precómputo (~15 min) → output/comuna6.sqlite
.venv/bin/python scripts/export_frontend.py     # SQLite → web/public/data/
.venv/bin/python -c "from fase0 import exportar_tejido_geojson; \
  exportar_tejido_geojson(nro=6, salida='web/public/data/tejido_comuna6.geojson')"
```

`fase0.py` también trae modos de análisis puntual:

```bash
.venv/bin/python fase0.py            # una dirección (editar CONFIG): intervalos de sol + mapas
.venv/bin/python fase0.py curva      # horas de sol según piso, ambas caras
.venv/bin/python fase0.py barrido    # todas las parcelas en 500 m, con benchmark
```

## Datos y limitaciones

- Fuentes: Tejido Urbano, Parcelas, Frentes de parcela, Callejero, Espacios
  Verdes y Red de Ferrocarril (BA Data), y el normalizador de direcciones de
  USIG para geocodificar.
- El relevamiento del tejido es de ~2021: la obra nueva posterior no existe
  para el modelo.
- No se modelan árboles, aleros ni balcones; la ventana es un punto.
- Cada resultado lleva notas de confianza según cómo se pudo posicionar el
  observador (esquinas, lotes sin construcción registrada, geometrías
  irregulares). Las estimaciones de contrafrente están en proceso de
  validación contra observación de campo y así se indican en el visor.

## Stack

- Motor: Python — geopandas, shapely, pvlib. Base precomputada en SQLite.
- Visor: Vite + React + deck.gl. Sin backend, sin cookies, sin analytics.
