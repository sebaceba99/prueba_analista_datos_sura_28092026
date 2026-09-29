# Tablero de monitoreo operativo de casos (Sección 4)
#
# Público: coordinadores de cuenta (y su jefatura), que lo abren el lunes para
# decidir qué clientes atender en la semana.
#
# Estructura (Shneiderman: "primero la vista general, luego filtrar y hacer zoom,
# y el detalle a demanda"):
#   1. Resumen     -> ¿vamos bien o mal y dónde hay que mirar?
#   2. Diagnóstico -> ¿por qué pasa y dónde se concentra?     (se llega haciendo clic en un sector)
#   3. Cliente     -> ¿qué hago con este cliente?              (se llega seleccionando un cliente)
#
# Ejecutar:  streamlit run app.py

# %% Librerías
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import datos as D

# %% Colores
# El color comunica, no decora. El semáforo se usa SOLO para el estado frente a la meta
# y siempre va con ícono y texto (nunca el color solo), para personas con daltonismo.
ESTADOS = {
    "verde":    {"color": "#0ca30c", "icono": "✓", "texto": "En meta",          "simbolo": "circle"},
    "amarillo": {"color": "#fab219", "icono": "!", "texto": "Cerca de la meta", "simbolo": "diamond"},
    "rojo":     {"color": "#d03b3b", "icono": "✕", "texto": "Fuera de meta",    "simbolo": "x"},
    "sin dato": {"color": "#898781", "icono": "–", "texto": "Sin dato",         "simbolo": "circle-open"},
}
AZUL = "#2a78d6"                          # la serie que se analiza (tasa)
LEVE, GRAVE = "#86b6ef", "#1c5cab"        # misma familia de color: más oscuro = más severo
GRIS_META, GRIS_PROMEDIO = "#52514e", "#898781"
GRILLA = "rgba(128,128,128,0.15)"

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

AYUDA = {
    "tasa": "Casos del mes por cada 100 trabajadores activos. Permite comparar clientes de distinto tamaño. "
            "La meta se ajusta por clase de riesgo: no se exige lo mismo a una mina que a una oficina.",
    "costo": "Suma del costo de los casos del mes (COP) frente al presupuesto: trabajadores activos × "
             f"${D.METAS['costo_por_trabajador']:,} por trabajador al mes.".replace(",", "."),
    "cobertura": "Porcentaje de clientes de riesgo alto (clase 4 y 5) que tuvieron al menos una actividad de "
                 "prevención en el mes. Es un indicador que anticipa: si baja, la tasa suele subir después.",
}

CTX = {}       # datos filtrados que comparten las tres páginas
PAGINAS = {}   # para poder navegar entre páginas desde un clic


# %% Formato (colombiano: punto de miles, coma decimal)
def fmt(valor, decimales=0):
    if pd.isna(valor):
        return "–"
    return f"{valor:,.{decimales}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def pesos(valor):
    return "–" if pd.isna(valor) else f"$ {fmt(valor / 1e6, 1)} M"


def pct(valor, decimales=0):
    return "–" if pd.isna(valor) else f"{fmt(valor * 100, decimales)}%"


def pct_cumplimiento(valor):
    # hacia abajo: 99,97% se muestra como 99%, no como un engañoso 100%
    return "–" if pd.isna(valor) else f"{int(np.floor(valor * 100))}%"


def mes_largo(ts):
    return f"{MESES[ts.month - 1]} {ts.year}"


def mes_corto(ts):
    return f"{MESES[ts.month - 1][:3]}-{str(ts.year)[2:]}"


def insignia(estado, detalle=""):
    """Etiqueta de estado: ícono + texto + color (nunca solo color)."""
    e = ESTADOS[estado]
    extra = f" · {detalle}" if detalle else ""
    return (f"<span style='background:{e['color']}26; border:1px solid {e['color']}; border-radius:12px; "
            f"padding:2px 10px; font-size:0.85rem; white-space:nowrap'>"
            f"<b style='color:{e['color']}'>{e['icono']}</b> {e['texto']}{extra}</span>")


def variacion_md(variacion, etiqueta, menor_es_mejor=True):
    """Variación con flecha y color: rojo si empeora, verde si mejora."""
    if pd.isna(variacion):
        return f"Sin dato de {etiqueta}"
    empeora = variacion > 0 if menor_es_mejor else variacion < 0
    flecha = "▲" if variacion > 0 else "▼"
    color = "red" if empeora else "green"
    palabra = "peor" if empeora else "mejor"
    return f":{color}[{flecha} {pct(abs(variacion), 1)} {palabra}] que {etiqueta}"


# %% Navegación con "obtención de detalles"
def ir_a(pagina, limpiar=None, **filtros):
    """Guarda los filtros que debe aplicar la página destino y navega hacia ella."""
    if limpiar and limpiar in st.session_state:
        del st.session_state[limpiar]  # para que la selección no se dispare otra vez al volver
    st.session_state["drill"] = filtros
    st.switch_page(PAGINAS[pagina])


