import os
from datetime import datetime, date
import pandas as pd
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

# --------- CONEXIÓN MONGO ---------
URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/gestion_docentes")
client = MongoClient(URI)

db = client.get_default_database()
if db is None:
    db = client["gestion_docentes"]

col_alumnos = db.alumnos

# --------- ARCHIVO EXCEL ---------
FILENAME = "MATRICULA_2025.xlsx"

if not os.path.exists(FILENAME):
    print(f"[ERROR] No se encontró el archivo {FILENAME} en la carpeta del proyecto.")
    raise SystemExit(1)

print(f"Usando archivo: {FILENAME}")

xls = pd.ExcelFile(FILENAME)
print("Hojas encontradas:", xls.sheet_names)

# --------- HELPERS ---------
def norm(s: str) -> str:
    """Normaliza encabezados: mayúsculas, _ y sin tildes ni puntos."""
    s = (s or "").strip().upper().replace(".", "_")
    for a, b in (
        ("Á", "A"), ("É", "E"), ("Í", "I"), ("Ó", "O"), ("Ú", "U"),
    ):
        s = s.replace(a, b)
    s = s.replace(" ", "_")
    return s

# mapa encabezado_normalizado -> campo en Mongo
MAPEO = {
    "APELLIDO": "apellido",
    "APELLIDOS": "apellido",
    "NOMBRE": "nombre",
    "NOMBRES": "nombre",
    "APELLIDO, NOMBRE": "apellido_nombre",
    "DNI": "dni",
    "D_N_I": "dni",
    "CUIL": "cuil",
    "FECHA_NAC": "fecha_nacimiento",
    "FECHA_NACIMIENTO": "fecha_nacimiento",
    "NACIONALIDAD": "nacionalidad",
    "DOMICILIO": "domicilio",
    "DIRECCION": "domicilio",
    "LOCALIDAD": "localidad",
    "CURSO": "curso",
    "GRADO": "curso",  # por si acaso
    "MADRE_PADRE_TUTOR": "responsable",
    "MADRE/PADRE/TUTOR": "responsable",
    "MADRE PADRE TUTOR": "responsable",
    "MADREPADRETUTOR": "responsable",
    "TELEFONO": "telefono",
    "TEL": "telefono",
    "TELÉFONO": "telefono",
    "ESC_PROCEDENCIA": "escuela_procedencia",
    "ESCUELA_PROCEDENCIA": "escuela_procedencia",
    "FECHA_INGRESO": "fecha_ingreso",
    "OBSERVACIONES": "observaciones",
    "SEXO": "sexo",          # 👈 NUEVO
    "GENERO": "sexo",        # opcional, por si alguna hoja lo llama así
}

# columnas mínimas que queremos tener para considerar la fila "real"
REQUERIDAS_LOGICAS = ["apellido", "nombre", "curso"]

def normalizar_curso(c: str) -> str:
    """Devuelve algo tipo 3°A / 3°B."""
    if not c:
        return ""
    s = c.strip().upper()
    s = s.replace("º", "°")
    s = s.replace(",", " ").replace("  ", " ")

    grado = ""
    for g in ("1", "2", "3", "4", "5", "6"):
        if g in s:
            grado = g
            break
    if not grado:
        return c.strip()

    seccion = ""
    if "A" in s:
        seccion = "A"
    if "B" in s:
        seccion = "B"

    if seccion:
        return f"{grado}°{seccion}"
    return f"{grado}°"

def parse_fecha(valor):
    if pd.isna(valor):
        return None
    if isinstance(valor, (datetime, date)):
        return valor.strftime("%Y-%m-%d")
    s = str(valor).strip()
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None

# --------- PROCESO ---------
total_insertados = 0
total_skipped = 0

for sheet_name in xls.sheet_names:
    print(f"\n--- Procesando hoja: {sheet_name} ---")
    df = pd.read_excel(FILENAME, sheet_name=sheet_name)

    # normalizar encabezados
    cols_norm = {c: norm(str(c)) for c in df.columns}

    # construir mapa de columna_origen -> campo_mongo
    col_map = {}
    for original, encabezado_norm in cols_norm.items():
        if encabezado_norm in MAPEO:
            col_map[original] = MAPEO[encabezado_norm]

    # chequeo mínimo
    campos_norm = set(col_map.values())
    if not {"apellido", "nombre", "curso"}.issubset(campos_norm):
        print("  [AVISO] La hoja no tiene columnas mínimas (apellido, nombre, curso). Se salta.")
        continue

    for idx, row in df.iterrows():
        data = {}

        # armar dict según col_map
        for original_col, campo in col_map.items():
            valor = row.get(original_col)
            if pd.isna(valor):
                continue

            if campo in ("fecha_nacimiento", "fecha_ingreso"):
                f = parse_fecha(valor)
                if f:
                    data[campo] = f
            elif campo == "telefono":
                tel_raw = str(valor).replace("\\n", "\n")
                tels = [t.strip() for t in tel_raw.splitlines() if t.strip()]
                if tels:
                    data[campo] = " / ".join(tels)
            else:
                data[campo] = str(valor).strip()

        # ---- normalización de sexo ----
        sexo = (data.get("sexo") or "").strip().upper()
        if sexo not in ("M", "F"):
            sexo = "X"
        data["sexo"] = sexo

        # ---- validaciones básicas ----
        apellido = data.get("apellido", "").strip()
        nombre = data.get("nombre", "").strip()
        curso = data.get("curso", "").strip()
        dni = str(data.get("dni", "")).strip()

        if not (apellido or nombre or dni or curso):
            continue

        cabeceras = {"APELLIDO", "APELLIDOS", "APELLIDO, NOMBRE", "NOMBRE", "DNI", "CURSO"}
        if (
            apellido.upper() in cabeceras
            or nombre.upper() in cabeceras
            or curso.upper() in cabeceras
        ):
            total_skipped += 1
            continue

        if not (apellido and nombre and curso):
            print(f"  [SKIP] Fila {idx+2}: datos incompletos -> {data}")
            total_skipped += 1
            continue

        data["curso"] = normalizar_curso(curso)

        col_alumnos.insert_one(data)
        total_insertados += 1

print("\n===================================")
print(f"Alumnos insertados: {total_insertados}")
print(f"Filas saltadas   : {total_skipped}")
print("Listo.")
