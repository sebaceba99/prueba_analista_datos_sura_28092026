# Riesgo y costo de casos por cliente activo (12 meses)

| | |
|---|---|
| **Identificador** | `cons_riesgo_costo_cliente_12m` |
| **Archivo** | [`2_1_consulta_corregida.sql`](2_1_consulta_corregida.sql) |
| **Versión** | v2.0 — corrige la v1 heredada ([`2_1_consulta_original.sql`](2_1_consulta_original.sql)) |
| **Dueño** | Equipo de Datos y Analítica |
| **Dialecto** | T-SQL (SQL Server / endpoint SQL de Microsoft Fabric) |
| **Frecuencia sugerida** | Mensual, a partir del día 1 (la ventana solo cambia al cerrar un mes) |

---

## 1. Propósito

Muestra, para cada cliente activo que tuvo incidentes en el último año, cuántos casos tuvo, qué proporción fueron graves, cuánto costaron en total y cuánto por cada trabajador. Además, ubica a cada cliente dentro de su clase de riesgo, del más costoso al menos costoso.

**Preguntas que responde**
- Entre los clientes de la misma clase de riesgo, ¿cuáles nos cuestan más?
- ¿Qué clientes tienen una proporción alta de casos graves?
- ¿Cuánto cuesta la accidentalidad por trabajador en cada cliente?

**Usuarios y uso:** coordinadores de cuenta y gerencia, para priorizar el seguimiento y preparar renovaciones o conversaciones comerciales con los clientes más costosos.

---

## 2. Tablas fuente

| Tabla | Tipo | Campos usados | Filtro aplicado |
|---|---|---|---|
| `clientes` | Dimensión | `id_cliente`, `nombre`, `clase_riesgo`, `estado` | `estado = 'activo'` |
| `casos` | Hechos (1 fila por caso) | `id_cliente`, `fecha_ocurrencia`, `tipo`, `costo` | `fecha_ocurrencia` dentro de la ventana |
| `facturacion` | Hechos (1 fila por cliente y período mensual) | `id_cliente`, `periodo`, `trabajadores_activos` | `periodo` dentro de la ventana |

**Cómo se unen:** `casos` y `facturacion` se resumen primero a **una fila por cliente**, cada una por separado, y después se unen a `clientes` por `id_cliente`:

- `clientes` → `casos` con **INNER JOIN**: solo salen los clientes que tienen casos.
- `clientes` → `facturacion` con **LEFT JOIN**: un cliente sin facturación sigue apareciendo, con el denominador vacío.

---

## 3. Parámetros y ventana de tiempo

| Parámetro | Valor | Ejemplo (ejecución 01-oct-2026) |
|---|---|---|
| `@hoy` | Fecha de ejecución | 2026-10-01 |
| `@inicio_mes` | Día 1 del mes en curso (**excluido**) | 2026-10-01 |
| `@inicio_vent` | 12 meses antes de `@inicio_mes` (incluido) | 2025-10-01 |

**Ventana:** 12 meses calendario cerrados, del `@inicio_vent` al último día del mes anterior. Casos y facturación usan **la misma ventana**. Para cambiarla, basta modificar el `-12` de `@inicio_vent`.

**Grano del resultado:** 1 fila por cliente activo con al menos un caso en la ventana.

---

## 4. Campos del resultado