def aplicar_drill():
    """Aplica los filtros de un clic antes de dibujar los controles."""
    for clave, valor in st.session_state.pop("drill", {}).items():
        st.session_state[clave] = valor


# %% Carga de datos (se genera una vez al día)
@st.cache_data(show_spinner="Cargando datos…")
def cargar(dia):
    return D.generar_datos(pd.Timestamp(dia))


# %% Filtros: siempre en la barra lateral, los mismos para las tres páginas
def filtros(datos):
    cl = datos["clientes"]
    meses = sorted(datos["facturacion"]["periodo"].unique(), reverse=True)[:12]
    sectores = sorted(cl["sector"].unique())
    coordinadores = ["Todos"] + sorted(cl["coordinador"].unique())

    valores_iniciales = {"f_mes": meses[0], "f_coord": "Todos", "f_sector": sectores, "f_clase": [1, 2, 3, 4, 5]}
    for clave, valor in valores_iniciales.items():
        st.session_state.setdefault(clave, valor)

    st.sidebar.header("Filtros")
    st.sidebar.selectbox("Mes a analizar", meses, key="f_mes", format_func=lambda m: mes_largo(pd.Timestamp(m)),
                         help="Por defecto, el último mes cerrado.")
    st.sidebar.selectbox("Coordinador", coordinadores, key="f_coord",
                         help="Elija su nombre para ver solo sus clientes.")
    st.sidebar.multiselect("Sector", sectores, key="f_sector")
    st.sidebar.multiselect("Clase de riesgo", [1, 2, 3, 4, 5], key="f_clase",
                           format_func=lambda c: f"Clase {c}", help="1 = riesgo más bajo, 5 = riesgo más alto.")

    if st.sidebar.button("Restablecer filtros", width="stretch"):
        for clave in list(valores_iniciales) + ["solo_sin_prevencion"]:
            st.session_state.pop(clave, None)
        st.rerun()

    st.sidebar.divider()
    st.sidebar.caption(
        "**Semáforo** (cumplimiento de la meta): ✓ verde 100% o más · ! amarillo 80–99% · ✕ rojo menos de 80%.")
    st.sidebar.caption("Datos simulados para la prueba técnica. En producción se leen del Lakehouse cada día.")
    return pd.Timestamp(st.session_state["f_mes"])


def encabezado(pregunta, mes):
    st.title(pregunta)
    filtro_coord = "" if st.session_state["f_coord"] == "Todos" else f" · {st.session_state['f_coord']}"
    st.caption(f"Mes analizado: **{mes_largo(mes)}**{filtro_coord} · Datos con corte al "
               f"{CTX['datos']['fecha_corte'].strftime('%d/%m/%Y')} · Cifras en pesos colombianos")

    # Si hay filtros de sector o clase, se avisa: los números no son de toda la cartera
    activos = []
    if len(st.session_state["f_sector"]) < len(D.SECTORES):
        activos.append("Sector: " + ", ".join(st.session_state["f_sector"]))
    if len(st.session_state["f_clase"]) < 5:
        activos.append("Clase de riesgo: " + ", ".join(map(str, st.session_state["f_clase"])))
    if activos:
        st.info("Filtros aplicados: " + " · ".join(activos) + ". Use **Restablecer filtros** para ver toda la cartera.")


def hay_datos():
    if CTX["datos"]["clientes"].empty:
        st.info("No hay clientes con esa combinación de filtros. Amplíelos en la barra lateral.")
        return False
    return True


# %% PÁGINA 1 — Resumen: ¿vamos bien o mal y dónde hay que mirar?
def pagina_resumen():
    mes, d, serie = CTX["mes"], CTX["datos"], CTX["serie"]
    encabezado("¿Vamos bien o mal y dónde hay que mirar?", mes)
    if not hay_datos():
        return

    with st.expander("¿Cómo leer este tablero?"):
        st.markdown(
            "- **Se lee de arriba hacia abajo:** primero *cómo vamos*, luego *si es puntual o tendencia* y al final "
            "*a quién atender*.\n"
            "- **Semáforo frente a la meta:** ✓ verde = en meta · ! amarillo = 80–99% de cumplimiento · "
            "✕ rojo = menos de 80%. Las flechas dicen si el indicador mejoró o empeoró frente al mes anterior.\n"
            "- **La meta de tasa depende del riesgo:** a un cliente de clase 5 se le permite más que a uno de clase 1.\n"
            "- **Para profundizar:** haga clic en un sector para ver su diagnóstico, o seleccione un cliente en la "
            "tabla para ver su ficha.\n"
            "- Pase el cursor sobre el ícono ⓘ de cada indicador para ver su definición.")

    sectores = D.tasa_por_sector(d, mes)
    clientes = D.resumen_clientes(d, mes)

    mensaje_clave(serie, mes, sectores)
    fila_kpis(serie, mes)
    fila_metricas(serie, mes, clientes)
    st.write("")

    izq, der = st.columns([7, 5], gap="large")
    with izq:
        grafico_tendencia(serie, mes)
    with der:
        grafico_sectores(sectores)

    st.divider()
    tabla_top_clientes(clientes)


