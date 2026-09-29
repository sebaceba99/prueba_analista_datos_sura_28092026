# Capa de datos del tablero (Sección 4)
#
# Todo lo que el tablero muestra se calcula aquí, no en la interfaz, para que
# cada indicador tenga una sola definición y se pueda revisar sin abrir el
# tablero. Hoy los datos se generan en el código; en producción solo cambia
# generar_datos() por una lectura del Lakehouse (ver README).

# %% Librerías
import numpy as np
import pandas as pd

# %% Metas y reglas de negocio
# Metas de la organización (valores de ejemplo para la prueba).
# Todas se expresan "por trabajador" para que sigan siendo válidas al filtrar.
METAS = {
    # Tasa máxima (casos por cada 100 trabajadores al mes) según la clase de riesgo del cliente.
    # Comparar un cliente de minería y uno de servicios contra la misma meta no es justo:
    # la meta se ajusta por riesgo. La meta de un grupo de clientes es el promedio
    # ponderado por trabajadores, así sigue siendo válida con cualquier filtro.
    "tasa_por_clase": {1: 0.25, 2: 0.30, 3: 0.65, 4: 0.95, 5: 1.25},
    "costo_por_trabajador": 22_000,  # presupuesto mensual de costo de casos por trabajador, COP (máximo)
    "cobertura_prevencion": 0.90,    # % de clientes de riesgo alto (clase 4-5) con prevención en el mes (mínimo)
}

# Semáforo por % de cumplimiento de la meta
UMBRAL_VERDE = 1.00     # 100% o más  -> verde (en meta)
UMBRAL_AMARILLO = 0.80  # 80% a 99%   -> amarillo (cerca de la meta)
                        # menos de 80% -> rojo (fuera de meta)

CLASES_RIESGO_ALTO = [4, 5]

SECTORES = {
    # sector: (clases de riesgo típicas, qué tanto se accidenta frente al promedio)
    "Construcción": ([4, 5], 1.6),
    "Minería": ([5], 1.8),
    "Energía": ([4, 5], 1.3),
    "Manufactura": ([3, 4], 1.2),
    "Transporte": ([3, 4], 1.1),
    "Salud": ([2, 3], 0.8),
    "Comercio": ([1, 2], 0.6),
    "Servicios": ([1, 2], 0.5),
}
N_COORDINADORES = 12
MESES_HISTORIA = 24


