# Validación de calidad del archivo de facturación (Sección 3)
#
# Simula el control que correría automáticamente con cada carga nueva,
# antes de que el archivo entre al pipeline:
#
#   1. Leer el CSV (todo como texto)
#   2. Normalizar formatos inequívocos (IDs y períodos)
#   3. Aplicar las reglas de las 5 dimensiones de calidad
#   4. Exportar válidos (Parquet), rechazados (Excel) y reporte (JSON)
#   5. Aprobar o bloquear la carga según el % de rechazo
#
# Ejecutar:  python validar_facturacion.py
#
# Códigos de salida (para que un orquestador pueda detener el pipeline):
#   0 = carga aprobada | 2 = error de entrada | 3 = carga bloqueada

# %% Librerías
import json
import sys
from pathlib import Path

import pandas as pd

from generar_datos import generar_facturacion

# %% Configuración
CARPETA = Path(__file__).parent
ARCHIVO_ENTRADA = CARPETA / "datos" / "facturacion_raw.csv"  # si no existe, se genera uno de prueba
CARPETA_SALIDA = CARPETA / "salidas"
FECHA_CARGA = pd.Timestamp.today().normalize()

UMBRAL_RECHAZO = 0.20        # por encima de este % de rechazo, la carga se bloquea
MAX_TRABAJADORES = 50_000
MAX_VALOR_CONTRATO = 5_000_000_000
MAX_DIAS_ATRASO = 60

COLUMNAS_OBLIGATORIAS = ["id_cliente", "periodo", "trabajadores_activos", "valor_contrato"]

# Catálogo de reglas: código -> (dimensión, descripción)
REGLAS = {
    "COM_01": ("completitud", "id_cliente vacío"),
    "COM_02": ("completitud", "periodo vacío"),
    "COM_03": ("completitud", "trabajadores_activos vacío"),
    "COM_04": ("completitud", "valor_contrato vacío"),
    "VAL_01": ("validez", "id_cliente con formato irreconocible"),
    "VAL_02": ("validez", "periodo con formato de fecha irreconocible"),
    "VAL_03": ("validez", "trabajadores_activos no es un número entero"),
    "VAL_04": ("validez", "trabajadores_activos fuera de rango (0 a 50.000)"),
    "VAL_05": ("validez", "valor_contrato no es numérico"),
    "VAL_06": ("validez", "valor_contrato fuera de rango (0 a 5.000 millones)"),
    "UNI_01": ("unicidad", "duplicado exacto de cliente + periodo (se conserva la primera fila)"),
    "UNI_02": ("unicidad", "duplicado de cliente + periodo con valores distintos"),
    "CON_01": ("consistencia", "valor_contrato > 0 pero trabajadores_activos <= 0"),
    "OPO_01": ("oportunidad", "periodo futuro"),
    "OPO_02": ("oportunidad", f"periodo con más de {MAX_DIAS_ATRASO} días de atraso"),
}

# Formatos de fecha aceptados. Se prueban uno por uno, nunca se "adivina":
# 01/08/2026 es agosto en Colombia (dd/mm/aaaa) y enero en EE. UU.
FORMATOS_FECHA = ["%Y-%m-%d", "%Y-%m", "%Y/%m", "%m/%Y", "%d/%m/%Y", "%Y%m", "%m-%Y", "%m %Y"]

# Nombres de meses en español -> número (primero los nombres largos)
MESES = {"septiembre": "09", "noviembre": "11", "diciembre": "12", "febrero": "02", "octubre": "10",
         "agosto": "08", "enero": "01", "marzo": "03", "abril": "04", "junio": "06", "julio": "07",
         "mayo": "05", "ene": "01", "feb": "02", "mar": "03", "abr": "04", "may": "05", "jun": "06",
         "jul": "07", "ago": "08", "sep": "09", "set": "09", "oct": "10", "nov": "11", "dic": "12"}


# %% 1. Lectura
def leer_archivo(ruta):
    """Lee el CSV como texto y revisa que tenga las columnas obligatorias."""
    if not ruta.exists():
        raise FileNotFoundError(f"No existe el archivo {ruta}")

    df = pd.read_csv(ruta, dtype=str, keep_default_na=False)  # todo como texto, vacíos como ""
    df.columns = df.columns.str.strip().str.lower()

    faltantes = [c for c in COLUMNAS_OBLIGATORIAS if c not in df.columns]
    if faltantes:
        raise ValueError(f"Faltan columnas obligatorias: {faltantes}")
    if df.empty:
        raise ValueError("El archivo no tiene registros")

    df["fila_origen"] = df.index + 2  # número de fila en el archivo (la 1 es el encabezado)
    return df