def mensaje_clave(serie, mes, sectores):
    """La idea principal del mes, escrita automáticamente, en 3 frases que terminan en una acción."""
    fila = serie[serie["periodo"] == mes].iloc[0]
    k = D.comparar(serie, mes, "tasa")
    estado, cumplimiento = D.semaforo(fila["tasa"], fila["meta_tasa"])
    n, sobre_meta = D.meses_seguidos(serie, mes)
    e = ESTADOS[estado]

    frase1 = (f"En {mes_largo(mes)} la tasa de incidencia fue <b>{fmt(fila['tasa'], 2)}</b> frente a una meta de "
              f"{fmt(fila['meta_tasa'], 2)}: <b style='color:{e['color']}'>{e['icono']} {e['texto'].lower()}</b> "
              f"({pct_cumplimiento(cumplimiento)} de cumplimiento).")
    lado = "por encima de" if sobre_meta else "dentro de"
    cambio = ""
    if not pd.isna(k["variacion"]):
        cambio = f" y {'empeoró' if k['variacion'] > 0 else 'mejoró'} {pct(abs(k['variacion']), 1)} frente al mes anterior"
    frase2 = f"Lleva {n} {'mes' if n == 1 else 'meses'} seguidos {lado} la meta{cambio}."

    rojos = sectores[sectores["estado"] == "rojo"].sort_values("cumplimiento")["sector"].tolist()
    if len(rojos) > 1:
        frase3 = f"Los sectores más lejos de su meta son <b>{rojos[0]}</b> y <b>{rojos[1]}</b>."
    elif len(rojos) == 1:
        frase3 = f"El sector más lejos de su meta es <b>{rojos[0]}</b>."
    else:
        frase3 = "Ningún sector está fuera de meta."

    frase4 = ""
    if fila["clientes_riesgo_alto"] > 0:
        faltan = int(fila["clientes_riesgo_alto"] - fila["riesgo_alto_con_prevencion"])
        if faltan > 0:
            frase4 = (f" <b>{faltan} de {int(fila['clientes_riesgo_alto'])} clientes de riesgo alto no tuvieron "
                      f"prevención en el mes</b>: son la prioridad de visitas de esta semana.")

    st.markdown(
        f"<div style='border-left:6px solid {e['color']}; background:{e['color']}14; padding:12px 16px; "
        f"border-radius:4px; font-size:1.05rem; line-height:1.6'>"
        f"<div style='font-size:0.8rem; text-transform:uppercase; letter-spacing:0.05em; opacity:0.7'>"
        f"Mensaje clave del mes</div>{frase1} {frase2} {frase3}{frase4}</div>",
        unsafe_allow_html=True)
    st.write("")


def tarjeta_kpi(titulo, ayuda, valor, estado, cumplimiento, meta_txt, variacion, promedio_txt, menor_es_mejor=True):
    """Tarjeta de KPI: valor + semáforo frente a la meta + comparación con el mes anterior y el promedio."""
    with st.container(border=True):
        st.markdown(f"**{titulo}**", help=ayuda)
        st.markdown(f"<div style='font-size:2.1rem; font-weight:600; line-height:1.2'>{valor}</div>",
                    unsafe_allow_html=True)
        st.markdown(insignia(estado, f"{pct_cumplimiento(cumplimiento)} de la meta"), unsafe_allow_html=True)
        st.caption(f"{meta_txt}  \n{promedio_txt}".replace("$", "\\$"))  # se escapa el signo de pesos para que no se lea como fórmula
        st.markdown(variacion_md(variacion, "el mes anterior", menor_es_mejor))