# %% 1. Generación de datos
def generar_datos(hoy, n_clientes=180, semilla=7):
    """
    Genera 24 meses de historia hasta el último mes cerrado.
    Devuelve un diccionario de DataFrames: clientes, facturacion, casos, prevencion.
    """
    rng = np.random.default_rng(semilla)
    ultimo_mes = pd.Timestamp(hoy).to_period("M").to_timestamp() - pd.DateOffset(months=1)
    meses = pd.date_range(end=ultimo_mes, periods=MESES_HISTORIA, freq="MS")

    # ---- Clientes ----
    sectores = rng.choice(list(SECTORES), size=n_clientes, p=[0.14, 0.05, 0.08, 0.18, 0.12, 0.10, 0.15, 0.18])
    clientes = pd.DataFrame({
        "id_cliente": [f"CLI-{i:04d}" for i in range(1, n_clientes + 1)],
        "nombre": [f"Cliente {i:03d}" for i in range(1, n_clientes + 1)],
        "sector": sectores,
        "clase_riesgo": [int(rng.choice(SECTORES[s][0])) for s in sectores],
        "coordinador": [f"Coordinador {c:02d}" for c in rng.integers(1, N_COORDINADORES + 1, n_clientes)],
        "trabajadores_base": np.clip(rng.lognormal(4.6, 0.9, n_clientes), 15, 4000).astype(int),
        "crecimiento": rng.normal(0.004, 0.006, n_clientes),       # variación mensual de trabajadores
        "deterioro": rng.choice([1.0, 1.0, 1.0, 1.6], n_clientes),  # 1 de cada 4 clientes empeora en los últimos meses
    })
    clientes["peso_sector"] = clientes["sector"].map(lambda s: SECTORES[s][1])

    # ---- Facturación: trabajadores activos por cliente y mes ----
    fact = clientes.merge(pd.DataFrame({"periodo": meses, "n_mes": range(len(meses))}), how="cross")
    fact["trabajadores_activos"] = (fact["trabajadores_base"] * (1 + fact["crecimiento"] * fact["n_mes"])
                                    * rng.normal(1, 0.02, len(fact))).round().clip(lower=1).astype(int)
    fact["valor_contrato"] = fact["trabajadores_activos"] * 52_000

    # ---- Casos: cuántos por cliente y mes (Poisson) ----
    tasa_base = 0.0045 * (0.55 + 0.25 * fact["clase_riesgo"]) * fact["peso_sector"]  # casos por trabajador al mes
    estacional = 1 + 0.12 * np.sin(2 * np.pi * (fact["periodo"].dt.month - 3) / 12)
    meses_recientes = (fact["n_mes"] - (MESES_HISTORIA - 6)).clip(lower=0) / 6  # 0 -> 1 en los últimos 6 meses
    empeora = 1 + (fact["deterioro"] - 1) * meses_recientes
    fact["n_casos"] = rng.poisson(fact["trabajadores_activos"] * tasa_base * estacional * empeora)

    casos = fact.loc[fact.index.repeat(fact["n_casos"]), ["id_cliente", "periodo", "clase_riesgo"]].reset_index(drop=True)
    grave = rng.random(len(casos)) < 0.06 + 0.035 * casos["clase_riesgo"]
    casos["tipo"] = np.where(grave, "grave", "leve")
    casos["dias_ausencia"] = np.where(grave, rng.integers(16, 120, len(casos)), rng.integers(1, 16, len(casos)))
    casos["costo"] = (casos["dias_ausencia"] * rng.uniform(95_000, 140_000, len(casos)) + np.where(grave, 2_500_000, 0)).round()
    casos["fecha_ocurrencia"] = casos["periodo"] + pd.to_timedelta(
        (rng.random(len(casos)) * casos["periodo"].dt.days_in_month).astype(int), unit="D")
    casos.insert(0, "id_caso", range(1, len(casos) + 1))
    casos = casos.drop(columns="clase_riesgo")

    # ---- Prevención: actividades por cliente y mes (más frecuentes en riesgo alto) ----
    # Los clientes que empeoran son también los que dejaron de recibir prevención en los últimos meses
    prob = fact["clase_riesgo"].map({1: 0.30, 2: 0.35, 3: 0.50, 4: 0.74, 5: 0.78})
    prob = prob * np.where(fact["deterioro"] > 1, 1 - 0.7 * meses_recientes, 1)
    n_act = np.where(rng.random(len(fact)) < prob, rng.integers(1, 3, len(fact)), 0)
    prev = fact.loc[fact.index.repeat(n_act), ["id_cliente", "periodo"]].reset_index(drop=True)
    prev["fecha"] = prev["periodo"] + pd.to_timedelta(
        (rng.random(len(prev)) * prev["periodo"].dt.days_in_month).astype(int), unit="D")
    prev["tipo"] = rng.choice(["Capacitación", "Inspección", "Simulacro", "Asesoría"], len(prev))
    prev["participantes"] = rng.integers(5, 41, len(prev))
    prev.insert(0, "id_actividad", range(1, len(prev) + 1))

    return {
        "clientes": clientes[["id_cliente", "nombre", "sector", "clase_riesgo", "coordinador"]],
        "facturacion": fact[["id_cliente", "periodo", "trabajadores_activos", "valor_contrato"]],
        "casos": casos,
        "prevencion": prev,
        "fecha_corte": ultimo_mes + pd.offsets.MonthEnd(0),
    }


# %% 2. Filtros
def filtrar(datos, coordinador, sectores, clases):
    """Aplica los filtros de cliente a todas las tablas. coordinador = 'Todos' o un nombre."""
    cl = datos["clientes"]
    cl = cl[cl["sector"].isin(sectores) & cl["clase_riesgo"].isin(clases)]
    if coordinador != "Todos":
        cl = cl[cl["coordinador"] == coordinador]
    ids = cl["id_cliente"]
    return {
        "clientes": cl,
        "facturacion": datos["facturacion"][datos["facturacion"]["id_cliente"].isin(ids)],
        "casos": datos["casos"][datos["casos"]["id_cliente"].isin(ids)],
        "prevencion": datos["prevencion"][datos["prevencion"]["id_cliente"].isin(ids)],
        "fecha_corte": datos["fecha_corte"],
    }


# %% 3. Semáforo
def semaforo(real, meta, menor_es_mejor=True):
    """
    Devuelve (estado, cumplimiento).
    Cumplimiento = meta / real si menor es mejor (tasa, costo); real / meta si mayor es mejor (cobertura).
    """
    if pd.isna(real) or pd.isna(meta):
        return "sin dato", np.nan
    if menor_es_mejor:
        cumplimiento = 1.0 if real <= meta else meta / real
    else:
        cumplimiento = min(real / meta, 1.0)
    if cumplimiento >= UMBRAL_VERDE:
        return "verde", cumplimiento
    if cumplimiento >= UMBRAL_AMARILLO:
        return "amarillo", cumplimiento
    return "rojo", cumplimiento


