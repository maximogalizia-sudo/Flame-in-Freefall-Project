# Catálogo de datos de combustión de NASA PSI

Relevamiento de la búsqueda `combustion` en PSI ([link](https://psi.nasa.gov/physci/repo/search?q=combustion&data_source=psi&data_type=investigation)), armado el 13/11/2026 para **Flame in Freefall**.

## En una línea

PSI tiene **24 investigaciones** de *Combustion Science*, con **66.586 archivos** y unos **13,5 TB**, casi todo video e imágenes crudas. Lo que sirve de verdad para el proyecto pesa unos **2 GB**: 22 tablas experimentales en CSV (en `tablas_experimentales/`, ~200 KB en total) y unos 270 PDF y documentos únicos para el RAG (~0,8 GB). Todo es público, sin cuenta, con licencia **CC0-1.0** (PSI-10 no declara licencia). Hay que citar el DOI de cada investigación.

## Paso a paso: qué hacer con esto

1. **Abrí primero `catalog/psi_combustion_catalog.csv`** en Excel o Google Sheets. Tiene una fila por experimento, con qué datos hay, qué variables mide y si sirve para el ML o para el RAG. Con eso decidís qué bajar.
2. **Las tablas ya las tenés** en `tablas_experimentales/`, así que para empezar el modelo no hace falta bajar nada. Arrancá con `PSI-69_Experimental table_FLEX.csv` y `PSI-159_..._CFI-G_*.csv`.
3. **Bajá esta carpeta entera a tu compu.** Lo más fácil es bajar `psi-combustion.zip` (está en esta misma carpeta, ~0,9 MB) y descomprimirlo.
4. **Instalá Python 3.9 o más nuevo** ([python.org](https://www.python.org/downloads/)). El script usa solo la librería estándar, así que no hay que instalar ninguna dependencia.
5. **Abrí una terminal en la carpeta `scripts/`** y corré, en este orden:

   ```bash
   # a) Prueba chica: solo las tablas originales (~200 KB)
   python psi_download.py ../catalog/psi_archivos_utiles.csv --out ../descargas --kind experimental_table

   # b) Los documentos para el RAG, sin duplicados (~0,8 GB)
   python psi_download.py ../catalog/rag_documentos_pdf.csv --out ../descargas --skip-duplicates

   # c) Todo lo "útil": tablas, documentos y datos analizados (hasta ~2,1 GB)
   python psi_download.py ../catalog/psi_archivos_utiles.csv --out ../descargas --max-mb 60
   ```

   En Windows, si `python` no anda, probá con `py`. Los archivos quedan en `descargas/PSI-xx/<categoría>/`. Si cortás el script y lo volvés a correr, saltea lo que ya bajó.
6. **Espacio en disco:** reservá unos **3 GB** libres. Lo "útil" pesa unos 2,1 GB y conviene tener margen. Con `--max-mb 60`, los archivos de más de 60 MB se saltean.
7. **No intentes bajar "toda" la información.** Son 13,5 TB, casi todo video crudo de las cámaras, y no aporta nada al proyecto. Si en algún momento querés un video puntual (por ejemplo, un ensayo de FLEX), lo buscás por nombre en `psi_files_index_completo.csv.gz`.

> **No lo pude probar:** desde mi entorno PSI está bloqueado, así que el script nunca corrió contra el servidor real. Primero corré el paso (a). Si da error, pasame el mensaje y lo arreglo. Algunos archivos viejos pueden fallar porque dan 404 en el propio servidor de NASA, y eso es normal.

### ¿Qué es "el JSON"?

Hay dos cosas distintas que se llaman JSON:

- **`catalog/psi_combustion_catalog.json`** es el mismo catálogo que el CSV, con más detalle: equipo, objetivos, publicaciones y columnas de cada tabla. Está en un formato que un programa lee directo. Sirve para que tu backend o el RAG lo carguen sin parsear Excel. Si lo querés mirar a mano, abrí el CSV.
- **La API JSON de PSI** es la "puerta trasera" de la página de NASA: una dirección web que, en vez de mostrar la página, devuelve los datos en texto ordenado. El script la usa para pedir cada archivo.

## Qué hay en esta carpeta

| Archivo | Qué es |
|---|---|
| `catalog/psi_combustion_catalog.csv` | Una fila por investigación: nombre, plataforma, fechas, DOI, cantidad de archivos y formatos, tablas, variables clave, resultado medido y valor para ML y RAG. |
| `catalog/psi_combustion_catalog.json` | Lo mismo, más completo: equipo, objetivo, hipótesis, método, impacto, publicaciones y perfil de cada tabla (filas, columnas, encoding). |
| `catalog/psi_files_index_completo.csv.gz` | Los 66.586 archivos: investigación, categoría, nombre, tamaño, tipo y `s3_key`. |
| `catalog/psi_archivos_utiles.csv` | 2.546 archivos que valen la pena (tablas, documentos, datos analizados y tabulares chicos). Excluye video, imágenes y zips. |
| `catalog/rag_documentos_pdf.csv` | 316 documentos (reports, science docs, presentaciones, ingeniería). Hay 269 únicos; la columna `duplicate_of_other_investigation` marca los que se repiten entre investigaciones (ACME y SAFFIRE comparten muchos). |
| `tablas_experimentales/` | Las 22 tablas experimentales originales de PSI (CSV). |
| `rag_fichas/` | Una ficha Markdown por investigación, con objetivo, hipótesis, método, impacto, tablas y publicaciones. Tiene un front-matter con el DOI y la URL, listo para indexar y citar. |
| `scripts/psi_download.py` | Descarga los archivos listados en los CSV usando la API pública de PSI. |

## Cómo se accede a PSI

- La página es una app JavaScript. Con `curl` o un scraper sobre el HTML no aparece nada.
- **API JSON pública, sin login y sin documentación oficial:**
  - Búsqueda: `https://psi.nasa.gov/geode-py/ws/repo/search?size=500&ffield=Research%20Area&fvalue=combustion%20science` (24 resultados).
  - Metadatos de una investigación: `https://psi.nasa.gov/geode-py/ws/repo/investigations/PSI-69`
  - Descarga: `GET /geode-py/ws/studies/<PSI-ID>/download?file=<nombre>&version=<v>&redirect=false` devuelve una URL S3 firmada.
  - Algunos archivos viejos apuntan a `https://psi.ndc.nasa.gov/mpglobal/api/athena/file/<id>/?inline=1&log=1`. Según otro equipo, unos 2.600 de esos dan 404 en el servidor de NASA, casi todos de FLEX-2.
- **Ojo con la procedencia.** Desde el entorno donde armé esto, `psi.nasa.gov` está bloqueado para descargas directas. La búsqueda y los metadatos los leí de la API, y el resto lo tomé de copias públicas en GitHub de la misma API que hicieron otros equipos del reto: `Krypton-N/NASA-SPACE-APPS` (metadatos y manifiestos de archivos) y `FarhanS7/IGNIS` (tablas). Comparé los tamaños con los de PSI: la única diferencia es que esas copias usan fin de línea LF en lugar de CRLF. Igual conviene volver a bajar las tablas desde PSI con `psi_download.py` antes de la demo.

## Las 24 investigaciones

ML y RAG indican qué tan útil es cada una para el modelo y para el chat (alta, media o baja).

| PSI | Sigla | Tipo | Tabla (filas) | Variables clave | Resultado medido | ML | RAG |
|---|---|---|---|---|---|---|---|
| PSI-69 | **FLEX** | gotas | 274 | combustible, presión, O2 0.12–0.34, CO2/He supresor, diámetro | **Extinción / Completo / Disrupción** | **alta** | media |
| PSI-159 | **ACME CFI-G** | gas | 179 + 154 | combustible, O2 0.18–0.85, presión 0.5–3 bar | **ignición sí/no, autoextinción** | **alta** | media |
| PSI-25 | **BASS-II** | sólidos | 129 | material, O2 13.6–22.2 %, flujo (ventilador), CO/CO2 | sin etiqueta (está en el Summary Report y los videos) | media-alta | alta |
| PSI-99 | **SAFFIRE-II** | sólidos grandes | 15 | material, espesor, flujo 20 cm/s, O2 ~21.5 % | longitud quemada y velocidad de propagación en µg y en 1 g | media | alta |
| PSI-26 | BASS | sólidos | 122 | material y geometría (flujo en el xlsx "As Run") | en el xlsx | media | alta |
| PSI-39 | CFI | gotas / llamas frías | 87 | presión, O2, He, combustible | en el xlsx DataSummary | media | alta |
| PSI-10 | ACME Flame Design | gas | 207 + 120 | O2 0.2–0.4, presión 0.4–3 atm, caudales | sin etiqueta | media | media |
| PSI-107 | SPICE | gas, humo | 469 | combustible, tobera, caudal, aire de coflujo | punto de humo | media | media |
| PSI-68 | FLEX-2 | gotas con flujo | 8 (matriz) | O2 0.17–0.30, presión 0.5–3 atm, flujo 0.1–5 cm/s | en Raw Data (.txt) | media | media |
| PSI-98 / PSI-100 | SAFFIRE-I / III | sólidos grandes | 4 / 2 | SIBAL, flujo 20–30 cm/s | tiempo de quemado y telemetría (O2, CO2, T) | baja | alta |
| PSI-101 / PSI-102 | SAME-R / SAME | humo | 109 / 8 | material, flujo, envejecimiento | tamaño de partículas de humo | baja | alta |
| PSI-20 | ACME BRE | gas que emula sólidos | 18 (bitácora) | O2 21–40 % | llamas por día | baja | alta |
| PSI-22 / PSI-23 / PSI-21 | ACME E-FIELD / s-Flame / CLD | gas | bitácoras | combustible, dilución | llamas por día | baja | media / media / baja |
| PSI-106 | SLICE | gas | 12 | combustible | — | baja | baja |
| PSI-47 | DAFT / DAFT-2 | polvo | 2 | — | partículas | no | baja |
| PSI-115, 117, 142 | modelado y ensayos en tierra | — | — | — | — | baja | media / media / baja |
| PSI-60, PSI-62 | análisis de SPICE y de BASS | — | sin archivos | — | — | no | baja |

## Recomendación

**Para el modelo de ML (¿se apaga o sigue ardiendo?):**
1. **FLEX (PSI-69).** Es la base: 274 ensayos con resultado explícito y variación real de O2, presión y supresores. Tiene detalles a cuidar. El encoding es cp1252. En los encabezados se perdió el "2": `O`=O2, `N`=N2, `CO`=CO2. Las 61 "Disruption" hay que excluirlas o tratarlas aparte.
2. **ACME CFI-G (PSI-159).** Suma unos 333 ensayos con ignición y autoextinción, en gas, con O2 hasta 85 %. Es lo que le da al modelo un borde de "O2 alto" y alimenta la alarma de *zona sin datos*.
3. **BASS-II + BASS + SAFFIRE-II (sólidos: material + O2 + flujo).** Es justo lo que pide el simulador, pero la etiqueta no viene en el CSV. Hay que sacarla del *BASS-II Summary Report*, de los xlsx de matrices de ensayo y de las columnas de SAFFIRE-II. Para el MVP sirve un set chico, curado a mano y con la fuente de cada fila.
4. **No mezclar familias sin una columna `familia`** (gotas, gas, sólidos). Una gota de heptano y una tela no son comparables. Lo más honesto es un modelo por familia o un modelo con esa variable, y que la alarma de extrapolación mire el rango de cada familia.

**Para el RAG:**
- Indexar primero las 24 `rag_fichas/` (pesan poco y ya traen el DOI) y los documentos únicos de `rag_documentos_pdf.csv`, empezando por los *Final Report* / *Summary Report*, SRD, *data interpretation* y presentaciones de BASS, BASS-II, SAFFIRE, FLEX, CFI, SAME y ACME BRE.
- Para la pregunta "¿qué telas se apagan solas con poco flujo?", las fuentes clave son BASS (presentaciones sobre inflamabilidad de telas SIBAL y algodón-fibra de vidrio), BASS-II y SAFFIRE-II (Nomex no propaga; silicona apenas quema en µg).

**No bajar:** Raw Data de ACME, FLEX y BASS. Son ~13 TB de video (.ts/.mp4) y zips de imágenes, y no aportan nada al MVP.

## Huecos que conviene saber

- PSI **no tiene datos en gravedad parcial** (Luna o Marte). Todo es microgravedad o ensayos en tierra. Esa es justamente la "zona sin datos".
- SoFIE todavía no está en PSI. Para eso hay que buscar en NTRS.
- Ningún CSV de sólidos tiene una columna de "se extinguió" limpia. Esa etiqueta hay que construirla.