def fila_kpis(serie, mes):
    """Los 3 KPI: tienen meta, dueño y, si se mueven, alguien hace algo distinto."""
    fila = serie[serie["periodo"] == mes].iloc[0]
    tasa, costo, cob = D.comparar(serie, mes, "tasa"), D.comparar(serie, mes, "costo"), D.comparar(serie, mes, "cobertura")
    meta_cob = D.METAS["cobertura_prevencion"]

    c1, c2, c3 = st.columns(3)
    with c1:  # KPI principal a la izquierda: es lo primero que se lee (patrón en F / Z)
        estado, cumpl = D.semaforo(fila["tasa"], fila["meta_tasa"])
        tarjeta_kpi("Tasa de incidencia", AYUDA["tasa"], fmt(fila["tasa"], 2), estado, cumpl,
                    f"Meta ≤ {fmt(fila['meta_tasa'], 2)}", tasa["variacion"],
                    f"Promedio 12 meses {fmt(tasa['promedio_12m'], 2)}")
    with c2:
        estado, cumpl = D.semaforo(fila["costo"], fila["presupuesto"])
        tarjeta_kpi("Costo total de casos", AYUDA["costo"], pesos(fila["costo"]), estado, cumpl,
                    f"Presupuesto {pesos(fila['presupuesto'])}", costo["variacion"],
                    f"Promedio 12 meses {pesos(costo['promedio_12m'])}")
    with c3:
        estado, cumpl = D.semaforo(fila["cobertura"], meta_cob, menor_es_mejor=False)
        tarjeta_kpi("Cobertura de prevención", AYUDA["cobertura"],
                    f"{pct(fila['cobertura'])} <span style='font-size:1rem; font-weight:400'>"
                    f"({int(fila['riesgo_alto_con_prevencion'])} de {int(fila['clientes_riesgo_alto'])})</span>",
                    estado, cumpl, f"Meta ≥ {pct(meta_cob)}", cob["variacion"],
                    f"Promedio 12 meses {pct(cob['promedio_12m'])}", menor_es_mejor=False)
        if st.button("Ver clientes de riesgo alto sin prevención →", width="stretch"):
            ir_a("diagnostico", solo_sin_prevencion=True, f_clase=D.CLASES_RIESGO_ALTO)


def fila_metricas(serie, mes, clientes):
    """Métricas de apoyo: dan contexto, pero no tienen meta propia (la meta está en la tasa)."""
    casos, graves = D.comparar(serie, mes, "casos"), D.comparar(serie, mes, "graves")
    dias = D.comparar(serie, mes, "dias_ausencia")
    fuera = (clientes["estado"] == "rojo").sum()

    def delta(v):
        return None if pd.isna(v) else f"{pct(v, 1)} vs mes anterior"

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Casos del mes", fmt(casos["actual"]), delta(casos["variacion"]), delta_color="inverse",
              help="Número de casos (leves y graves) ocurridos en el mes.")
    m2.metric("Casos graves", fmt(graves["actual"]), delta(graves["variacion"]), delta_color="inverse",
              help="Casos con más de 15 días de ausencia o clasificados como graves.")
    m3.metric("Días de ausencia", fmt(dias["actual"]), delta(dias["variacion"]), delta_color="inverse",
              help="Suma de días de incapacidad de los casos del mes.")
    m4.metric("Clientes fuera de meta", f"{fuera} de {len(clientes)}",
              help="Clientes cuya tasa del mes está por debajo del 80% de cumplimiento de su meta.")


def grafico_tendencia(serie, mes):
    """¿Es puntual o es tendencia? 12 meses frente a la meta y al promedio histórico."""
    historia = serie[serie["periodo"] <= mes]
    ult12 = historia.tail(12).copy()
    promedio = historia["tasa"].mean()
    n, sobre_meta = D.meses_seguidos(serie, mes)

    titulo = (f"La tasa lleva {n} {'mes' if n == 1 else 'meses'} seguidos por encima de su meta" if sobre_meta
              else f"La tasa lleva {n} {'mes' if n == 1 else 'meses'} dentro de su meta")
    st.subheader(titulo)
    st.caption("Casos por cada 100 trabajadores, últimos 12 meses. Cada punto lleva el color y la forma "
               "de su estado (✓ círculo, ! rombo, ✕ equis).")

    ult12["estado"] = [D.semaforo(t, m)[0] for t, m in zip(ult12["tasa"], ult12["meta_tasa"])]
    x = [f"{MESES[p.month - 1][:3]}<br>{p.year}" for p in ult12["periodo"]]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=ult12["meta_tasa"], mode="lines", name="Meta",
                             line=dict(color=GRIS_META, width=2, dash="dash"), hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=x, y=[promedio] * len(x), mode="lines", name="Promedio histórico",
                             line=dict(color=GRIS_PROMEDIO, width=1.5, dash="dot"), hoverinfo="skip"))
    fig.add_trace(go.Scatter(
        x=x, y=ult12["tasa"], mode="lines+markers", name="Tasa de incidencia",
        line=dict(color=AZUL, width=2),
        marker=dict(size=11, color=[ESTADOS[e]["color"] for e in ult12["estado"]],
                    symbol=[ESTADOS[e]["simbolo"] for e in ult12["estado"]], line=dict(color="white", width=1)),
        customdata=[[fmt(t, 2), fmt(m, 2), ESTADOS[e]["icono"] + " " + ESTADOS[e]["texto"]]
                    for t, m, e in zip(ult12["tasa"], ult12["meta_tasa"], ult12["estado"])],
        hovertemplate="%{x}: <b>%{customdata[0]}</b> (meta %{customdata[1]})<br>%{customdata[2]}<extra></extra>"))

    # Etiquetas directas al final de cada línea (en lugar de obligar a buscar en la leyenda)
    meta_final = ult12["meta_tasa"].iloc[-1]
    arriba = 8 if meta_final >= promedio else -8  # si las dos líneas quedan juntas, las etiquetas se separan
    for y, texto, color, desplazamiento in [(meta_final, f"Meta {fmt(meta_final, 2)}", GRIS_META, arriba),
                                            (promedio, f"Promedio {fmt(promedio, 2)}", GRIS_PROMEDIO, -arriba)]:
        fig.add_annotation(x=x[-1], y=y, text=texto, showarrow=False, xanchor="left", xshift=12,
                           yshift=desplazamiento, font=dict(color=color, size=11))

    fig.update_layout(height=340, margin=dict(l=10, r=90, t=10, b=10), showlegend=False, hovermode="closest",
                      yaxis=dict(gridcolor=GRILLA, title=None), xaxis=dict(showgrid=False, tickangle=0))
    st.plotly_chart(fig, config={"displayModeBar": False})