# %% 4. Indicadores
def serie_mensual(datos):
    """
    Una fila por mes con: casos, graves, días de ausencia, costo, trabajadores,
    tasa de incidencia, presupuesto de costo y cobertura de prevención.
    Tasa = casos del mes / trabajadores activos del mes * 100 (nunca promedio de tasas).
    """
    meses = pd.date_range(end=datos["fecha_corte"].to_period("M").to_timestamp(), periods=MESES_HISTORIA, freq="MS")
    serie = pd.DataFrame({"periodo": meses})

    c = datos["casos"].groupby("periodo").agg(
        casos=("id_caso", "count"),
        graves=("tipo", lambda t: (t == "grave").sum()),
        dias_ausencia=("dias_ausencia", "sum"),
        costo=("costo", "sum"))
    f = datos["facturacion"].merge(datos["clientes"][["id_cliente", "clase_riesgo"]], on="id_cliente", validate="m:1")
    f["casos_meta"] = f["trabajadores_activos"] * f["clase_riesgo"].map(METAS["tasa_por_clase"]) / 100
    t = f.groupby("periodo").agg(trabajadores=("trabajadores_activos", "sum"), casos_meta=("casos_meta", "sum"))

    serie = serie.merge(c, on="periodo", how="left", validate="1:1").merge(t, on="periodo", how="left", validate="1:1")
    serie[["casos", "graves", "dias_ausencia", "costo"]] = serie[["casos", "graves", "dias_ausencia", "costo"]].fillna(0)
    serie["tasa"] = serie["casos"] / serie["trabajadores"] * 100
    serie["meta_tasa"] = serie["casos_meta"] / serie["trabajadores"] * 100  # meta ajustada por riesgo
    serie["presupuesto"] = serie["trabajadores"] * METAS["costo_por_trabajador"]

    # Cobertura de prevención: clientes de riesgo alto con al menos una actividad en el mes
    alto = datos["clientes"].loc[datos["clientes"]["clase_riesgo"].isin(CLASES_RIESGO_ALTO), "id_cliente"]
    con_prev = (datos["prevencion"][datos["prevencion"]["id_cliente"].isin(alto)]
                .groupby("periodo")["id_cliente"].nunique())
    serie["clientes_riesgo_alto"] = len(alto)
    serie["riesgo_alto_con_prevencion"] = serie["periodo"].map(con_prev).fillna(0)
    serie["cobertura"] = serie["riesgo_alto_con_prevencion"] / serie["clientes_riesgo_alto"].replace(0, np.nan)
    return serie


def comparar(serie, mes, columna):
    """Valor del mes, del mes anterior, variación % y promedio de los 12 meses previos."""
    actual = serie.loc[serie["periodo"] == mes, columna]
    anterior = serie.loc[serie["periodo"] == mes - pd.DateOffset(months=1), columna]
    previos = serie.loc[(serie["periodo"] < mes) & (serie["periodo"] >= mes - pd.DateOffset(months=12)), columna]

    a = actual.iloc[0] if len(actual) else np.nan
    p = anterior.iloc[0] if len(anterior) else np.nan
    variacion = (a - p) / p if p and not pd.isna(p) else np.nan  # sin mes anterior no hay variación
    return {"actual": a, "anterior": p, "variacion": variacion, "promedio_12m": previos.mean()}


def meses_seguidos(serie, mes):
    """Cuántos meses seguidos (hasta el mes analizado) lleva la tasa del mismo lado de su meta."""
    historia = serie[serie["periodo"] <= mes].sort_values("periodo", ascending=False)
    sobre_meta = (historia["tasa"] > historia["meta_tasa"]).tolist()
    if not sobre_meta:
        return 0, False
    n = 0
    for valor in sobre_meta:
        if valor != sobre_meta[0]:
            break
        n += 1
    return n, sobre_meta[0]


