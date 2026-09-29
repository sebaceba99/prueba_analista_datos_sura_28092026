/* =============================================================================
   Nombre      : Controles de calidad del snapshot diario (auditar antes de publicar)
   Sección     : 2.3 — pregunta adicional
   Dialecto    : T-SQL
   Uso         : Corre después de cargar stg.tablero_ejecutivo_diario y antes de
                 publicarlo. Devuelve una fila por control. El orquestador
                 (Fabric Data Pipeline / Airflow) lee el resultado:
                   - algún control 'BLOQUEANTE' en 'FALLA' -> no se publica,
                     el tablero sigue mostrando el último día bueno con un
                     aviso de "datos al <fecha>", y se alerta al equipo.
                   - 'ADVERTENCIA' en 'FALLA' -> se publica y se notifica.
                 Los resultados se guardan en dq.historial_controles para ver
                 tendencias de calidad en el tiempo.
============================================================================= */

DECLARE @fecha_corte DATE = DATEADD(DAY, -1, CAST(GETDATE() AS DATE));
DECLARE @ini_mes     DATE = DATEFROMPARTS(YEAR(@fecha_corte), MONTH(@fecha_corte), 1);

WITH hoy AS (
    SELECT * FROM stg.tablero_ejecutivo_diario WHERE fecha_corte = @fecha_corte
),
ayer AS (
    SELECT * FROM dbo.tablero_ejecutivo_diario WHERE fecha_corte = DATEADD(DAY, -1, @fecha_corte)
),
controles AS (
    -- 1. Completitud del resultado: una fila por cada cliente activo
    SELECT 'C01' AS control, 'Filas del snapshot = clientes activos' AS descripcion, 'BLOQUEANTE' AS severidad,
           CAST((SELECT COUNT(*) FROM hoy) AS DECIMAL(18,2)) AS valor,
           CAST((SELECT COUNT(*) FROM clientes WHERE estado = 'activo') AS DECIMAL(18,2)) AS esperado,
           CASE WHEN (SELECT COUNT(*) FROM hoy) = (SELECT COUNT(*) FROM clientes WHERE estado = 'activo')
                THEN 'OK' ELSE 'FALLA' END AS resultado
    UNION ALL
    -- 2. Unicidad del grano
    SELECT 'C02', 'Sin clientes duplicados en el snapshot', 'BLOQUEANTE',
           (SELECT COUNT(*) - COUNT(DISTINCT id_cliente) FROM hoy), 0,
           CASE WHEN (SELECT COUNT(*) - COUNT(DISTINCT id_cliente) FROM hoy) = 0 THEN 'OK' ELSE 'FALLA' END
    UNION ALL
    -- 3. Frescura: la fuente de casos tiene datos recientes (una fuente que dejó de cargar se ve como "cero casos")
    SELECT 'C03', 'Días desde el último caso registrado (máx. 3)', 'BLOQUEANTE',
           (SELECT DATEDIFF(DAY, MAX(fecha_ocurrencia), @fecha_corte) FROM casos), 3,
           CASE WHEN (SELECT DATEDIFF(DAY, MAX(fecha_ocurrencia), @fecha_corte) FROM casos) <= 3 THEN 'OK' ELSE 'FALLA' END
    UNION ALL
    -- 4. Monotonía: los casos acumulados del mes no deberían bajar frente a ayer (salvo el día 1)
    SELECT 'C04', 'Casos acumulados del mes no disminuyen >2% vs ayer', 'BLOQUEANTE',
           (SELECT SUM(casos_mes) FROM hoy), (SELECT SUM(casos_mes) FROM ayer),
           CASE WHEN @fecha_corte = @ini_mes THEN 'OK'
                WHEN (SELECT SUM(casos_mes) FROM hoy) >= 0.98 * (SELECT SUM(casos_mes) FROM ayer) THEN 'OK'
                ELSE 'FALLA' END
    UNION ALL
    -- 5. Integridad referencial de la fuente
    SELECT 'C05', 'Casos del mes con cliente inexistente', 'BLOQUEANTE',
           (SELECT COUNT(*) FROM casos ca WHERE ca.fecha_ocurrencia >= @ini_mes
               AND NOT EXISTS (SELECT 1 FROM clientes c WHERE c.id_cliente = ca.id_cliente)), 0,
           CASE WHEN EXISTS (SELECT 1 FROM casos ca WHERE ca.fecha_ocurrencia >= @ini_mes
               AND NOT EXISTS (SELECT 1 FROM clientes c WHERE c.id_cliente = ca.id_cliente)) THEN 'FALLA' ELSE 'OK' END
    UNION ALL
    -- 6. Dominio y nulos en campos que alimentan indicadores
    SELECT 'C06', 'Casos del mes con tipo, fecha o cliente inválidos', 'BLOQUEANTE',
           (SELECT COUNT(*) FROM casos WHERE fecha_ocurrencia >= @ini_mes
               AND (id_cliente IS NULL OR tipo IS NULL OR tipo NOT IN ('leve', 'grave'))), 0,
           CASE WHEN EXISTS (SELECT 1 FROM casos WHERE fecha_ocurrencia >= @ini_mes
               AND (id_cliente IS NULL OR tipo IS NULL OR tipo NOT IN ('leve', 'grave'))) THEN 'FALLA' ELSE 'OK' END
    UNION ALL
    -- 7. Fechas futuras
    SELECT 'C07', 'Casos con fecha de ocurrencia futura', 'ADVERTENCIA',
           (SELECT COUNT(*) FROM casos WHERE fecha_ocurrencia > @fecha_corte), 0,
           CASE WHEN EXISTS (SELECT 1 FROM casos WHERE fecha_ocurrencia > @fecha_corte) THEN 'FALLA' ELSE 'OK' END
    UNION ALL
    -- 8. Valores negativos
    SELECT 'C08', 'Casos del mes con costo o días de ausencia negativos', 'ADVERTENCIA',
           (SELECT COUNT(*) FROM casos WHERE fecha_ocurrencia >= @ini_mes AND (costo < 0 OR dias_ausencia < 0)), 0,
           CASE WHEN EXISTS (SELECT 1 FROM casos WHERE fecha_ocurrencia >= @ini_mes
               AND (costo < 0 OR dias_ausencia < 0)) THEN 'FALLA' ELSE 'OK' END
    UNION ALL
    -- 9. Duplicados de caso en la fuente
    SELECT 'C09', 'id_caso duplicados', 'BLOQUEANTE',
           (SELECT COUNT(*) - COUNT(DISTINCT id_caso) FROM casos), 0,
           CASE WHEN (SELECT COUNT(*) - COUNT(DISTINCT id_caso) FROM casos) = 0 THEN 'OK' ELSE 'FALLA' END
    UNION ALL
    -- 10. Denominador disponible: pocos clientes sin trabajadores
    SELECT 'C10', '% clientes sin trabajadores (tasa sin dato) <= 5%', 'ADVERTENCIA',
           (SELECT 100.0 * SUM(CASE WHEN clasificacion = 'sin dato' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0) FROM hoy), 5,
           CASE WHEN (SELECT 100.0 * SUM(CASE WHEN clasificacion = 'sin dato' THEN 1 ELSE 0 END)
                             / NULLIF(COUNT(*), 0) FROM hoy) <= 5 THEN 'OK' ELSE 'FALLA' END
    UNION ALL
    -- 11. Plausibilidad: tasas absurdas suelen indicar un denominador mal cargado
    SELECT 'C11', 'Clientes con tasa de incidencia > 50', 'ADVERTENCIA',
           (SELECT COUNT(*) FROM hoy WHERE tasa_incidencia > 50), 0,
           CASE WHEN EXISTS (SELECT 1 FROM hoy WHERE tasa_incidencia > 50) THEN 'FALLA' ELSE 'OK' END
    UNION ALL
    -- 12. Salto de volumen: el total de clientes 'crítico' no se multiplica de un día a otro
    SELECT 'C12', 'Clientes críticos no crecen más de 2x vs ayer', 'ADVERTENCIA',
           (SELECT COUNT(*) FROM hoy WHERE clasificacion = 'crítico'),
           (SELECT COUNT(*) FROM ayer WHERE clasificacion = 'crítico'),
           CASE WHEN (SELECT COUNT(*) FROM hoy WHERE clasificacion = 'crítico')
                     <= 2 * (SELECT COUNT(*) FROM ayer WHERE clasificacion = 'crítico') + 3 THEN 'OK' ELSE 'FALLA' END
)
SELECT @fecha_corte AS fecha_corte, controles.*, SYSDATETIME() AS fecha_ejecucion
FROM controles
ORDER BY CASE severidad WHEN 'BLOQUEANTE' THEN 0 ELSE 1 END, control;