def grafico_sectores(sectores):
    """¿Dónde mirar? Gráfico de bala (Few): barra = tasa del sector, marca = su meta."""
    peor = sectores.sort_values("cumplimiento").iloc[0]
    if peor["estado"] == "rojo":
        st.subheader(f"{peor['sector']} es el sector más lejos de su meta")
    else:
        st.subheader("Ningún sector está fuera de su meta")
    st.caption("Barra = tasa del mes · marca vertical = meta del sector. **Haga clic en una barra** para ver su diagnóstico.")

    s = sectores.sort_values("cumplimiento", ascending=False)  # el peor queda arriba
    colores = [ESTADOS[e]["color"] for e in s["estado"]]
    textos = [f"{ESTADOS[e]['icono']} {fmt(t, 2)}" for e, t in zip(s["estado"], s["tasa"])]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=s["sector"], x=s["tasa"], orientation="h", marker=dict(color=colores), name="Tasa",
        customdata=[[fmt(m, 2), pct_cumplimiento(c), int(n)] for m, c, n in zip(s["meta_tasa"], s["cumplimiento"], s["casos"])],
        hovertemplate="<b>%{y}</b><br>Tasa %{x:.2f} · meta %{customdata[0]}<br>"
                      "Cumplimiento %{customdata[1]} · %{customdata[2]} casos<extra></extra>"))
    fig.add_trace(go.Scatter(
        y=s["sector"], x=s["meta_tasa"], mode="markers", name="Meta", hoverinfo="skip",
        marker=dict(symbol="line-ns", size=22, line=dict(width=3, color=GRIS_META))))
    # Etiqueta a la derecha de la barra o de la meta (la que llegue más lejos), para que no se tapen
    fig.add_trace(go.Scatter(
        y=s["sector"], x=np.maximum(s["tasa"], s["meta_tasa"]), mode="text", text=textos,
        textposition="middle right", hoverinfo="skip", cliponaxis=False))
    fig.update_layout(height=340, margin=dict(l=10, r=40, t=10, b=10), showlegend=False, bargap=0.35,
                      xaxis=dict(gridcolor=GRILLA, rangemode="tozero"), yaxis=dict(title=None))

    evento = st.plotly_chart(fig, key="clic_sector", on_select="rerun", selection_mode="points",
                             config={"displayModeBar": False})
    puntos = evento.selection.points if evento and evento.selection else []
    if puntos:
        ir_a("diagnostico", limpiar="clic_sector", f_sector=[puntos[0]["y"]])


def tabla_clientes(clientes, clave):
    """Tabla de clientes con semáforo. Devuelve el id del cliente seleccionado (o None)."""
    tabla = pd.DataFrame({
        "Estado": [f"{ESTADOS[e]['icono']} {ESTADOS[e]['texto']}" for e in clientes["estado"]],
        "Cliente": clientes["nombre"],
        "Coordinador": clientes["coordinador"],
        "Sector": clientes["sector"],
        "Clase de riesgo": [f"{c} " + "●" * c + "○" * (5 - c) for c in clientes["clase_riesgo"]],
        "Casos": clientes["casos"].astype(int),
        "Graves": clientes["graves"].astype(int),
        "Tasa": clientes["tasa"].round(2),
        "Meta": clientes["meta_tasa"],
        "Días sin prevención": clientes["dias_sin_prevencion"],
        "Acción sugerida": clientes["accion"],
    })

    def color_estado(texto):
        for e in ESTADOS.values():
            if texto.startswith(e["icono"]):
                return f"background-color: {e['color']}33"
        return ""

    evento = st.dataframe(
        tabla.style.map(color_estado, subset=["Estado"]), hide_index=True, key=clave,
        on_select="rerun", selection_mode="single-row",
        column_config={
            "Casos": st.column_config.ProgressColumn("Casos", format="%d", min_value=0,
                                                     max_value=max(1, int(tabla["Casos"].max()))),
            "Tasa": st.column_config.NumberColumn(format="%.2f", help="Casos por cada 100 trabajadores"),
            "Meta": st.column_config.NumberColumn(format="%.2f", help="Meta de tasa según la clase de riesgo"),
            "Días sin prevención": st.column_config.NumberColumn(
                format="%d", help="Días desde la última actividad de prevención al cierre del mes (vacío = nunca)"),
        })
    filas = evento.selection.rows if evento and evento.selection else []
    return clientes.iloc[filas[0]]["id_cliente"] if filas else None


