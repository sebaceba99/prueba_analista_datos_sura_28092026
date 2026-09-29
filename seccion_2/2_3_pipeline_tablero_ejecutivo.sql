/* =============================================================================
   Nombre      : Snapshot diario del tablero ejecutivo por cliente
   Sección     : 2.3
   Dialecto    : T-SQL (SQL Server / endpoint SQL de Microsoft Fabric)
   Grano       : 1 fila por cliente activo y fecha de corte
   Frecuencia  : diaria, corte al día anterior

   Decisiones
   - "Mes en curso" = del día 1 hasta la fecha de corte. La variación se
     compara contra el MISMO tramo del mes anterior (del 1 al mismo día): si el
     día 10 se compara contra el mes anterior completo, todos los clientes
     "mejoran" los primeros días del mes. También se entrega el mes anterior
     completo como referencia.
   - Si la fecha de corte es el último día del mes, el mes anterior se toma
     completo (evita comparar un febrero completo contra 1-28 de enero).
   - Trabajadores activos: período facturado del mes de corte; si aún no se ha
     facturado, el último período disponible. Se expone qué período se usó.
   - Tasa sin denominador => clasificación 'sin dato', nunca 'bajo': un
     cliente sin información no debe parecer un cliente sano.
   - Nota de negocio: con la tasa acumulada del mes, los primeros días casi
     todos los clientes quedan en 'bajo'. Se recomienda validar con gerencia si
     la clasificación debería usar una ventana móvil de 30 días.

   Persistencia (escribir → auditar → publicar):
     1. Se inserta en stg.tablero_ejecutivo_diario (borrando antes la misma
        fecha_corte, para que re-ejecutar el día sea idempotente).
     2. Se corre 2_3_controles_calidad.sql sobre staging.
     3. Solo si no falla ningún control bloqueante se copia a
        dbo.tablero_ejecutivo_diario, que es lo que lee el tablero.
============================================================================= */

DECLARE @fecha_corte     DATE = DATEADD(DAY, -1, CAST(GETDATE() AS DATE));
DECLARE @ini_mes         DATE = DATEFROMPARTS(YEAR(@fecha_corte), MONTH(@fecha_corte), 1);
DECLARE @ini_mes_ant     DATE = DATEADD(MONTH, -1, @ini_mes);
DECLARE @fin_mes_ant     DATE = EOMONTH(@ini_mes_ant);
DECLARE @corte_mes_ant   DATE = CASE WHEN @fecha_corte = EOMONTH(@fecha_corte)
                                     THEN @fin_mes_ant
                                     ELSE DATEADD(MONTH, -1, @fecha_corte) END;  -- 31-mar -> 28/29-feb

WITH activos AS (
    SELECT id_cliente, nombre, sector, clase_riesgo
    FROM clientes
    WHERE estado = 'activo'
),
casos_mes AS (
    SELECT
        id_cliente,
        SUM(CASE WHEN fecha_ocurrencia >= @ini_mes     AND fecha_ocurrencia <= @fecha_corte   THEN 1 ELSE 0 END) AS casos_mes,
        SUM(CASE WHEN fecha_ocurrencia >= @ini_mes     AND fecha_ocurrencia <= @fecha_corte
                  AND tipo = 'grave'                                                          THEN 1 ELSE 0 END) AS graves_mes,
        SUM(CASE WHEN fecha_ocurrencia >= @ini_mes_ant AND fecha_ocurrencia <= @corte_mes_ant THEN 1 ELSE 0 END) AS casos_mes_ant_mismo_corte,
        SUM(CASE WHEN fecha_ocurrencia >= @ini_mes_ant AND fecha_ocurrencia <= @fin_mes_ant   THEN 1 ELSE 0 END) AS casos_mes_ant_completo
    FROM casos
    WHERE fecha_ocurrencia >= @ini_mes_ant
      AND fecha_ocurrencia <= @fecha_corte
    GROUP BY id_cliente
),
trabajadores AS (   -- período del mes de corte o, si no existe aún, el último disponible
    SELECT id_cliente, periodo AS periodo_trabajadores, trabajadores_activos
    FROM (
        SELECT id_cliente, periodo, trabajadores_activos,
               ROW_NUMBER() OVER (PARTITION BY id_cliente ORDER BY periodo DESC) AS rn
        FROM facturacion
        WHERE periodo <= @ini_mes
    ) f
    WHERE rn = 1
),
prevencion_30d AS (
    SELECT id_cliente,
           COUNT(*)          AS actividades_prevencion_30d,
           SUM(participantes) AS participantes_prevencion_30d
    FROM prevencion
    WHERE fecha >  DATEADD(DAY, -30, @fecha_corte)   -- 30 días incluyendo la fecha de corte
      AND fecha <= @fecha_corte
    GROUP BY id_cliente
),
base AS (
    SELECT
        a.id_cliente, a.nombre, a.sector, a.clase_riesgo,
        COALESCE(c.casos_mes, 0)                  AS casos_mes,
        COALESCE(c.graves_mes, 0)                 AS graves_mes,
        COALESCE(c.casos_mes_ant_mismo_corte, 0)  AS casos_mes_ant_mismo_corte,
        COALESCE(c.casos_mes_ant_completo, 0)     AS casos_mes_ant_completo,
        t.periodo_trabajadores,
        t.trabajadores_activos,
        COALESCE(p.actividades_prevencion_30d, 0)   AS actividades_prevencion_30d,
        COALESCE(p.participantes_prevencion_30d, 0) AS participantes_prevencion_30d
    FROM activos a
    LEFT JOIN casos_mes c       ON c.id_cliente = a.id_cliente
    LEFT JOIN trabajadores t    ON t.id_cliente = a.id_cliente
    LEFT JOIN prevencion_30d p  ON p.id_cliente = a.id_cliente
)
SELECT
    @fecha_corte                                                        AS fecha_corte,
    b.id_cliente, b.nombre, b.sector, b.clase_riesgo,
    b.casos_mes,
    b.graves_mes,
    b.casos_mes_ant_mismo_corte,
    b.casos_mes - b.casos_mes_ant_mismo_corte                           AS variacion_casos,
    ROUND((b.casos_mes - b.casos_mes_ant_mismo_corte) * 100.0
          / NULLIF(b.casos_mes_ant_mismo_corte, 0), 1)                  AS variacion_casos_pct,   -- NULL si el mes anterior fue 0
    b.casos_mes_ant_completo,
    b.periodo_trabajadores,
    b.trabajadores_activos,
    ROUND(b.casos_mes * 100.0 / NULLIF(b.trabajadores_activos, 0), 2)   AS tasa_incidencia,
    b.actividades_prevencion_30d,
    b.participantes_prevencion_30d,
    CASE
        WHEN NULLIF(b.trabajadores_activos, 0) IS NULL                    THEN 'sin dato'
        WHEN b.casos_mes * 100.0 / b.trabajadores_activos >  5            THEN 'crítico'
        WHEN b.casos_mes * 100.0 / b.trabajadores_activos >= 2            THEN 'moderado'
        ELSE 'bajo'
    END                                                                 AS clasificacion,
    SYSDATETIME()                                                       AS fecha_proceso
FROM base b;
