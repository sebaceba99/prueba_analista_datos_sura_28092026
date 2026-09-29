# Pruebas de las reglas de normalización y validación (Sección 3)
#
# Casos pequeños con resultado conocido. Si alguna regla cambia de comportamiento,
# el assert correspondiente falla y dice cuál fue.
#
# Ejecutar:  python probar_reglas.py

# %% Librerías
import pandas as pd

from validar_facturacion import aplicar_reglas, normalizar, normalizar_id, normalizar_periodo, separar_resultados

FECHA = pd.Timestamp("2026-09-26")


def tabla(filas):
    df = pd.DataFrame(filas, columns=["id_cliente", "periodo", "trabajadores_activos", "valor_contrato"])
    df["fila_origen"] = df.index + 2
    return df


# %% 1. Normalización de IDs
entrada = pd.Series(["CLI-0012", "cli-12", " CLI0012 ", "12", "CL1-00A2", "#N/A", "0", ""])
esperado = ["CLI-0012", "CLI-0012", "CLI-0012", "CLI-0012", None, None, None, None]
resultado = normalizar_id(entrada).tolist()
for e, r, x in zip(entrada, resultado, esperado):
    assert (pd.isna(r) and x is None) or r == x, f"ID {e!r}: se esperaba {x}, salió {r}"
print("OK  normalización de IDs")

# %% 2. Normalización de períodos
entrada = pd.Series(["2026-08-01", "2026-08", "08/2026", "2026/08", "01/08/2026", "202608",
                     "ago-2026", "Agosto 2026", "13/2026", "31/02/2026", "sin dato", ""])
resultado = normalizar_periodo(entrada)
assert (resultado[:8] == pd.Timestamp("2026-08-01")).all(), f"Períodos válidos mal leídos:\n{resultado[:8]}"
assert resultado[8:].isna().all(), f"Períodos inválidos que se aceptaron:\n{resultado[8:]}"
print("OK  normalización de períodos (incluye dd/mm/aaaa como formato colombiano)")

# %% 3. Reglas de completitud, validez, consistencia y oportunidad
df = tabla([
    ["CLI-0001", "2026-09-01", "10", "500000"],   # válida
    ["cli-2", "08/2026", "5", "250000"],          # válida después de normalizar
    ["CLI-0003", "2026-09-01", "0", "100000"],    # CON_01
    ["CLI-0004", "2026-12-01", "8", "100000"],    # OPO_01 (futuro)
    ["CLI-0005", "2026-05-01", "8", "100000"],    # OPO_02 (más de 60 días)
    ["CLI-0006", "2026-09-01", "-3", "100000"],   # VAL_04 y CON_01
    ["CLI-0007", "2026-09-01", "", "100000"],     # COM_03
    ["CLI-0008", "2026-09-01", "12.5", "100000"], # VAL_03
    ["CLI-0009", "2026-09-01", "10", "$ 1.200.000"],  # VAL_05 (no se adivina el monto)
])
validos, rechazados = separar_resultados(aplicar_reglas(normalizar(df), FECHA))
reglas = dict(zip(rechazados["id_cliente"], rechazados["reglas_incumplidas"]))

assert sorted(validos["id_cliente"]) == ["CLI-0001", "CLI-0002"]
assert reglas["CLI-0003"] == "CON_01"
assert reglas["CLI-0004"] == "OPO_01"
assert reglas["CLI-0005"] == "OPO_02"
assert reglas["CLI-0006"] == "VAL_04; CON_01"
assert reglas["CLI-0007"] == "COM_03"
assert reglas["CLI-0008"] == "VAL_03"
assert reglas["CLI-0009"] == "VAL_05"
print("OK  reglas de completitud, validez, consistencia y oportunidad")

# %% 4. Duplicados
df = tabla([
    ["CLI-0001", "2026-09-01", "10", "500000"],
    ["cli-1", "09/2026", "10", "500000"],        # mismo cliente y mes, mismos valores -> se conserva uno
    ["CLI-0002", "2026-09-01", "10", "500000"],
    ["CLI-0002", "2026-09-01", "10", "550000"],  # mismo cliente y mes, valor distinto -> se rechazan ambos
])
validos, rechazados = separar_resultados(aplicar_reglas(normalizar(df), FECHA))

assert validos["id_cliente"].tolist() == ["CLI-0001"]
assert sorted(rechazados["reglas_incumplidas"]) == ["UNI_01", "UNI_02", "UNI_02"]
print("OK  duplicados exactos y conflictivos")

print("\nTodas las pruebas pasaron.")