def tabla_top_clientes(clientes):
    """¿Con qué clientes hablar primero? Los 10 con más casos, con su clase de riesgo y la acción sugerida."""
    st.subheader("¿Con qué clientes hablar primero?")
    top = clientes.sort_values(["casos", "graves", "costo"], ascending=False).head(10)
    con_accion = (top["accion"] != "Sin acción inmediata").sum()
    st.caption(f"Los 10 clientes con más casos en el mes; {con_accion} requieren una acción esta semana. "
               "**Seleccione una fila** para ver la ficha del cliente.")
    elegido = tabla_clientes(top, "sel_top")
    if elegido:
        ir_a("cliente", limpiar="sel_top", f_cliente=elegido)


# %% PÁGINA 2 — Diagnóstico: ¿por qué pasa y dónde se concentra?
def pagina_diagnostico():
    mes, d = CTX["mes"], CTX["datos"]
    encabezado("¿Por qué pasa y dónde se concentra?", mes)
    if not hay_datos():
        return

    c1, c2 = st.columns([2, 3], gap="large")
    with c1:
        grafico_tipo(d, mes)
    with c2:
        grafico_clase(d, mes)
    st.divider()
    mapa_calor_sectores(d, mes)
    st.divider()

    st.subheader("Todos los clientes del filtro")
    clientes = D.resumen_clientes(d, mes)
    solo = st.checkbox("Mostrar solo clientes de riesgo alto sin prevención en el mes", key="solo_sin_prevencion")
    if solo:
        clientes = clientes[clientes["clase_riesgo"].isin(D.CLASES_RIESGO_ALTO) & (clientes["prevencion_mes"] == 0)]
    orden = {"rojo": 0, "amarillo": 1, "verde": 2, "sin dato": 3}
    clientes = clientes.sort_values(["estado", "casos"], key=lambda c: c.map(orden) if c.name == "estado" else -c)
    st.caption(f"{len(clientes)} clientes, ordenados del peor al mejor estado. **Seleccione una fila** para ver su ficha.")
    elegido = tabla_clientes(clientes, "sel_diag")
    if elegido:
        ir_a("cliente", limpiar="sel_diag", f_cliente=elegido)


def grafico_tipo(d, mes):
    dist = D.distribucion(d, mes)
    leve, grave = int(dist["leve"].sum()), int(dist["grave"].sum())
    total = max(1, leve + grave)
    st.subheader(f"{pct(grave / total)} de los casos fueron graves")
    st.caption("Casos del mes por tipo.")
    fig = go.Figure(go.Bar(x=["Leve", "Grave"], y=[leve, grave], marker_color=[LEVE, GRAVE],
                           text=[f"{leve} ({pct(leve / total)})", f"{grave} ({pct(grave / total)})"],
                           textposition="outside", cliponaxis=False,
                           hovertemplate="%{x}: <b>%{y}</b> casos<extra></extra>"))
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=20, b=10), showlegend=False, bargap=0.4,
                      yaxis=dict(visible=False, range=[0, max(leve, grave, 1) * 1.25]))
    st.plotly_chart(fig, config={"displayModeBar": False})


def grafico_clase(d, mes):
    dist = D.distribucion(d, mes)
    grave_alto = dist.loc[dist["clase_riesgo"].isin(D.CLASES_RIESGO_ALTO), "grave"].sum()
    total_grave = max(1, dist["grave"].sum())
    st.subheader(f"Las clases 4 y 5 concentran el {pct(grave_alto / total_grave)} de los casos graves")
    st.caption("Casos del mes por clase de riesgo (1 = más bajo, 5 = más alto).")
    x = [f"Clase {c}" for c in dist["clase_riesgo"]]
    fig = go.Figure()
    for tipo, color in [("leve", LEVE), ("grave", GRAVE)]:
        fig.add_trace(go.Bar(x=x, y=dist[tipo], name=tipo.capitalize(), marker_color=color,
                             hovertemplate="%{x} · " + tipo + ": <b>%{y}</b> casos<extra></extra>"))
    fig.update_layout(barmode="stack", bargap=0.35, height=320, margin=dict(l=10, r=10, t=20, b=10),
                      legend=dict(orientation="h", y=1.1, x=1, xanchor="right", traceorder="normal"),
                      yaxis=dict(gridcolor=GRILLA))
    st.plotly_chart(fig, config={"displayModeBar": False})


