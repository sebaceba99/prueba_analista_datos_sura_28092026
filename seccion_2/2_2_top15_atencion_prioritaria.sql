/* =============================================================================
   Nombre      : Top 15 clientes para visita prioritaria (semanal)
   Sección     : 2.2
   Dialecto    : T-SQL (SQL Server 2022 / endpoint SQL de Microsoft Fabric)
   Uso         : Se corre el lunes; alimenta la planeación de visitas de la semana.

   Criterio: una visita de prevención tiene más valor donde (a) el cliente se
   está accidentando más de lo que su tamaño explica, (b) los casos son graves,
   (c) la situación empeora, (d) el riesgo inherente es alto y (e) hace tiempo
   nadie hace prevención allí. Cada componente se normaliza con PERCENT_RANK
   (0 = el mejor cliente, 1 = el peor) para que ninguno domine por su escala,
   y se pondera:

       Incidencia 90 días (casos por 100 trabajadores) ....... 30 %
       Severidad (graves 90 días + graves aún abiertos) ....... 25 %
       Tendencia (90 días recientes vs 90 días anteriores) .... 15 %
       Clase de riesgo (1-5) ................................... 15 %
       Días sin actividad de prevención ........................ 15 %

   Los pesos son un punto de partida para validar con las coordinaciones; se
   dejan como variables para ajustarlos sin reescribir la consulta.
============================================================================= */

DECLARE @corte DATE = DATEADD(DAY, -1, CAST(GETDATE() AS DATE));  -- datos hasta ayer
DECLARE @w_incidencia DECIMAL(4,2) = 0.30,
        @w_severidad  DECIMAL(4,2) = 0.25,
        @w_tendencia  DECIMAL(4,2) = 0.15,
        @w_riesgo     DECIMAL(4,2) = 0.15,
        @w_prevencion DECIMAL(4,2) = 0.15;
DECLARE @min_trabajadores INT = 20;  -- piso del denominador: evita que 1 caso en un cliente de 3 personas domine

WITH activos AS (
    SELECT id_cliente, nombre, sector, clase_riesgo
    FROM clientes
    WHERE estado = 'activo'
),
trabajadores AS (   -- último período facturado con trabajadores > 0
    SELECT id_cliente, trabajadores_activos
    FROM (
        SELECT id_cliente, trabajadores_activos,
               ROW_NUMBER() OVER (PARTITION BY id_cliente ORDER BY periodo DESC) AS rn
        FROM facturacion
        WHERE periodo <= @corte AND trabajadores_activos > 0
    ) t
    WHERE rn = 1
),
casos_cliente AS (
    SELECT
        id_cliente,
        SUM(CASE WHEN fecha_ocurrencia >  DATEADD(DAY, -90, @corte) THEN 1 ELSE 0 END)                     AS casos_90d,
        SUM(CASE WHEN fecha_ocurrencia <= DATEADD(DAY, -90, @corte)
                  AND fecha_ocurrencia >  DATEADD(DAY, -180, @corte) THEN 1 ELSE 0 END)                    AS casos_90d_previos,
        SUM(CASE WHEN fecha_ocurrencia >  DATEADD(DAY, -90, @corte) AND tipo = 'grave' THEN 1 ELSE 0 END)  AS graves_90d,
        SUM(CASE WHEN estado = 'abierto' AND tipo = 'grave' THEN 1 ELSE 0 END)                             AS graves_abiertos
    FROM casos
    WHERE fecha_ocurrencia <= @corte
    GROUP BY id_cliente
),
prevencion_cliente AS (
    SELECT id_cliente, MAX(fecha) AS ultima_prevencion
    FROM prevencion
    WHERE fecha <= @corte
    GROUP BY id_cliente
),
metricas AS (
    SELECT
        a.id_cliente, a.nombre, a.sector, a.clase_riesgo,
        t.trabajadores_activos,
        COALESCE(c.casos_90d, 0)          AS casos_90d,
        COALESCE(c.casos_90d_previos, 0)  AS casos_90d_previos,
        COALESCE(c.graves_90d, 0)         AS graves_90d,
        COALESCE(c.graves_abiertos, 0)    AS graves_abiertos,
        p.ultima_prevencion,
        -- Incidencia con piso en el denominador
        COALESCE(c.casos_90d, 0) * 100.0
            / CASE WHEN COALESCE(t.trabajadores_activos, 0) < @min_trabajadores
                   THEN @min_trabajadores ELSE t.trabajadores_activos END              AS incidencia_90d,
        COALESCE(c.graves_90d, 0) + COALESCE(c.graves_abiertos, 0)                     AS severidad,
        -- Razón suavizada (+1) para que pasar de 0 a 1 caso no sea "infinito"
        (COALESCE(c.casos_90d, 0) + 1.0) / (COALESCE(c.casos_90d_previos, 0) + 1.0)   AS tendencia,
        -- Sin ninguna prevención registrada = 365 días (máxima brecha razonable)
        COALESCE(DATEDIFF(DAY, p.ultima_prevencion, @corte), 365)                      AS dias_sin_prevencion
    FROM activos a
    LEFT JOIN trabajadores t        ON t.id_cliente = a.id_cliente
    LEFT JOIN casos_cliente c       ON c.id_cliente = a.id_cliente
    LEFT JOIN prevencion_cliente p  ON p.id_cliente = a.id_cliente
),
puntaje AS (
    SELECT
        m.*,
        PERCENT_RANK() OVER (ORDER BY m.incidencia_90d)       AS p_incidencia,
        PERCENT_RANK() OVER (ORDER BY m.severidad)            AS p_severidad,
        PERCENT_RANK() OVER (ORDER BY m.tendencia)            AS p_tendencia,
        (m.clase_riesgo - 1) / 4.0                            AS p_riesgo,
        PERCENT_RANK() OVER (ORDER BY m.dias_sin_prevencion)  AS p_prevencion
    FROM metricas m
),
final AS (
    SELECT
        p.*,
        @w_incidencia * p_incidencia + @w_severidad * p_severidad + @w_tendencia * p_tendencia
          + @w_riesgo * p_riesgo + @w_prevencion * p_prevencion                AS score_prioridad
    FROM puntaje p
)
SELECT TOP 15
    ROW_NUMBER() OVER (ORDER BY f.score_prioridad DESC, f.graves_90d DESC, f.casos_90d DESC) AS prioridad,
    f.id_cliente, f.nombre, f.sector, f.clase_riesgo,
    f.trabajadores_activos,
    f.casos_90d, f.graves_90d, f.graves_abiertos,
    ROUND(f.incidencia_90d, 2)          AS incidencia_90d,
    f.casos_90d_previos,
    f.ultima_prevencion, f.dias_sin_prevencion,
    ROUND(f.score_prioridad * 100, 1)   AS score_prioridad,
    motivo.motivo_principal             -- lo primero que el coordinador debe revisar en la visita
FROM final f
CROSS APPLY (      -- motivo = componente dinámico en el que el cliente está peor ubicado frente a los demás.
                   -- La clase de riesgo no compite como motivo: es fija y ya se muestra en su columna.
    SELECT TOP 1 v.motivo AS motivo_principal
    FROM (VALUES
        (f.p_incidencia, @w_incidencia, 'Incidencia alta para su tamaño'),
        (f.p_severidad,  @w_severidad,  'Casos graves recientes o abiertos'),
        (f.p_tendencia,  @w_tendencia,  'Casos en aumento'),
        (f.p_prevencion, @w_prevencion, 'Sin prevención reciente')
    ) v(percentil, peso, motivo)
    ORDER BY v.percentil DESC, v.peso DESC
) motivo
ORDER BY prioridad;
