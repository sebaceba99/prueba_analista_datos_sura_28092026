/* =============================================================================
   Nombre      : Riesgo y costo de casos por cliente activo (12 meses)
   Sección     : 2.1 — versión corregida de la consulta heredada
   Dialecto    : T-SQL (SQL Server / endpoint SQL de Microsoft Fabric)
   Propósito   : Para cada cliente activo con casos en los últimos 12 meses
                 cerrados: volumen de casos, % graves, costo total, costo por
                 trabajador y posición dentro de su clase de riesgo.

   Cambios frente a la versión original
   1. Cada tabla de hechos se agrega por separado ANTES de unirla a clientes.
      La original unía casos y facturación a la vez, lo que multiplicaba cada
      caso por el número de períodos facturados (~6x en casos, graves y costo).
   2. AVG sobre una columna INT en SQL Server devuelve INT (trunca). Se castea.
   3. Casos y facturación usan la MISMA ventana: 12 meses cerrados. La original
      dividía costo de 12 meses entre trabajadores de 6 meses, y la ventana
      empezaba a mitad de mes.
   4. "Grave" se toma del campo oficial casos.tipo, no de dias_ausencia > 15.
      (Si el negocio define grave por días, esa regla debe vivir en el origen
      o en el modelo semántico, no escondida en una consulta.)
   5. CURRENT_DATE no existe en T-SQL; se usa CAST(GETDATE() AS DATE).
============================================================================= */

DECLARE @hoy          DATE = CAST(GETDATE() AS DATE);
DECLARE @inicio_mes   DATE = DATEFROMPARTS(YEAR(@hoy), MONTH(@hoy), 1);  -- mes en curso (excluido)
DECLARE @inicio_vent  DATE = DATEADD(MONTH, -12, @inicio_mes);           -- 12 meses cerrados

WITH casos_cliente AS (            -- grano: 1 fila por cliente
    SELECT
        ca.id_cliente,
        COUNT(*)                                            AS total_casos,
        SUM(CASE WHEN ca.tipo = 'grave' THEN 1 ELSE 0 END)  AS casos_graves,
        SUM(ca.costo)                                       AS costo_total
    FROM casos ca
    WHERE ca.fecha_ocurrencia >= @inicio_vent
      AND ca.fecha_ocurrencia <  @inicio_mes
    GROUP BY ca.id_cliente
),
facturacion_cliente AS (           -- grano: 1 fila por cliente
    SELECT
        f.id_cliente,
        AVG(CAST(f.trabajadores_activos AS DECIMAL(18, 2))) AS trabajadores_prom
    FROM facturacion f
    WHERE f.periodo >= @inicio_vent
      AND f.periodo <  @inicio_mes
    GROUP BY f.id_cliente
),
base AS (
    SELECT
        c.id_cliente,
        c.nombre,
        c.clase_riesgo,
        cc.total_casos,
        cc.casos_graves,
        cc.costo_total,
        fc.trabajadores_prom
    FROM clientes c
    INNER JOIN casos_cliente cc            ON cc.id_cliente = c.id_cliente   -- solo clientes con casos (igual que el original)
    LEFT  JOIN facturacion_cliente fc      ON fc.id_cliente = c.id_cliente
    WHERE c.estado = 'activo'
)
SELECT
    b.*,
    ROUND(b.casos_graves * 100.0 / NULLIF(b.total_casos, 0), 1)   AS pct_graves,
    ROUND(b.costo_total / NULLIF(b.trabajadores_prom, 0), 0)      AS costo_x_trab,
    DENSE_RANK() OVER (PARTITION BY b.clase_riesgo
                       ORDER BY b.costo_total DESC)               AS ranking_clase
FROM base b
ORDER BY b.clase_riesgo DESC, ranking_clase;
