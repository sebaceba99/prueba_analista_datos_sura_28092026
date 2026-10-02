/* Consulta original  (Sección 2.1), sin cambios, para referencia.
  
  ¿ Para que sirve ?
  
  La consulta toma a todos los clientes activos que tuvieron al menos un incidente en el último año y, para cada uno,
  cuenta cuántos casos tuvo, cuántos fueron graves, cuánto costaron en total y cuántos trabajadores tiene en promedio.
  Con eso calcula qué porcentaje de sus casos fueron graves y cuánto cuestan los casos por cada trabajador. 
  
  Al final ordena a los clientes de mayor a menor costo y les pone una posición dentro de su nivel de riesgo, para responder
  preguntas como “entre los clientes de riesgo 5, ¿cuál nos cuesta más?”. Sirve para priorizar la atención y para conversaciones
  comerciales o de renovación de contrato con los clientes más costosos. 
  
  Errores:

  El principal error de la consulta es que cruza dos tablas de hechos antes de hacer el groupby, lo que puede ocasionar que se multiplquen
  el número de filas de la tabla ocasionando así cálculos inconsistentes. La solución es agrupar cada tabla para que queden todas en el mismo
  nivel de granularidad antes de cruzarlas, por otra parte la columna "trabajadores_activos" es un número entero, por lo cual debemos hacer
  casteo al promedio.  
  
  
   */
WITH base AS (
    SELECT
        c.id_cliente, c.nombre, c.clase_riesgo,
        COUNT(ca.id_caso)                                AS total_casos,
        SUM(CASE WHEN ca.dias_ausencia > 15
                 THEN 1 ELSE 0 END)                      AS casos_graves,
        SUM(ca.costo)                                    AS costo_total,
        AVG(f.trabajadores_activos)                      AS trabajadores_prom
    FROM clientes c
    LEFT JOIN casos ca ON c.id_cliente = ca.id_cliente
        AND ca.fecha_ocurrencia >= DATEADD(month,-12,CURRENT_DATE)
    LEFT JOIN facturacion f ON c.id_cliente = f.id_cliente
        AND f.periodo >= DATEADD(month,-6,CURRENT_DATE)
    WHERE c.estado = 'activo'
    GROUP BY c.id_cliente, c.nombre, c.clase_riesgo
)
SELECT *,
    ROUND(casos_graves*100.0/NULLIF(total_casos,0),1)   AS pct_graves,
    ROUND(costo_total/NULLIF(trabajadores_prom,0),0)    AS costo_x_trab,
    DENSE_RANK() OVER (PARTITION BY clase_riesgo
                       ORDER BY costo_total DESC)       AS ranking_clase
FROM base WHERE total_casos > 0 ORDER BY costo_total DESC;