def mapa_calor_sectores(d, mes):
    """Análisis temporal por segmento: ¿el problema de un sector es de un mes o de varios?"""
    df = D.tasa_sector_mes(d, mes)
    if df.empty:
        return
    persistentes = (df[df["periodo"] > mes - pd.DateOffset(months=3)]
                    .groupby("sector")["estado"].apply(lambda e: (e == "rojo").all()))
    persistentes = persistentes[persistentes].index.tolist()
    if persistentes:
        st.subheader(f"Fuera de meta los últimos 3 meses: {', '.join(persistentes)}")
    else:
        st.subheader("Ningún sector lleva 3 meses seguidos fuera de meta")
    st.caption("Tasa por sector y mes frente a su meta. Un problema de varios meses seguidos pide un plan, no una visita.")

    meses = sorted(df["periodo"].unique())
    sectores = sorted(df["sector"].unique(), reverse=True)
    codigo = {"verde": 0, "amarillo": 1, "rojo": 2, "sin dato": np.nan}
    z, texto, hover = [], [], []
    for s in sectores:
        fila_z, fila_t, fila_h = [], [], []
        for m in meses:
            r = df[(df["sector"] == s) & (df["periodo"] == m)]
            if r.empty:
                fila_z.append(np.nan); fila_t.append(""); fila_h.append("")
                continue
            r = r.iloc[0]
            e = ESTADOS[r["estado"]]
            fila_z.append(codigo[r["estado"]])
            fila_t.append(f"{e['icono']} {fmt(r['tasa'], 2)}")
            fila_h.append(f"{s} · {mes_corto(pd.Timestamp(m))}<br>Tasa {fmt(r['tasa'], 2)} · meta {fmt(r['meta_tasa'], 2)}"
                          f"<br>{e['icono']} {e['texto']}")
        z.append(fila_z); texto.append(fila_t); hover.append(fila_h)

    escala = [[0, ESTADOS["verde"]["color"]], [0.33, ESTADOS["verde"]["color"]],
              [0.34, ESTADOS["amarillo"]["color"]], [0.66, ESTADOS["amarillo"]["color"]],
              [0.67, ESTADOS["rojo"]["color"]], [1, ESTADOS["rojo"]["color"]]]
    fig = go.Figure(go.Heatmap(z=z, x=[mes_corto(pd.Timestamp(m)) for m in meses], y=sectores, zmin=0, zmax=2,
                               colorscale=escala, showscale=False, text=texto, texttemplate="%{text}",
                               textfont=dict(size=11, color="#0b0b0b"), customdata=hover,
                               hovertemplate="%{customdata}<extra></extra>", xgap=2, ygap=2, opacity=0.6))
    fig.update_layout(height=40 * len(sectores) + 60, margin=dict(l=10, r=10, t=10, b=10),
                      xaxis=dict(side="top"), yaxis=dict(title=None))
    st.plotly_chart(fig, config={"displayModeBar": False})


