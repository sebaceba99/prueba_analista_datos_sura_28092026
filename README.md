# Prueba técnica — Analista de Datos y Analítica

Entrega de las secciones prácticas (2, 3 y 4) y del documento con las respuestas escritas (1, 2, 5 y 6).

| Carpeta | Contenido |
|---|---|
| [`docs/`](docs/) | `Respuestas_Secciones_1_2_5_6.docx` — respuestas escritas con el razonamiento de cada decisión |
| [`seccion_2/`](seccion_2/) | Consultas T-SQL: original y corregida (2.1), top 15 priorizado (2.2), pipeline diario y controles de calidad (2.3) |
| [`seccion_3/`](seccion_3/) | Validador de calidad del archivo de facturación (Python) |
| [`seccion_4/`](seccion_4/) | Tablero de monitoreo operativo (Streamlit) |

## Instalación (una sola vez, ~1 minuto)

Requiere Python 3.11 o superior.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Sección 3 — Validación de calidad del dato

```bash
cd seccion_3
python validar_facturacion.py      # genera el archivo de prueba y lo valida
python probar_reglas.py            # opcional: pruebas de las reglas
```

Resultados en `seccion_3/salidas/`: `facturacion_validos.parquet`, `facturacion_rechazados.xlsx` (con la razón de rechazo) y `reporte_calidad.json`. Detalle de reglas y decisiones en [`seccion_3/README.md`](seccion_3/README.md).

## Sección 4 — Tablero

```bash
cd seccion_4
streamlit run app.py
```

Se abre en el navegador en `http://localhost:8501`. Tiene tres páginas: **Resumen** (¿vamos bien o mal y dónde mirar?), **Diagnóstico** y **Ficha de cliente**, a las que se llega con un clic. Público objetivo, KPI y metas, principios de diseño con sus referencias, herramienta elegida y conexión a un Lakehouse en [`seccion_4/README.md`](seccion_4/README.md).

## Sección 2 — SQL

Las consultas están escritas en T-SQL (endpoint SQL de Microsoft Fabric / SQL Server) y no requieren ejecución local. Se validaron sobre datos sintéticos traduciéndolas a DuckDB. El análisis completo está en el documento de `docs/`.

| Archivo | Pregunta |
|---|---|
| `2_1_consulta_original.sql` | Consulta heredada, sin cambios |
| `2_1_consulta_corregida.sql` | Versión corregida y documentada |
| `2_2_top15_atencion_prioritaria.sql` | Top 15 clientes para visita, con puntaje y motivo principal |
| `2_3_pipeline_tablero_ejecutivo.sql` | Snapshot diario por cliente activo |
| `2_3_controles_calidad.sql` | Controles que deciden si el snapshot se publica |

## Datos

Todos los datos se generan en el código con semilla fija; no se usa ningún sistema externo.