# %% 2. Normalización
def normalizar_id(serie):
    """'cli-12', ' CLI0012 ', '12'  ->  'CLI-0012'. Lo que no se reconoce queda vacío (NaN)."""
    numero = (serie.str.strip().str.upper()
                   .str.extract(r"^(?:CLI)?[-_ ]?0*(\d{1,6})$")[0])
    numero = pd.to_numeric(numero, errors="coerce")
    numero = numero.where(numero > 0)  # el cliente 0 no existe
    return ("CLI-" + numero.astype("Int64").astype(str).str.zfill(4)).where(numero.notna())


def normalizar_periodo(serie):
    """Convierte las variantes conocidas de período al primer día del mes. Lo que no se reconoce queda NaT."""
    texto = serie.str.strip().str.lower()

    # 'sep-2026' -> '09-2026' ; 'septiembre 2026' -> '09 2026'
    for nombre, numero in MESES.items():
        texto = texto.str.replace(rf"^{nombre}\b", numero, regex=True)

    fecha = pd.Series(pd.NaT, index=serie.index)
    for formato in FORMATOS_FECHA:
        intento = pd.to_datetime(texto, format=formato, errors="coerce")
        fecha = fecha.fillna(intento)

    return fecha.dt.to_period("M").dt.to_timestamp()


def normalizar(df):
    """Agrega columnas limpias (_id, _periodo, _trabajadores, _valor) sin tocar las originales."""
    df["_id"] = normalizar_id(df["id_cliente"])
    df["_periodo"] = normalizar_periodo(df["periodo"])
    df["_trabajadores"] = pd.to_numeric(df["trabajadores_activos"].str.strip(), errors="coerce")
    df["_valor"] = pd.to_numeric(df["valor_contrato"].str.strip(), errors="coerce")  # '$ 1.200.000' no se adivina
    return df


# %% 3. Reglas de calidad
def aplicar_reglas(df, fecha_carga):
    """Agrega una columna booleana por regla: True = la fila incumple la regla."""
    vacio = {col: df[col].str.strip().eq("") for col in COLUMNAS_OBLIGATORIAS}
    trab, valor, periodo = df["_trabajadores"], df["_valor"], df["_periodo"]

    # Completitud
    df["COM_01"] = vacio["id_cliente"]
    df["COM_02"] = vacio["periodo"]
    df["COM_03"] = vacio["trabajadores_activos"]
    df["COM_04"] = vacio["valor_contrato"]

    # Validez (solo sobre campos que sí vienen diligenciados)
    trab_no_entero = ~vacio["trabajadores_activos"] & (trab.isna() | (trab % 1 != 0))
    df["VAL_01"] = ~vacio["id_cliente"] & df["_id"].isna()
    df["VAL_02"] = ~vacio["periodo"] & periodo.isna()
    df["VAL_03"] = trab_no_entero
    df["VAL_04"] = ~trab_no_entero & ((trab < 0) | (trab > MAX_TRABAJADORES))
    df["VAL_05"] = ~vacio["valor_contrato"] & valor.isna()
    df["VAL_06"] = (valor < 0) | (valor > MAX_VALOR_CONTRATO)

    # Consistencia
    df["CON_01"] = (valor > 0) & (trab <= 0)

    # Oportunidad
    df["OPO_01"] = periodo > fecha_carga
    df["OPO_02"] = (fecha_carga - periodo).dt.days > MAX_DIAS_ATRASO

    # Unicidad: sobre el ID y período YA normalizados ('cli-1' y 'CLI-0001' son el mismo cliente)
    llave = ["_id", "_periodo"]
    tiene_llave = df["_id"].notna() & df["_periodo"].notna()
    df["_firma"] = df["_trabajadores"].astype(str) + "|" + df["_valor"].astype(str)
    versiones = df.groupby(llave)["_firma"].transform("nunique")  # cuántas versiones distintas hay de cada llave

    df["UNI_02"] = tiene_llave & (versiones > 1)
    df["UNI_01"] = tiene_llave & (versiones == 1) & df.duplicated(subset=llave, keep="first")

    # NaN en comparaciones -> False (una regla que no se puede evaluar no se marca como incumplida)
    df[list(REGLAS)] = df[list(REGLAS)].fillna(False).astype(bool)
    return df


# %% 4. Resultados
def separar_resultados(df):
    """Devuelve (validos, rechazados). Los rechazados llevan los códigos y la razón de rechazo."""
    df["reglas_incumplidas"] = ""
    df["razon_rechazo"] = ""
    for codigo, (dimension, descripcion) in REGLAS.items():
        falla = df[codigo]
        df.loc[falla, "reglas_incumplidas"] += codigo + "; "
        df.loc[falla, "razon_rechazo"] += descripcion + "; "
    df["reglas_incumplidas"] = df["reglas_incumplidas"].str.rstrip("; ")
    df["razon_rechazo"] = df["razon_rechazo"].str.rstrip("; ")

    rechazada = df["reglas_incumplidas"] != ""

    validos = df.loc[~rechazada, ["fila_origen", "_id", "_periodo", "_trabajadores", "_valor"]].rename(columns={
        "_id": "id_cliente", "_periodo": "periodo",
        "_trabajadores": "trabajadores_activos", "_valor": "valor_contrato"})
    validos["trabajadores_activos"] = validos["trabajadores_activos"].astype(int)
    validos["fecha_carga"] = FECHA_CARGA

    rechazados = df.loc[rechazada, ["fila_origen"] + COLUMNAS_OBLIGATORIAS + ["reglas_incumplidas", "razon_rechazo"]]
    return validos.reset_index(drop=True), rechazados.reset_index(drop=True)