# %% PÁGINA 3 — Ficha del cliente: ¿qué hago con este cliente?
def pagina_cliente():
    mes, d = CTX["mes"], CTX["datos"]
    encabezado("¿Qué hago con este cliente?", mes)
    if not hay_datos():
        return

    clientes = D.resumen_clientes(d, mes)
    orden = {"rojo": 0, "amarillo": 1, "verde": 2, "sin dato": 3}
    clientes = clientes.sort_values(["estado", "casos"], key=lambda c: c.map(orden) if c.name == "estado" else -c)
    opciones = clientes["id_cliente"].tolist()
    if st.session_state.get("f_cliente") not in opciones:
        st.session_state["f_cliente"] = opciones[0]
    nombres = clientes.set_index("id_cliente")

    st.selectbox("Cliente", opciones, key="f_cliente",
                 format_func=lambda i: f"{ESTADOS[nombres.at[i, 'estado']]['icono']} {nombres.at[i, 'nombre']} · "
                                       f"{nombres.at[i, 'sector']} · {nombres.at[i, 'coordinador']}",
                 help="Ordenados del peor al mejor estado. Respeta los filtros de la barra lateral.")
    id_cliente = st.session_state["f_cliente"]
    c = nombres.loc[id_cliente]
    e = ESTADOS[c["estado"]]

    st.markdown(
        f"<div style='border-left:6px solid {e['color']}; background:{e['color']}14; padding:10px 16px; "
        f"border-radius:4px'><b>Acción sugerida:</b> {c['accion']}<br>"
        f"<span style='opacity:0.75'>{c['sector']} · clase de riesgo {c['clase_riesgo']} · "
        f"{fmt(c['trabajadores'])} trabajadores · {c['coordinador']}</span></div>", unsafe_allow_html=True)
    st.write("")

    serie = D.serie_cliente(d, id_cliente)
    k = D.comparar(serie, mes, "tasa")
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        with st.container(border=True):
            st.markdown("**Tasa de incidencia**", help=AYUDA["tasa"])
            st.markdown(f"<div style='font-size:1.8rem; font-weight:600'>{fmt(c['tasa'], 2)}</div>",
                        unsafe_allow_html=True)
            st.markdown(insignia(c["estado"], f"meta {fmt(c['meta_tasa'], 2)}"), unsafe_allow_html=True)
            st.markdown(variacion_md(k["variacion"], "el mes anterior"))
    m2.metric("Casos del mes", int(c["casos"]), border=True)
    m3.metric("Casos graves", int(c["graves"]), border=True)
    m4.metric("Días sin prevención", "Nunca" if pd.isna(c["dias_sin_prevencion"]) else int(c["dias_sin_prevencion"]),
              border=True, help="Días desde la última actividad de prevención hasta el cierre del mes.")

    izq, der = st.columns([3, 2], gap="large")
    with izq:
        st.subheader("Evolución de los últimos 12 meses")
        ult12 = serie[serie["periodo"] <= mes].tail(12)
        x = [mes_corto(p) for p in ult12["periodo"]]
        prev = d["prevencion"][d["prevencion"]["id_cliente"] == id_cliente].groupby("periodo").size()
        fig = go.Figure()
        fig.add_trace(go.Bar(x=x, y=ult12["casos"], name="Casos", marker_color=LEVE,
                             hovertemplate="%{x}: <b>%{y}</b> casos<extra></extra>"))
        fig.add_trace(go.Scatter(x=x, y=ult12["casos_meta"], mode="lines", name="Casos esperados según la meta",
                                 line=dict(color=GRIS_META, dash="dash", width=2),
                                 hovertemplate="Meta: %{y:.1f} casos<extra></extra>"))
        sin_prev = [mes_corto(p) for p in ult12["periodo"] if prev.get(p, 0) == 0]
        fig.add_trace(go.Scatter(x=sin_prev, y=[0] * len(sin_prev), mode="markers", name="Mes sin prevención",
                                 marker=dict(symbol="triangle-up", size=11, color=ESTADOS["rojo"]["color"]),
                                 hovertemplate="%{x}: sin actividades de prevención<extra></extra>"))
        fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), bargap=0.35,
                          legend=dict(orientation="h", y=-0.15), yaxis=dict(gridcolor=GRILLA, rangemode="tozero"))
        st.plotly_chart(fig, config={"displayModeBar": False})
        st.caption("Las barras sobre la línea punteada son meses con más casos de los que permite la meta. "
                   "Los triángulos ▲ marcan meses sin prevención.")
    with der:
        st.subheader("Casos del mes")
        casos_mes = d["casos"][(d["casos"]["id_cliente"] == id_cliente) & (d["casos"]["periodo"] == mes)]
        if casos_mes.empty:
            st.write("Sin casos en el mes.")
        else:
            st.dataframe(pd.DataFrame({
                "Fecha": casos_mes["fecha_ocurrencia"].dt.strftime("%d/%m/%Y"),
                "Tipo": casos_mes["tipo"].str.capitalize(),
                "Días de ausencia": casos_mes["dias_ausencia"],
                "Costo (M$)": (casos_mes["costo"] / 1e6).round(2),
            }).sort_values("Fecha"), hide_index=True, height=180)
        st.subheader("Últimas actividades de prevención")
        p = d["prevencion"][(d["prevencion"]["id_cliente"] == id_cliente) &
                            (d["prevencion"]["fecha"] <= mes + pd.offsets.MonthEnd(0))].sort_values("fecha", ascending=False).head(5)
        if p.empty:
            st.write("No hay actividades registradas.")
        else:
            st.dataframe(pd.DataFrame({"Fecha": p["fecha"].dt.strftime("%d/%m/%Y"), "Tipo": p["tipo"],
                                       "Participantes": p["participantes"]}), hide_index=True)


# %% Programa principal
def main():
    st.set_page_config(page_title="Monitoreo de casos", page_icon="📊", layout="wide")

    datos = cargar(pd.Timestamp.today().strftime("%Y-%m-%d"))
    aplicar_drill()
    mes = filtros(datos)

    CTX["mes"] = mes
    CTX["datos"] = D.filtrar(datos, st.session_state["f_coord"], st.session_state["f_sector"], st.session_state["f_clase"])
    CTX["serie"] = D.serie_mensual(CTX["datos"])

    PAGINAS["resumen"] = st.Page(pagina_resumen, title="Resumen", icon=":material/dashboard:", url_path="resumen", default=True)
    PAGINAS["diagnostico"] = st.Page(pagina_diagnostico, title="Diagnóstico", icon=":material/troubleshoot:", url_path="diagnostico")
    PAGINAS["cliente"] = st.Page(pagina_cliente, title="Ficha de cliente", icon=":material/person_search:", url_path="cliente")
    st.navigation(list(PAGINAS.values()), position="top").run()


if __name__ == "__main__":
    main()
