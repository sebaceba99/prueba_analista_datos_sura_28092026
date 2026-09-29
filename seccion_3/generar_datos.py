# Generador del archivo de facturación de prueba (Sección 3)
#
# Crea un CSV con 1.000+ registros y errores de calidad sembrados a propósito:
#   - IDs de cliente con formatos inconsistentes (y algunos irrecuperables)
#   - Períodos en diferentes formatos de fecha (y algunos imposibles)
#   - Valores nulos, negativos o no numéricos en campos críticos
#   - Duplicados por cliente + período (exactos y con valores distintos)
#   - Contratos con valor pero sin trabajadores
#   - Períodos futuros o con más de 60 días de atraso
#
# Todo se guarda como texto, igual que llegaría desde el sistema origen.
# Usa una semilla fija, así que siempre genera el mismo archivo.

# %% Librerías
import numpy as np
import pandas as pd

MESES = {1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio", 7: "julio",
         8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"}


# %% Función principal
def generar_facturacion(fecha_carga, n_clientes=560, semilla=42):
    """
    Devuelve un DataFrame (todo en texto) con la facturación del mes anterior y
    del mes en curso para n_clientes, con errores de calidad sembrados.
    """
    rng = np.random.default_rng(semilla)
    fecha_carga = pd.Timestamp(fecha_carga)
    mes_actual = fecha_carga.to_period("M").to_timestamp()
    mes_anterior = mes_actual - pd.DateOffset(months=1)

    # ---- 1. Datos limpios: cada cliente con dos períodos ----
    clientes = np.arange(1, n_clientes + 1)
    trabajadores_base = np.clip(rng.lognormal(4.2, 0.9, n_clientes), 3, 5000).astype(int)
    tarifa = rng.uniform(38_000, 62_000, n_clientes)  # pesos por trabajador al mes

    df = pd.DataFrame({
        "num_cliente": np.repeat(clientes, 2),
        "periodo": np.tile([mes_anterior, mes_actual], n_clientes),
        "trabajadores_activos": np.repeat(trabajadores_base, 2) + rng.integers(-3, 4, n_clientes * 2),
        "tarifa": np.repeat(tarifa, 2),
    })
    df["trabajadores_activos"] = df["trabajadores_activos"].clip(lower=1)
    df["valor_contrato"] = (df["trabajadores_activos"] * df["tarifa"]).round(2)

    # Todo a texto, en el formato "correcto"
    df["id_cliente"] = "CLI-" + df["num_cliente"].astype(str).str.zfill(4)
    df["periodo_fecha"] = df["periodo"]
    df["periodo"] = df["periodo"].dt.strftime("%Y-%m-%d")
    df["trabajadores_activos"] = df["trabajadores_activos"].astype(str)
    df["valor_contrato"] = df["valor_contrato"].map("{:.2f}".format)

    def filas_al_azar(fraccion):
        """Índices de una muestra aleatoria de filas."""
        n = max(1, int(len(df) * fraccion))
        return rng.choice(df.index, size=n, replace=False)

    # ---- 2. IDs con formato inconsistente (recuperables) ----
    for i in filas_al_azar(0.15):
        num = df.loc[i, "num_cliente"]
        variantes = [f"cli-{num:04d}", f"CLI{num:04d}", f"CLI-{num}", f" CLI-{num:04d} ", f"{num}"]
        df.loc[i, "id_cliente"] = rng.choice(variantes)

    # IDs irrecuperables
    for i in filas_al_azar(0.012):
        df.loc[i, "id_cliente"] = rng.choice(["CL1-00A2", "#N/A", "cliente_x", "0", "CLI-"])

    # ---- 3. Períodos en otros formatos (recuperables) ----
    for i in filas_al_azar(0.20):
        p = df.loc[i, "periodo_fecha"]
        variantes = [
            p.strftime("%Y-%m"),                     # 2026-09
            p.strftime("%m/%Y"),                     # 09/2026
            p.strftime("%Y/%m"),                     # 2026/09
            p.strftime("%d/%m/%Y"),                  # 01/09/2026 (formato colombiano)
            p.strftime("%Y%m"),                      # 202609
            f"{MESES[p.month][:3]}-{p.year}",        # sep-2026
            f"{MESES[p.month].capitalize()} {p.year}",  # Septiembre 2026
        ]
        df.loc[i, "periodo"] = rng.choice(variantes)

    # Períodos imposibles de interpretar
    for i in filas_al_azar(0.01):
        df.loc[i, "periodo"] = rng.choice(["13/2026", "2026-15", "sin dato", "31/02/2026"])

    # ---- 4. Nulos en campos críticos ----
    for col, fraccion in [("id_cliente", 0.008), ("periodo", 0.008),
                          ("trabajadores_activos", 0.015), ("valor_contrato", 0.015)]:
        df.loc[filas_al_azar(fraccion), col] = ""

    # ---- 5. Negativos, textos y valores no enteros ----
    idx = [i for i in filas_al_azar(0.012) if df.loc[i, "valor_contrato"] != ""]
    df.loc[idx, "valor_contrato"] = "-" + df.loc[idx, "valor_contrato"]
    for i in filas_al_azar(0.008):
        df.loc[i, "trabajadores_activos"] = str(-rng.integers(1, 50))
    for i in filas_al_azar(0.005):
        df.loc[i, "trabajadores_activos"] = rng.choice(["N/A", "doce", "12.5"])
    for i in filas_al_azar(0.005):
        df.loc[i, "valor_contrato"] = rng.choice(["N/A", "$ 1.200.000", "pendiente"])

    # ---- 6. Inconsistencia: contrato con valor pero sin trabajadores ----
    df.loc[filas_al_azar(0.012), "trabajadores_activos"] = "0"

    # ---- 7. Oportunidad: períodos futuros o muy atrasados ----
    for i in filas_al_azar(0.01):
        futuro = mes_actual + pd.DateOffset(months=int(rng.integers(1, 4)))
        df.loc[i, "periodo"] = futuro.strftime("%Y-%m-%d")
    for i in filas_al_azar(0.015):
        atrasado = mes_actual - pd.DateOffset(months=int(rng.integers(3, 8)))
        df.loc[i, "periodo"] = atrasado.strftime("%Y-%m-%d")

    # ---- 8. Duplicados por cliente + período ----
    columnas = ["id_cliente", "periodo", "trabajadores_activos", "valor_contrato"]
    exactos = df.loc[filas_al_azar(0.02), columnas]
    conflictivos = df.loc[filas_al_azar(0.015), columnas].copy()
    valor = pd.to_numeric(conflictivos["valor_contrato"], errors="coerce")
    tiene_valor = valor.notna()
    conflictivos.loc[tiene_valor, "valor_contrato"] = (valor[tiene_valor] * 1.1).round(2).astype(str)

    resultado = pd.concat([df[columnas], exactos, conflictivos], ignore_index=True)
    resultado = resultado.sample(frac=1, random_state=semilla).reset_index(drop=True)  # desordenar
    return resultado


# %% Ejecución directa: python generar_datos.py
if __name__ == "__main__":
    from pathlib import Path
    Path("datos").mkdir(exist_ok=True)
    datos = generar_facturacion(pd.Timestamp.today())
    datos.to_csv("datos/facturacion_raw.csv", index=False)
    print(f"Archivo generado: {len(datos)} registros")