| Campo | Tipo | Definición | Regla / cálculo | Si no hay dato |
|---|---|---|---|---|
| `id_cliente` | Llave | Identificador del cliente | `clientes.id_cliente` | — |
| `nombre` | Texto | Nombre del cliente | `clientes.nombre` | — |
| `clase_riesgo` | Entero 1–5 | Nivel de riesgo del cliente | `clientes.clase_riesgo` | — |
| `total_casos` | Entero | Casos ocurridos en la ventana | `COUNT(*)` de `casos` | No aplica: solo salen clientes con casos |
| `casos_graves` | Entero | Casos clasificados como graves | Casos con `tipo = 'grave'` (campo oficial) | 0 |
| `costo_total` | Decimal (COP) | Costo total de los casos | `SUM(casos.costo)` | NULL si ningún caso tiene costo |
| `trabajadores_prom` | Decimal | Promedio mensual de trabajadores activos facturados | `AVG(trabajadores_activos)` sobre los períodos facturados en la ventana, en decimal | NULL si el cliente no tiene facturación |
| `pct_graves` | Decimal, 1 dec. | % de casos que fueron graves | `casos_graves × 100 / total_casos` | — |
| `costo_x_trab` | Decimal, 0 dec. (COP) | Costo de casos por trabajador en 12 meses | `costo_total / trabajadores_prom` | NULL si no hay facturación o el promedio es 0 |
| `ranking_clase` | Entero | Posición del cliente por costo dentro de su clase de riesgo (1 = más costoso) | `DENSE_RANK()` por `clase_riesgo`, orden `costo_total` descendente; los empates comparten posición | — |

**Orden del resultado:** clase de riesgo de mayor a menor y, dentro de cada clase, por `ranking_clase`.

---

## 5. Notas de uso

**Cómo interpretar**
- `pct_graves` y `costo_x_trab` son **razones**. No se suman ni se promedian entre clientes: para un grupo se recalculan con los totales (Σ graves / Σ casos, Σ costo / Σ trabajadores).
- `ranking_clase` ordena por **costo absoluto**, así que favorece a los clientes grandes. Para comparar eficiencia entre clientes de distinto tamaño se usa `costo_x_trab`.
- El ranking solo compara clientes **dentro de la misma clase de riesgo**. Un 1 en clase 2 y un 1 en clase 5 no son equivalentes.

**Qué no incluye**
- Clientes inactivos ni clientes activos sin casos en la ventana. Si se necesitan, se cambia el `INNER JOIN` a `casos_cliente` por un `LEFT JOIN` con `COALESCE(..., 0)`.
- El mes en curso, para no comparar meses incompletos.

**Dependencias de calidad**
- Requiere que `casos.tipo` esté diligenciado con `'leve'` o `'grave'`. Un caso sin tipo cuenta en `total_casos`, pero no en `casos_graves`.
- No distingue el estado del caso: abiertos y cerrados cuentan igual. El costo de un caso abierto puede cambiar después.
- Si a un cliente le faltan meses de facturación, el promedio se calcula con los meses que sí existen.

**Diferencias con la versión anterior (v1)**

Los resultados de la v1 **no son comparables** con los de la v2.

| Cambio | Efecto en el resultado |
|---|---|
| Cada tabla de hechos se agrega antes de unirse | La v1 multiplicaba cada caso por el número de períodos facturados. En la prueba con datos sintéticos contaba 9.930 casos cuando eran 1.986 |
| Promedio de trabajadores en decimal | La v1 truncaba el promedio a entero |
| Misma ventana (12 meses cerrados) para casos y facturación | La v1 dividía el costo de 12 meses entre trabajadores de 6 meses, con una ventana que empezaba a mitad de mes |
| "Grave" según `tipo` y no `dias_ausencia > 15` | El número de graves coincide con el resto de reportes |
| `CAST(GETDATE() AS DATE)` en lugar de `CURRENT_DATE` | La v1 no corría en T-SQL |
| Orden por clase y ranking (antes por costo total) | Se lee por clase de riesgo, que es como se usa el ranking |

**Rendimiento:** se recomiendan índices (o particionamiento en el Lakehouse) por `casos (id_cliente, fecha_ocurrencia)` y `facturacion (id_cliente, periodo)`.

**Validación:** probada sobre datos sintéticos traducida a DuckDB, comparando los totales de casos contra un conteo directo de la tabla `casos` en la misma ventana.

---

## 6. Historial de cambios

| Versión | Fecha | Autor | Cambio |
|---|---|---|---|
| v1.0 | — | Autor anterior | Versión original |
| v2.0 | 2026-10 | Sebastián | Corrige multiplicación de filas, promedio entero, ventanas desalineadas, definición de grave y compatibilidad T-SQL |