def construir_reporte(df, validos, rechazados):
    """Reporte de calidad: score por dimensión, totales y rechazos por regla."""
    total = len(df)

    score = {}
    for dimension in ["completitud", "validez", "unicidad", "consistencia", "oportunidad"]:
        codigos = [c for c, (d, _) in REGLAS.items() if d == dimension]
        filas_con_falla = df[codigos].any(axis=1).sum()
        score[dimension] = round(1 - filas_con_falla / total, 4)

    rechazos_por_regla = {
        codigo: {"dimension": dim, "descripcion": desc, "filas": int(df[codigo].sum())}
        for codigo, (dim, desc) in REGLAS.items()
    }

    pct_rechazo = len(rechazados) / total
    return {
        "fecha_carga": FECHA_CARGA.strftime("%Y-%m-%d"),
        "archivo": str(ARCHIVO_ENTRADA.name),
        "total_registros": total,
        "validos": len(validos),
        "rechazados": len(rechazados),
        "pct_rechazo": round(pct_rechazo, 4),
        "estado_carga": "APROBADA" if pct_rechazo <= UMBRAL_RECHAZO else "BLOQUEADA",
        "score_por_dimension": score,
        "rechazos_por_regla": rechazos_por_regla,
        "correcciones_de_formato": {
            "id_cliente": int((df["_id"].notna() & (df["_id"] != df["id_cliente"])).sum()),
            "periodo": int((df["_periodo"].notna() & ~df["periodo"].str.fullmatch(r"\d{4}-\d{2}-01")).sum()),
        },
        "notas": [
            "score = 1 - (filas con al menos una falla en la dimensión / total de filas)",
            "una fila puede fallar varias reglas, por eso la suma por regla puede superar el total de rechazados",
        ],
    }


def exportar(validos, rechazados, reporte):
    """Escribe el Parquet de válidos, el Excel de rechazados y el JSON del reporte."""
    CARPETA_SALIDA.mkdir(exist_ok=True)

    validos.to_parquet(CARPETA_SALIDA / "facturacion_validos.parquet", index=False)

    resumen = pd.DataFrame(reporte["rechazos_por_regla"]).T.reset_index(names="regla")
    with pd.ExcelWriter(CARPETA_SALIDA / "facturacion_rechazados.xlsx") as excel:
        rechazados.to_excel(excel, sheet_name="rechazados", index=False, freeze_panes=(1, 0))
        resumen.to_excel(excel, sheet_name="resumen_reglas", index=False, freeze_panes=(1, 0))

    with open(CARPETA_SALIDA / "reporte_calidad.json", "w", encoding="utf-8") as f:
        json.dump(reporte, f, ensure_ascii=False, indent=2)


# %% 5. Ejecución completa
def main():
    # Si no hay archivo de entrada, se genera uno de prueba
    if not ARCHIVO_ENTRADA.exists():
        ARCHIVO_ENTRADA.parent.mkdir(exist_ok=True)
        generar_facturacion(FECHA_CARGA).to_csv(ARCHIVO_ENTRADA, index=False)
        print(f"Archivo de prueba generado: {ARCHIVO_ENTRADA}")

    try:
        df = leer_archivo(ARCHIVO_ENTRADA)
    except (FileNotFoundError, ValueError, pd.errors.EmptyDataError) as error:
        print(f"ERROR: la carga no se puede validar. {error}")
        sys.exit(2)

    df = normalizar(df)
    df = aplicar_reglas(df, FECHA_CARGA)
    validos, rechazados = separar_resultados(df)
    reporte = construir_reporte(df, validos, rechazados)

    try:
        exportar(validos, rechazados, reporte)
    except PermissionError:
        print("ERROR: no se pudieron escribir las salidas. ¿Está abierto el Excel de una ejecución anterior?")
        sys.exit(2)

    # Resumen en pantalla
    print(f"\nRegistros: {reporte['total_registros']} | Válidos: {reporte['validos']} | "
          f"Rechazados: {reporte['rechazados']} ({reporte['pct_rechazo']:.1%})")
    for dimension, valor in reporte["score_por_dimension"].items():
        print(f"  {dimension:<13} {valor:.1%}")
    print(f"Salidas en: {CARPETA_SALIDA}")

    if reporte["estado_carga"] == "BLOQUEADA":
        print(f"\nCARGA BLOQUEADA: el rechazo supera el umbral de {UMBRAL_RECHAZO:.0%}")
        sys.exit(3)
    print("\nCARGA APROBADA")


if __name__ == "__main__":
    main()
