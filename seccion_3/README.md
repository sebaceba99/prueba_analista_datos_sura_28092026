# Sección 3 — Validación de calidad del archivo de facturación

Simula el control que correría automáticamente con cada carga nueva de facturación, antes de que el archivo entre al pipeline.

```
CSV crudo ─► normalización ─► reglas de las 5 dimensiones
          ─► válidos (Parquet) + rechazados (Excel) + reporte (JSON)
          ─► la carga se APRUEBA o se BLOQUEA
```

## Ejecución

```bash
python validar_facturacion.py     # genera el archivo de prueba (si no existe) y lo valida
python probar_reglas.py           # opcional: pruebas de las reglas con casos conocidos
```

Los archivos tienen celdas `# %%`, así que también se pueden correr por partes en VS Code o Jupyter.

| Archivo | Qué hace |
|---|---|
| `generar_datos.py` | Crea `datos/facturacion_raw.csv` con ~1.160 registros y errores sembrados |
| `validar_facturacion.py` | Valida el archivo y genera las salidas |
| `probar_reglas.py` | Verifica cada regla con casos pequeños de resultado conocido |

## Salidas (carpeta `salidas/`)

- `facturacion_validos.parquet`: registros válidos, con tipos correctos y el número de fila original.
- `facturacion_rechazados.xlsx`: hoja `rechazados` con los códigos de las reglas incumplidas y la razón de rechazo en texto; hoja `resumen_reglas` con el conteo por regla.
- `reporte_calidad.json`: total de registros, válidos, rechazados, score por dimensión, rechazos por regla, correcciones de formato aplicadas y estado de la carga.

## Configuración

Al inicio de `validar_facturacion.py`:

| Constante | Valor | Uso |
|---|---|---|
| `ARCHIVO_ENTRADA` | `datos/facturacion_raw.csv` | Archivo a validar. Si no existe, se genera uno de prueba |
| `FECHA_CARGA` | hoy | Fecha contra la que se mide la oportunidad |
| `UMBRAL_RECHAZO` | `0.20` | % máximo de rechazo. Por encima, la carga se bloquea |
| `MAX_DIAS_ATRASO` | `60` | Atraso máximo permitido del período |

**Manejo de errores y códigos de salida** (para que un orquestador detenga el pipeline sin intervención humana): `0` carga aprobada · `2` error de entrada (archivo sin columnas obligatorias, vacío, o Excel de salida abierto) · `3` carga bloqueada por superar el umbral. Con `UMBRAL_RECHAZO = 0.10` se puede ver el bloqueo.

## Reglas

| Código | Dimensión | Regla |
|---|---|---|
| COM_01–04 | Completitud | id_cliente, periodo, trabajadores_activos y valor_contrato no vacíos |
| VAL_01 | Validez | id_cliente reconocible como `CLI-NNNN` |
| VAL_02 | Validez | periodo en un formato de fecha conocido |
| VAL_03–04 | Validez | trabajadores_activos entero entre 0 y 50.000 |
| VAL_05–06 | Validez | valor_contrato numérico entre 0 y 5.000 millones |
| UNI_01 | Unicidad | Duplicado exacto por cliente + periodo: se conserva la primera fila |
| UNI_02 | Unicidad | Duplicado con valores distintos: se rechazan todas las filas |
| CON_01 | Consistencia | trabajadores_activos > 0 cuando valor_contrato > 0 |
| OPO_01 | Oportunidad | periodo no futuro respecto a la fecha de carga |
| OPO_02 | Oportunidad | periodo con máximo 60 días de atraso |

## Decisiones de diseño

1. **Corregir solo lo inequívoco.** `cli-12` y `09/2026` tienen una sola interpretación: se corrigen y el reporte cuenta cuántas correcciones hubo. `$ 1.200.000` o `CL1-00A2` no se adivinan: se rechazan. En datos que terminan en una factura, adivinar es peor que rechazar.
2. **Formatos de fecha explícitos.** Se prueban uno por uno; nunca se usa un lector que adivine, porque `01/08/2026` es agosto en Colombia y enero en EE. UU.
3. **Todas las reglas se evalúan en cada fila**, sin detenerse en el primer error, para que quien corrige en el origen vea todos los problemas de una vez.
4. **La unicidad se evalúa sobre la llave normalizada**: `cli-1` y `CLI-0001` en el mismo mes son el mismo cliente duplicado.
5. **Score por dimensión** = 1 − (filas con al menos una falla en esa dimensión / total de filas).