# %% 5. Detalle por cliente y por segmento
def resumen_clientes(datos, mes):
    """Una fila por cliente con los indicadores del mes, su semáforo y una acción sugerida."""
    fin_mes = mes + pd.offsets.MonthEnd(0)
    c = datos["casos"][datos["casos"]["periodo"] == mes].groupby("id_cliente").agg(
        casos=("id_caso", "count"),
        graves=("tipo", lambda t: (t == "grave").sum()),
        costo=("costo", "sum"))
    t = datos["facturacion"][datos["facturacion"]["periodo"] == mes].set_index("id_cliente")["trabajadores_activos"]
    p = datos["prevencion"][datos["prevencion"]["fecha"] <= fin_mes]
    ultima = p.groupby("id_cliente")["fecha"].max()
    prev_mes = p[p["periodo"] == mes].groupby("id_cliente").size()

    df = datos["clientes"].copy()
    df = df.merge(c, left_on="id_cliente", right_index=True, how="left", validate="1:1")
    df[["casos", "graves", "costo"]] = df[["casos", "graves", "costo"]].fillna(0)
    df["trabajadores"] = df["id_cliente"].map(t)
    df["tasa"] = df["casos"] / df["trabajadores"] * 100
    df["meta_tasa"] = df["clase_riesgo"].map(METAS["tasa_por_clase"])
    df["prevencion_mes"] = df["id_cliente"].map(prev_mes).fillna(0).astype(int)
    df["dias_sin_prevencion"] = (fin_mes - df["id_cliente"].map(ultima)).dt.days

    df["estado"] = [semaforo(v, m)[0] for v, m in zip(df["tasa"], df["meta_tasa"])]
    df["accion"] = df.apply(accion_sugerida, axis=1)
    return df


def accion_sugerida(fila):
    """Regla simple y explicable de qué hacer con cada cliente (se valida con las coordinaciones)."""
    riesgo_alto = fila["clase_riesgo"] in CLASES_RIESGO_ALTO
    if fila["estado"] == "rojo" and fila["prevencion_mes"] == 0:
        return "Agendar visita de prevención esta semana"
    if fila["graves"] > 0:
        return "Revisar casos graves con el cliente"
    if riesgo_alto and fila["prevencion_mes"] == 0:
        return "Programar prevención (riesgo alto sin actividad)"
    if fila["estado"] == "rojo":
        return "Seguimiento: tasa sobre la meta"
    return "Sin acción inmediata"


def _casos_y_meta(datos, desde, hasta, grupo):
    """Casos, trabajadores y casos esperados según la meta, agrupados por las columnas de 'grupo'."""
    cl = datos["clientes"][["id_cliente", "sector", "clase_riesgo"]]
    c = datos["casos"][datos["casos"]["periodo"].between(desde, hasta)].merge(cl, on="id_cliente", validate="m:1")
    f = datos["facturacion"][datos["facturacion"]["periodo"].between(desde, hasta)].merge(cl, on="id_cliente", validate="m:1")
    f["casos_meta"] = f["trabajadores_activos"] * f["clase_riesgo"].map(METAS["tasa_por_clase"]) / 100

    df = f.groupby(grupo).agg(trabajadores=("trabajadores_activos", "sum"), casos_meta=("casos_meta", "sum"))
    df["casos"] = c.groupby(grupo).size()
    df["casos"] = df["casos"].fillna(0)
    df["tasa"] = df["casos"] / df["trabajadores"] * 100
    df["meta_tasa"] = df["casos_meta"] / df["trabajadores"] * 100
    df["estado"] = [semaforo(v, m)[0] for v, m in zip(df["tasa"], df["meta_tasa"])]
    df["cumplimiento"] = [semaforo(v, m)[1] for v, m in zip(df["tasa"], df["meta_tasa"])]
    return df


def tasa_por_sector(datos, mes):
    """Tasa del mes por sector frente a su meta (ajustada por riesgo)."""
    df = _casos_y_meta(datos, mes, mes, ["sector"])
    return df.reset_index().sort_values("cumplimiento", ascending=False)


def tasa_sector_mes(datos, mes, n_meses=12):
    """Sector x mes: tasa, meta y estado, para ver si un problema es persistente o puntual."""
    desde = mes - pd.DateOffset(months=n_meses - 1)
    return _casos_y_meta(datos, desde, mes, ["sector", "periodo"]).reset_index()


def distribucion(datos, mes):
    """Casos del mes por clase de riesgo y tipo (leve / grave)."""
    c = datos["casos"][datos["casos"]["periodo"] == mes].merge(datos["clientes"], on="id_cliente", validate="m:1")
    tabla = (c.groupby(["clase_riesgo", "tipo"]).size().unstack(fill_value=0)
              .reindex(index=range(1, 6), columns=["leve", "grave"], fill_value=0))
    return tabla.reset_index()


def serie_cliente(datos, id_cliente):
    """Serie mensual de un solo cliente (para su ficha)."""
    uno = {k: (v[v["id_cliente"] == id_cliente] if isinstance(v, pd.DataFrame) else v) for k, v in datos.items()}
    return serie_mensual(uno)
