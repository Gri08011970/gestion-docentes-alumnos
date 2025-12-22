from flask import (
    Flask, render_template, request, redirect,
    url_for, jsonify, abort, send_file
) 

from flask_pymongo import PyMongo
from bson.objectid import ObjectId

from datetime import datetime, date, timedelta, timezone
from dateutil.relativedelta import relativedelta

from io import BytesIO
#from weasyprint import HTML 

from collections import defaultdict

import os
import unicodedata
import calendar

from urllib.parse import quote



try:
    from salidas_blueprint import salidas_bp
except ImportError:
    # Definición dummy si el blueprint no existe para evitar errores de importación 
    class DummyBlueprint:
        def register(self, app, url_prefix=None):
            pass
    salidas_bp = DummyBlueprint()

# Si tienes notify (para email), asegúrate de que esté importado
try:
    from notify import send_email
except ImportError:
    def send_email(*args, **kwargs):
        print("WARN: Función send_email no disponible. No se enviará email.")

from urllib.parse import quote

from urllib.parse import quote

# ----------------- Config Flask + Mongo -----------------
app = Flask(__name__)

# Función para usar strftime como filtro de Jinja en las plantillas

# Registrar el filtro 'strftime' en el entorno de Jinja (se registra más abajo tras definir datetimeformat)

app.config["MONGO_URI"] = os.environ.get("MONGO_URI", "mongodb://localhost:27017/escuela_db")

# UNA sola instancia de PyMongo
mongo = PyMongo(app)
# Exponer en app.mongo para blueprints: usar SIEMPRE la MISMA conexión
app.mongo = mongo.db

# Registrar blueprint de salidas
app.register_blueprint(salidas_bp, url_prefix="/salidas")

# ----------------- Collections -----------------
COL_DOCENTES        = mongo.db.docentes
COL_AUX             = mongo.db.auxiliares
COL_ALUMNOS         = mongo.db.alumnos
COL_MOVIMIENTOS     = mongo.db.movimientos_alumnos
COL_INASISTENCIAS_DOCENTES   = mongo.db.inasistencias
COL_ASISTENCIAS     = mongo.db.asistencias
COL_ASISTENCIA      = mongo.db.asistencias   # alias para compatibilidad con código que usa COL_ASISTENCIA
COL_DIAS_HABILES    = mongo.db.dias_habiles  # colección para almacenar días hábiles por curso/mes
COL_INASISTENCIAS     = mongo.db.inasistencias
COL_ESTADOS_ADMIN   = mongo.db.estados_admin
COL_CALIFICACIONES  = mongo.db.calificaciones
COL_CFG_ASIGNATURAS = mongo.db.config_asignaturas
COL_CURSOS          = mongo.db.cursos  # Agregado para evitar error de definición
COL_CONFIG          = mongo.db.config   # Colección para configuraciones generales (ej. _id: "config_general")COL_CERTIFICADOS    = mongo.db["certificados_pendientes"]
COL_CERTIFICADOS    = mongo.db.certificados_pendientes

# ----------------- Authentication Placeholder -----------------

# Función de ejemplo para obtener el usuario actual (placeholder)
def get_current_user():
    """Placeholder para obtener la información del usuario actual (admin, director, docente)"""
    # En un entorno real, esto se obtendría de la sesión o token
    # Para la simulación, retornamos un usuario predefinido
    return {
        "uid": "simulated_admin_user",
        "rol": "admin", # Puede ser 'admin', 'director', 'docente' 
        "nombre": "Admin Demo",
        "docente_id": None # Si el rol es 'docente', aquí iría su ObjectId
    }
# ----------------- CONFIG CALIFICACIONES -----------------
# Asignaturas por ciclo / grado (puedes ajustar nombres cuando quieras)

CFG_ASIGNATURAS = {
    "1": [
        "Prácticas del Lenguaje",
        "Matemática",
        "Ciencias Sociales",
        "Ciencias Naturales",
        "Educación Física",
        "Educación Artística"
    ],
    "2": [
        "Prácticas del Lenguaje",
        "Matemática",
        "Ciencias Sociales",
        "Ciencias Naturales",
        "Educación Física",
        "Educación Artística"
    ],
    "3": [
        "Prácticas del Lenguaje",
        "Matemática",
        "Ciencias Sociales",
        "Ciencias Naturales",
        "Educación Física",
        "Educación Artística"
    ],
    "4": [
        "Prácticas del Lenguaje",
        "Matemática",
        "Ciencias Sociales",
        "Ciencias Naturales",
        "Inglés",
        "Educación Física",
        "Educación Artística"
    ],
    "5": [
        "Prácticas del Lenguaje",
        "Matemática",
        "Ciencias Sociales",
        "Ciencias Naturales",
        "Inglés",
        "Educación Física",
        "Educación Artística"
    ],
    "6": [
        "Prácticas del Lenguaje",
        "Matemática",
        "Ciencias Sociales",
        "Ciencias Naturales",
        "Inglés",
        "Educación Física",
        "Educación Artística" 
    ]
}

def _get_cfg_calificaciones():
    """
    Retorna la configuración de asignaturas por ciclo/grado.
    Función simple para mantener compatibilidad con llamadas en la app;
    si en el futuro se requiere enriquecerla (p. ej. fusionar con
    COL_CFG_ASIGNATURAS de la DB), puede ampliarse aquí. 
    """
    return CFG_ASIGNATURAS

def filtro_activos():
    return {"$or": [
        {"fecha_salida": {"$exists": False}},  
        {"fecha_salida": ""},
        {"fecha_salida": None},
    ]}


def _insert_movimiento_si_no_duplicado(doc, segundos=5):
    """
    Evita duplicados si el form se envía 2 veces.
    Considera duplicado: mismo alumno_id + tipo + campos clave dentro de los últimos X segundos.
    """
    try:
        desde = datetime.utcnow() - timedelta(seconds=segundos)

        q = {
            "alumno_id": doc.get("alumno_id"),
            "tipo": doc.get("tipo"),
            "fecha": {"$gte": desde},
        }

        # Campos opcionales que hacen “único” al movimiento
        for k in ("curso_origen", "curso_destino", "curso", "motivo", "escuela_destino"):
            v = doc.get(k)
            if v not in (None, ""):
                q[k] = v

        # Si ya existe uno igual recientemente, no insertamos
        if COL_MOVIMIENTOS.find_one(q):
            return False

        COL_MOVIMIENTOS.insert_one(doc)
        return True

    except Exception as e:
        print("Error en _insert_movimiento_si_no_duplicado:", e)
        # ante duda, insertamos igual para no “perder” el movimiento
        try:
            COL_MOVIMIENTOS.insert_one(doc)
        except Exception:
            pass
        return True

@app.route('/')
def index():
    user = get_current_user()
    
    # Obtener un resumen de docentes y cursos
    docentes_count = COL_DOCENTES.count_documents({})
    cursos_count = COL_CURSOS.count_documents({})
    
    # Obtener el estado actual del SET4 (asumiendo que hay una configuración para el año)
    config = COL_CONFIG.find_one({"_id": "config_general"})
    set4_estado = config.get("set4_estado", "No iniciado") if config else "No iniciado"
    
    # Obtener alertas (vencimientos de licencias, etc.)
    # Esto es una simulación. En realidad se haría una consulta más compleja.
    alertas = []
    
    # Simulación de alerta de vencimiento de licencias
    docentes_con_licencia = COL_DOCENTES.find({"licencia_vto": {"$exists": True, "$ne": ""}})
    for doc in docentes_con_licencia:
        try:
            vto_date = datetime.strptime(doc['licencia_vto'], '%Y-%m-%d').date()
            if vto_date < date.today() + timedelta(days=30):
                dias_restantes = (vto_date - date.today()).days
                alertas.append({
                    "tipo": "Vencimiento",
                    "mensaje": f"La licencia de {doc['nombre']} vence en {dias_restantes} días ({doc['licencia_vto']}).",
                    "nivel": "warning" if dias_restantes > 0 else "danger"
                })
        except ValueError:
            # Ignorar si el formato de fecha es incorrecto
            pass

    return render_template('index.html', 
                           user=user, 
                           docentes_count=docentes_count, 
                           cursos_count=cursos_count,
                           set4_estado=set4_estado,
                           alertas=alertas)

@app.route("/mapa/escuela")
def ver_mapa_escuela():
    """
    Abre en una nueva pestaña el mapa centrado en la escuela.
    """
    direccion = "Tomás Edison 2164, Isidro Casanova, Buenos Aires, Argentina"
    url = f"https://www.google.com/maps/search/?api=1&query={quote(direccion)}"
    return redirect(url)
  
# ----------------- Helpers genéricos -----------------
def to_json(doc):
    d = dict(doc)
    d["_id"] = str(d["_id"])
    return d

def matriz_5x5_vacia():
    return [["" for _ in range(5)] for _ in range(5)] 

def calcular_edad(fecha_nacimiento, referencia=None):
    if not fecha_nacimiento: 
        return None
    if isinstance(fecha_nacimiento, str):
        try:
            fecha_nacimiento = datetime.strptime(fecha_nacimiento, "%Y-%m-%d").date()
        except:
            return None
    referencia = referencia or date(datetime.now().year, 6, 30)  # 30/06 año actual
    return relativedelta(referencia, fecha_nacimiento).years

def today():
    # Devuelve la fecha actual como date (útil para comparaciones y strftime)
    return date.today()

def today_datetime():
    """Retorna la fecha y hora actual con info de zona horaria (UTC)"""
    return datetime.now(timezone.utc)

def get_dias_habiles(year, month):
    """
    Calcula los días hábiles (Lunes a Viernes) para un mes/año dado.
    Retorna una lista de objetos date (solo los días hábiles).
    Esta función es crucial para la vista de asistencia.
    """
    dias_habiles = []
    # CORRECCIÓN: Usar date(year, month, 1) para el primer día
    current_date = date(year, month, 1)
    
    # Calcular el último día del mes
    # Si es Diciembre (12), el siguiente mes es Enero del próximo año
    if month == 12: 
        # El último día de Diciembre siempre es 31
        last_day = date(year, month, 31)
    else:
        # El último día del mes es el día anterior al primero del mes siguiente
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    
    while current_date <= last_day:
        # 0=Lunes, 6=Domingo. Consideramos Lunes (0) a Viernes (4) como hábiles.
        if 0 <= current_date.weekday() <= 4:
            dias_habiles.append(current_date)
        current_date += timedelta(days=1)
    
    return dias_habiles


def datetimeformat(value, format='%Y-%m-%d'):
    if isinstance(value, datetime) or isinstance(value, date):
        return value.strftime(format)
    return value

# Registrar filtro Jinja tras definir la función
app.jinja_env.filters['strftime'] = datetimeformat
  

def dias_restantes(f_limite_str):
    if not f_limite_str:
        return None
    try:
        f = datetime.strptime(f_limite_str[:10], "%Y-%m-%d").date()
    except Exception:
        return None
    try:
        return (f - today()).days
    except Exception:
        return None
MESES_MAYUS = {
    1: "ENERO", 2: "FEBRERO", 3: "MARZO", 4: "ABRIL",
    5: "MAYO", 6: "JUNIO", 7: "JULIO", 8: "AGOSTO",
    9: "SEPTIEMBRE", 10: "OCTUBRE", 11: "NOVIEMBRE", 12: "DICIEMBRE"
}

def fecha_larga_castellano(fecha):
    if isinstance(fecha, str):
        fecha = datetime.strptime(fecha, "%Y-%m-%d").date()
    return f"{fecha.day} días de {MESES_MAYUS[fecha.month]} del año {fecha.year}"

LEG_CAMPOS = [
    ("dni_menor", "Fotocopia DNI alumno/a"),
    ("partida_nacimiento", "Partida de nacimiento"),
    ("vacunas_calendario", "Vacunas de calendario"),
    ("vacunas_covid", "Vacunas COVID"),
    ("dni_responsables", "DNI madre/padre/tutor"),
    ("dni_autorizados", "DNI de autorizados a retirar"),
]

# --------- Helpers Inasistencias (normalización y topes) ----------
import unicodedata

def _normalizar_texto(txt: str) -> str:
    """
    Pone en minúsculas y saca tildes para poder comparar causas
    sin preocuparnos por acentos.
    """
    if not txt:
        return ""
    txt = txt.strip().lower()
    # quitar tildes
    txt = unicodedata.normalize("NFD", txt)
    txt = "".join(c for c in txt if unicodedata.category(c) != "Mn")
    return txt


def _norm(s: str) -> str:
    """
    Versión corta: simplemente delega en _normalizar_texto.
    (así evitamos el lío del maketrans y el ValueError)
    """
    return _normalizar_texto(s)


def _es_citacion_otro_est(c_norm: str) -> bool:
    """
    Devuelve True si la causa normalizada corresponde a
    'citacion / convocatoria en otro establecimiento'.
    """
    c = c_norm
    return (
        ("citacion" in c or "convocatoria" in c)
        and (
            "otro establecimiento" in c
            or "otros establecimientos" in c
            or "otro est" in c
        )
    )


def _is_preexamen(causa_norm: str) -> bool:
    return "pre" in causa_norm and "examen" in causa_norm


def _causa_bucket(causa_norm: str) -> str:
    c2 = causa_norm

    if "enfermedad personal" in c2:
        return "enfermedad_personal"
    if "enfermedad familiar" in c2:
        return "enfermedad_familiar"
    if "particular" in c2:
        return "particulares"

    # Citación / convocatoria en otro establecimiento (semáforo VERDE)
    if _es_citacion_otro_est(c2):
        return "convocatoria_otros_establecimientos"

    if "injustificada" in c2:
        return "injustificadas"

    return "otras"


def _color_for_causa(causa: str) -> str:
    """
    Color para pintar tanto historial como calendario anual:
    - VERDE: citación/convocatoria en otro establecimiento
    - ROJO: todas las demás causas
    (devolvemos códigos HEX porque el template usa background-color: {{ celda.color }})
    """
    c2 = _normalizar_texto(causa)

    if _es_citacion_otro_est(c2):
        return "#28a745"   # verde

    return "#dc3545"       # rojo


def _parse_date(valor):
    """
    Intenta convertir lo que venga a date:
    - date => se devuelve tal cual
    - '2025-11-27'
    - '27/11/2025'
    """
    if isinstance(valor, date):
        return valor
    if not valor:
        return None

    s = str(valor).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _year_bounds(d):
    base = d or date.today()
    return date(base.year, 1, 1), date(base.year, 12, 31)


def _maybe_oid(x): 
    if isinstance(x, ObjectId):
        return x
    try:
        return ObjectId(str(x))
    except Exception:
        return str(x)


LIMITES_ANUALES = {
    "preexamen": 12,
    "enfermedad_personal": 25,
    "enfermedad_familiar": 20,
    "particulares": 6,
}


def _limites_restantes(docente_id_raw, referencia_fecha=None):
    docente_id = _maybe_oid(docente_id_raw)
    y1, y2 = _year_bounds(referencia_fecha)

    # NOTA: guardamos 'fecha' como ISO "YYYY-MM-DD". Si la tuvieras como date, hay que adaptar el filtro.
    q = {"docente_id": docente_id, "fecha": {"$gte": y1.isoformat(), "$lte": y2.isoformat()}}

    cont = {
        "preexamen": 0,
        "enfermedad_personal": 0,
        "enfermedad_familiar": 0,
        "particulares": 0,
        "injustificadas": 0,
        "otras": 0
    }
    meses_particulares = {}

    for ins in COL_INASISTENCIAS.find(q):
        causa_norm = _norm(ins.get("causa"))
        bucket = _causa_bucket(causa_norm)
        fecha_s = ins.get("fecha")
        d = _parse_date(fecha_s)

        if _is_preexamen(causa_norm): 
            cont["preexamen"] += 1

        if bucket in cont: 
            cont[bucket] += 1
        else:
            cont["otras"] += 1

        if bucket == "particulares" and d:
            key = f"{d.year}-{d.month:02d}"
            meses_particulares[key] = meses_particulares.get(key, 0) + 1

    restantes = {
        "preexamen": max(0, LIMITES_ANUALES["preexamen"] - cont["preexamen"]),
        "enfermedad_personal": max(0, LIMITES_ANUALES["enfermedad_personal"] - cont["enfermedad_personal"]),
        "enfermedad_familiar": max(0, LIMITES_ANUALES["enfermedad_familiar"] - cont["enfermedad_familiar"]),
        "particulares": max(0, LIMITES_ANUALES["particulares"] - cont["particulares"]),
    }
    particulares_mes_lleno = {k: (v >= 1) for k, v in meses_particulares.items()}

    return {
        "consumos": cont,
        "restantes": restantes,
        "particulares_mes_lleno": particulares_mes_lleno
    }


# ----------------- DOCENTES -----------------
@app.route("/docentes")
def listar_docentes():
    docentes = []
    for d in COL_DOCENTES.find().sort([("apellido", 1), ("nombre", 1)]):
        dj = to_json(d)
        if not isinstance(dj.get("carga_horaria"), list) or len(dj.get("carga_horaria", [])) != 5:
            dj["carga_horaria"] = matriz_5x5_vacia()
        docentes.append(dj)
    return render_template("docentes.html", docentes=docentes)

@app.route("/docentes/nuevo", methods=["POST"])
def nuevo_docente():
    data = request.form.to_dict()

    # Cargos múltiples (select multiple)
    cargos = sorted(set(c.strip() for c in request.form.getlist("cargo") if c.strip()))
    if cargos:
        data["cargo"] = ", ".join(cargos)
    else:
        data["cargo"] = ""

    data.setdefault("situacion", "")
    data["carga_horaria"] = matriz_5x5_vacia()

    COL_DOCENTES.insert_one(data)
    return redirect(url_for("listar_docentes"))

@app.route("/docentes/<id>/editar", methods=["POST"])
def editar_docente(id):
    updates = request.form.to_dict()

    # Cargos múltiples
    cargos = request.form.getlist("cargo")
    if cargos:
        updates["cargo"] = ", ".join(c for c in cargos if c)

    # nunca sobrescribir la carga horaria
    updates.pop("carga_horaria", None)

    try:
        COL_DOCENTES.update_one({"_id": ObjectId(id)}, {"$set": updates})
    except Exception:
        abort(400)

    return redirect(url_for("listar_docentes")) 


@app.route("/docentes/<id>/eliminar", methods=["POST"])
def eliminar_docente(id):
    COL_DOCENTES.delete_one({"_id": ObjectId(id)})
    COL_INASISTENCIAS.delete_many({"docente_id": id})
    COL_CALIFICACIONES.delete_many({"docente_id": id})
    COL_ESTADOS_ADMIN.delete_many({"docente_id": id})
    return redirect(url_for("listar_docentes"))

@app.route("/api/docentes")
def api_docentes():
    out = []
    for d in COL_DOCENTES.find().sort([("apellido", 1), ("nombre", 1)]):
        out.append({
            "_id": str(d["_id"]),
            "nombre": d.get("nombre", ""),
            "apellido": d.get("apellido", ""),
            "cargo": d.get("cargo", "")
        })
    return jsonify(out)

@app.route("/docentes/<id>/carga_horaria", methods=["POST"]) 
def docente_carga_horaria(id): 
    payload = request.get_json(silent=True) or {}
    matriz = payload.get("carga_horaria")
    if not matriz or not isinstance(matriz, list) or len(matriz) != 5 or any(len(r) != 5 for r in matriz):
        return jsonify({"error": "Formato inválido. Se espera matriz 5x5."}), 400
    COL_DOCENTES.update_one({"_id": ObjectId(id)}, {"$set": {"carga_horaria": matriz}})
    return jsonify({"status": "ok"})


# --- SET4 helpers/route/pdf/mail ---
def _fetch_set4_context(docente_id, desde, hasta):
    d = COL_DOCENTES.find_one({"_id": ObjectId(docente_id)})
    if not d:
        return None, None, None

    # Construir la consulta con docente_id y, si se proporcionan, el rango de fechas
    q = {"docente_id": _maybe_oid(docente_id)}
    if desde and hasta:
        q["fecha"] = {"$gte": desde, "$lte": hasta}


    totales = {
    "enfermedad_personal": 0,
    "enfermedad_familiar": 0, 
    "particulares": 0,
    "citacion": 0,
    "injustificadas": 0,
    "pre_examen": 0,
    "duelo": 0,
    "examen": 0,
    "paro": 0,
    "otras": 0, 
    "suma": 0 
}

    
    lista = [] 
    for ins in COL_INASISTENCIAS.find(q).sort("fecha", 1): 
        causa_raw = ins.get("causa", "") or ""
        c2 = _normalizar_texto(causa_raw)
        if "enfermedad personal" in c2:
             totales["enfermedad_personal"] += 1

        elif "enfermedad familiar" in c2:
             totales["enfermedad_familiar"] += 1

        elif "particular" in c2:
             totales["particulares"] += 1

        elif "pre" in c2 and "examen" in c2:
              totales["pre_examen"] += 1

        elif "duelo" in c2:
             totales["duelo"] += 1

        elif "examen" in c2:
             totales["examen"] += 1

        elif "paro" in c2:
             totales["paro"] += 1

        elif "citacion" in c2 and "otro" in c2 and "establecimiento" in c2:
             totales["citacion"] += 1

        elif "injustificada" in c2:
            totales["injustificadas"] += 1

        else:
            totales["otras"] += 1


        lista.append({
            "fecha": ins.get("fecha", ""),
            "causa": causa_raw,
            "observaciones": ins.get("observaciones", "")
        })

    totales["suma"] = sum(v for k, v in totales.items() if k != "suma")

    periodo = {"desde": desde or "", "hasta": hasta or ""}
    return d, periodo, (totales, lista)

@app.route("/docentes/<id>/set4")
def docente_set4(id):
    try:
        _ = ObjectId(id)
    except:
        abort(404)
    desde = request.args.get("desde")
    hasta = request.args.get("hasta")
    d, periodo, pack = _fetch_set4_context(id, desde, hasta)
    if not d:
        abort(404)
    totales, inasistencias = pack
    return render_template("set4_calificacion.html",
                           docente=d, periodo=periodo,
                           totales=totales, inasistencias=inasistencias)

# @app.route("/docentes/<id>/set4.pdf")
# def docente_set4_pdf(id):
#     try:
#         _ = ObjectId(id)
#     except:
#         abort(404)
#     desde = request.args.get("desde")
#     hasta = request.args.get("hasta")
#     d, periodo, pack = _fetch_set4_context(id, desde, hasta)
#     if not d:
#         abort(404)
#     totales, inasistencias = pack
#     html = render_template("set4_calificacion.html",
#                            docente=d, periodo=periodo,
#                            totales=totales, inasistencias=inasistencias)
#     pdf_io = BytesIO()
#     HTML(string=html, base_url=request.host_url).write_pdf(pdf_io)
#     pdf_io.seek(0)
#     filename = f"SET4_{d.get('apellido','')}_{d.get('nombre','')}_{(desde or '')}_{(hasta or '')}.pdf"
#     return send_file(pdf_io, mimetype="application/pdf",
#                      as_attachment=True, download_name=filename)

# @app.route("/api/set4_mail", methods=["POST"])
# def set4_mail():
#     docente_id = request.args.get("docente") or request.args.get("id")
#     if not docente_id:
#         return jsonify({"ok": False, "error": "docente_id requerido"}), 400
#     try:
#         _ = ObjectId(docente_id)
#     except:
#         return jsonify({"ok": False, "error": "docente_id inválido"}), 400
#     desde = request.args.get("desde")
#     hasta = request.args.get("hasta")
#     d, periodo, pack = _fetch_set4_context(docente_id, desde, hasta)
#     if not d:
#         return jsonify({"ok": False, "error": "docente no encontrado"}), 404
#     totales, inasistencias = pack
#     html = render_template("set4_calificacion.html",
#                            docente=d, periodo=periodo,
#                            totales=totales, inasistencias=inasistencias)
#     pdf_io = BytesIO()
#     HTML(string=html, base_url=request.host_url).write_pdf(pdf_io)
#     pdf_io.seek(0)
#     pdf_bytes = pdf_io.read()
#     subject = f"SET4 - {d.get('apellido','')}, {d.get('nombre','')} ({desde} a {hasta})"
#     body = f"""
#     <p>Adjunto SET4 del docente <b>{d.get('apellido','')}, {d.get('nombre','')}</b>.</p>
#     <p>Período: {desde} a {hasta}</p>
#     <p>Ver en sistema: /docentes/{docente_id}/set4?desde={desde}&hasta={hasta}</p>
#     """
#     ok, err = send_email(subject, body, to_list=None,
#                          attachment=("SET4.pdf", pdf_bytes, "application/pdf"))
#     return jsonify({"ok": ok, "error": err if not ok else None})



@app.route("/docentes/<id>/inasistencias_anuales")
def docente_inasistencias_anuales(id):
    """
    Hoja anual de inasistencias para un docente:
    - Calendario de lunes a viernes (enero–diciembre) del año elegido
    - Colores por día según causa (rojo / verde)
    - Resumen numérico tipo punto 14 del SET4
    """
    try:
        oid = ObjectId(id)
    except Exception:
        abort(404)

    # Año a mostrar (por defecto el año actual)
    anio_param = (request.args.get("anio") or "").strip()
    try:
        anio = int(anio_param) if anio_param else date.today().year
    except ValueError:
        anio = date.today().year

    docente = COL_DOCENTES.find_one({"_id": oid})
    if not docente:
        abort(404)

    # Rango anual en formato ISO (para que coincida con lo que usa la DB)
    desde_iso = date(anio, 1, 1).isoformat()
    hasta_iso = date(anio, 12, 31).isoformat()

    # Resumen numérico (aprovechamos el helper del SET4)
    d_set4, periodo, pack = _fetch_set4_context(id, desde_iso, hasta_iso)
    if not d_set4:
        totales = {
            "enfermedad_personal": 0,
            "enfermedad_familiar": 0,
            "particulares": 0,
            "citacion": 0,
            "injustificadas": 0,
            "duelo": 0,
            "examen": 0,
            "paro": 0,
            "pre_examen": 0,
            "otras": 0,
            "suma": 0,
        }
        lista_inasistencias = []
        periodo = {"desde": desde_iso, "hasta": hasta_iso}

    else:
        totales, lista_inasistencias = pack

    # Mapa (mes, día) -> color para pintar el calendario
    faltas_por_fecha = {}
    q = {
        "docente_id": _maybe_oid(id),
        "fecha": {"$gte": desde_iso, "$lte": hasta_iso},
    }

    for ins in COL_INASISTENCIAS.find(q):
        f = _parse_date(ins.get("fecha"))
        if not f:
            continue
        causa = (ins.get("causa") or "").strip()
        color = _color_for_causa(causa)
        faltas_por_fecha[(f.month, f.day)] = color

    # Armamos el calendario anual por mes, usando sólo días hábiles (lunes-viernes)
    meses_data = []
    for mes in range(1, 13):
        dias_habiles = get_dias_habiles(anio, mes) 
        filas = []
        fila_actual = [None] * 5  # columnas: L, M, M, J, V
        last_weekday = None

        for f in dias_habiles:
            wd = f.weekday()  # 0=Lunes ... 4=Viernes
            if last_weekday is not None and wd == 0:
                if any(c is not None for c in fila_actual):
                    filas.append(fila_actual)
                fila_actual = [None] * 5

            fila_actual[wd] = {
                "dia": f.day,
                "color": faltas_por_fecha.get((mes, f.day)) 
            }
            last_weekday = wd

        if any(c is not None for c in fila_actual):
            filas.append(fila_actual)

        meses_data.append({
            "num": mes,
            "nombre": MESES_MAYUS.get(mes, str(mes)),
            "filas": filas,
        })

    # 👉 ACÁ definimos la fecha de impresión
    fecha_impresion = date.today()

    # Ajustamos período para que quede explícito el año completo
    periodo = {"desde": desde_iso, "hasta": hasta_iso}

    return render_template(
        "docente_inasistencias_calendario.html",
        docente=docente,
        anio=anio,
        meses=meses_data,
        totales=totales,
        periodo=periodo,
        fecha_impresion=fecha_impresion, 
    )


# ----------------- ALUMNOS -----------------

def _orden_alumno(a):
    """
    Orden:
      1) Turno mañana (A) -> 0, turno tarde (B) -> 1, otros -> 2
      2) Grado 1..6
      3) Sección (A/B)
      4) Apellido, Nombre
    """
    curso = (a.get("curso") or "").upper().replace("º", "°").strip()
    turno_rank = 2
    seccion = ""
    grado = 99

    # Detectamos A/B como secciones válidas
    # Importante: asumimos formato limpio tipo "1° A", "2° B", etc.
    if "A" in curso:
        seccion = "A"
    if "B" in curso:
        # si llegara a tener "A" y "B", gana B sólo si corresponde
        # pero en tu caso no pasa; es defensivo
        if curso.endswith("B") or " B" in curso:
            seccion = "B"

    # Turno por sección
    if seccion == "A":
        turno_rank = 0  # mañana
    elif seccion == "B":
        turno_rank = 1  # tarde

    # Grado (1..6)
    for g in ("1", "2", "3", "4", "5", "6"):
        if curso.startswith(g):
            grado = int(g)
            break

    return (
        turno_rank,
        grado,
        seccion,
        (a.get("apellido") or "").upper(),
        (a.get("nombre") or "").upper()
    )
@app.route("/alumnos")
def listar_alumnos():
    # ----------------- PARÁMETROS -----------------
    q = (request.args.get("q") or "").strip()
    curso_sel = (request.args.get("curso") or "").strip()
    turno_sel = (request.args.get("turno") or "").strip()
    ver_historico = (request.args.get("historico") == "1")

    turnos = ["Mañana", "Tarde"]

    # ----------------- FILTRO BASE -----------------
    filtro = {}

    # Búsqueda de texto
    if q:
        regex = {"$regex": q, "$options": "i"}
        filtro["$or"] = [
            {"apellido": regex},
            {"nombre": regex},
            {"dni": regex},
            {"curso": regex},
            {"apellido_nombre": regex},
        ]

    # Filtro por curso / turno
    if curso_sel:
        filtro["curso"] = {"$regex": f"^{curso_sel}$", "$options": "i"}
    elif turno_sel:
        if turno_sel.lower() == "mañana":
            filtro["curso"] = {"$regex": "A$", "$options": "i"}
        elif turno_sel.lower() == "tarde":
            filtro["curso"] = {"$regex": "B$", "$options": "i"}

    # ----------------- CONSULTA A MONGO -----------------
    if ver_historico and curso_sel:
        # Activos + históricos del curso
        alumnos_cur = COL_ALUMNOS.find({
            **filtro,
            "$or": [
                {"fecha_salida": {"$in": [None, "", False]}},
                {"curso_origen": curso_sel},
                {"curso": curso_sel},
            ]
        })
    else:
        # Solo activos (comportamiento normal)
        alumnos_cur = COL_ALUMNOS.find({**filtro_activos(), **filtro})

    alumnos = [to_json(a) for a in alumnos_cur]

    # ----------------- FLAGS PARA EL TEMPLATE -----------------
    for a in alumnos:
        fs = a.get("fecha_salida")
        a["esta_activo"] = not fs

        a["historico_en_curso"] = False
        if curso_sel:
            # Baja definitiva en ese curso
            if fs and a.get("curso") == curso_sel:
                a["historico_en_curso"] = True
            # Cambio de turno desde ese curso
            if a.get("curso_origen") == curso_sel:
                a["historico_en_curso"] = True
        mot = (a.get("motivo_salida") or "").strip().upper()
        dest = (a.get("destino_salida") or "").strip()
        curso_dest = (a.get("curso_destino") or "").strip()

        sale_a = ""
        if mot == "CAMBIO DE TURNO":
           sale_a = f"CAMBIO TURNO → {curso_dest}" if curso_dest else "CAMBIO DE TURNO"
        elif mot == "EGRESO":
           sale_a = "EGRESO (Finalización Primaria)"
        elif mot == "PASE A OTRA ESCUELA":
           sale_a = f"PASE → {dest}" if dest else "PASE A OTRA ESCUELA"
        elif mot:
           sale_a = f"{mot} → {dest}" if dest else mot

        a["sale_a_str"] = sale_a

    # ----------------- ORDEN -----------------
    alumnos.sort(key=_orden_alumno)

    # ----------------- EDAD / FECHAS -----------------
    from datetime import datetime, date
    REF = date(datetime.now().year, 6, 30)

    for a in alumnos:
        fn = a.get("fecha_nacimiento")
        if fn:
            try:
                y, m, d = map(int, fn.split("-"))
                nac = date(y, m, d)
                edad = REF.year - nac.year - ((REF.month, REF.day) < (nac.month, nac.day))
                a["edad_30jun"] = edad
                a["fecha_nac_str"] = f"{d:02d}/{m:02d}/{y}"
                a["fecha_nac_iso"] = fn
            except Exception:
                a["edad_30jun"] = ""
                a["fecha_nac_str"] = ""
                a["fecha_nac_iso"] = ""
        else:
            a["edad_30jun"] = ""
            a["fecha_nac_str"] = ""
            a["fecha_nac_iso"] = ""

    # ----------------- CURSOS PARA EL COMBO -----------------
    cursos = sorted(
        [c for c in COL_ALUMNOS.distinct("curso") if c],
        key=lambda x: str(x)
    ) 

    # ----------------- RENDER -----------------
    return render_template(
        "alumnos.html",
        alumnos=alumnos,
        q=q,
        curso_sel=curso_sel,
        cursos=cursos,
        turno_sel=turno_sel,
        turnos=turnos,
        ver_historico=ver_historico,
        hoy_str=today().strftime("%Y-%m-%d"),
    )


# app.py ~ Después de def listar_alumnos(): (Ej: Línea 430)

def get_dias_mes(year, month):
    """
    Genera una lista de días del mes, filtrando Sábados y Domingos.
    Esto representa los posibles "días hábiles" si la escuela abre.
    """
    dias = []
    try:
        current_date = date(year, month, 1)
    except ValueError:
        return [] # Manejo de errores de mes o año inválido
        
    # Lista de días hábiles de la semana (Lunes=0, Domingo=6)
    DIAS_HABILITADOS = [0, 1, 2, 3, 4] # Lunes a Viernes

    while current_date.month == month:
        if current_date.weekday() in DIAS_HABILITADOS:
            dias.append({
                "fecha": current_date,
                # Formato de día de la semana para la cabecera (Lun, Mar, etc.)
                "dia_semana": current_date.strftime("%a").capitalize().replace('.', ''), 
                "num_dia": current_date.day,
            })
        current_date += timedelta(days=1)
    return dias


# Pequeña ruta para seleccionar el curso y mes por defecto
@app.route("/asistencia", methods=["GET"])
def seleccionar_asistencia():
    """Redirige al mes y curso por defecto."""
    COL_ALUMNOS = app.mongo.alumnos 
    
    today_date = date.today()
    
    # Obtener el primer curso disponible para redirigir
    cursos_disponibles = COL_ALUMNOS.distinct("curso", filtro_activos())
    primer_curso = sorted(cursos_disponibles)[0] if cursos_disponibles else "1°A"
    
    return redirect(url_for(
        "asistencia_mensual", 
        curso=primer_curso, 
        year=today_date.year, 
        month=today_date.month
    ))

@app.route('/asistencia/<curso>/<int:year>/<int:month>', methods=['GET', 'POST'])
def asistencia_mensual(curso, year, month):
    """
    Maneja la visualización y guardado (GET y POST) de la asistencia mensual.
    """

    # Cursos disponibles para el selector
    cursos = sorted([c for c in COL_ALUMNOS.distinct("curso", filtro_activos()) if c])

    # Obtener días hábiles
    try:
        dias_habil_registrados = get_dias_habiles(year, month)

        if not dias_habil_registrados:
            print(f"DEBUG ASISTENCIA: get_dias_habiles({year}, {month}) devolvió lista vacía.")
        else:
            print(
                f"DEBUG ASISTENCIA: Días hábiles encontrados: {len(dias_habil_registrados)}. "
                f"Primer día: {dias_habil_registrados[0].isoformat()}. "
                f"Último día: {dias_habil_registrados[-1].isoformat()}"
            )

    except ValueError:
        abort(400, description="Fecha inválida.")

    # Fechas para navegación
    current_date = date(year, month, 1)
    prev_month_date = current_date - relativedelta(months=1)
    next_month_date = current_date + relativedelta(months=1)

    # Mes en castellano
    meses_es = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
    ]
    mes_actual = f"{meses_es[current_date.month - 1].capitalize()} de {current_date.year}"

    # ----------------------------------------------------------------------
    #                          PROCESAR POST
    # ----------------------------------------------------------------------
    if request.method == 'POST':

        for key, value in request.form.items():
            if not key.startswith('asistencia_'):
                continue

            try:
                # asistencia_<alumno_id>_<YYYY-MM-DD>
                parts = key.split('_')
                alumno_id_str = parts[1]
                asistencia_date_iso = parts[2]
                estado = value.upper().strip()

                query = {
                    'alumno_id': ObjectId(alumno_id_str),
                    'fecha': asistencia_date_iso,
                    'curso': curso,
                }

                if estado in ['P', 'A', 'X']:
                    update = {'$set': {'estado': estado}}
                    COL_ASISTENCIA.update_one(query, update, upsert=True)
                elif estado == '':
                    COL_ASISTENCIA.delete_one(query)

            except Exception as e:
                print(f"[ERROR ASISTENCIA] clave '{key}' valor '{value}': {e}")
                continue

        # Post-Redirect-Get
        return redirect(url_for('asistencia_mensual', curso=curso, year=year, month=month))

    # ----------------------------------------------------------------------
    #                          PROCESAR GET
    # ----------------------------------------------------------------------

    # Alumnos ordenados por apellido
    alumnos = list(COL_ALUMNOS.find({**filtro_activos(), "curso": curso}).sort("apellido", 1))

    # Rango de fechas del mes
    start_date = date(year, month, 1)
    last_day_of_month = date(year, month, 1) + relativedelta(months=1) - timedelta(days=1)

    start_date_iso = start_date.isoformat()
    end_date_iso = last_day_of_month.isoformat()

    # Obtener asistencias del mes (lista, no cursor)
    asistencias_mes = list(COL_ASISTENCIA.find({
        'curso': curso,
        'fecha': {'$gte': start_date_iso, '$lte': end_date_iso}
    }))

    # Mapeo: alumno → {fecha → estado}
    asistencia_map = {}
    for reg in asistencias_mes:
        try:
            fecha_iso = reg['fecha']
            alumno_id = str(reg['alumno_id'])

            if alumno_id not in asistencia_map:
                asistencia_map[alumno_id] = {}

            asistencia_map[alumno_id][fecha_iso] = reg.get('estado', '')

        except Exception as e:
            print(f"ERROR MAPEO ASISTENCIA: {e}")
            continue

    # Unir alumnos con sus asistencias
    alumnos_con_asistencia = []
    for alumno in alumnos:
        alumno_id = str(alumno['_id'])
        alumno['asistencia_mensual'] = asistencia_map.get(alumno_id, {})
        alumnos_con_asistencia.append(alumno)

    # ------------------------------------------------------------------
    #                     RESUMEN SEMANAL (MES ACTUAL)
    # ------------------------------------------------------------------
    resumen_semanal = {}   # {alumno_id: {semana: {"P":x,"A":y,"D":z}}}
    max_semana = 0

    for reg in asistencias_mes:
        try:
            estado = reg.get('estado', '') 
            if estado not in ('P', 'A'):
                continue

            fecha_iso = reg['fecha']
            fecha = date.fromisoformat(fecha_iso)
            semana = (fecha.day - 1) // 7 + 1  # Semana 1..5

            aid = str(reg['alumno_id'])
            semanas_map = resumen_semanal.setdefault(aid, {})
            info = semanas_map.setdefault(semana, {'P': 0, 'A': 0, 'D': 0})

            info[estado] += 1
            info['D'] += 1

            if semana > max_semana:
                max_semana = semana

        except Exception as e:
            print(f"ERROR RESUMEN SEMANAL: {e}")
            continue

    # Si no hubo asistencias cargadas, igual determinamos cuántas semanas tiene el mes
    if max_semana == 0 and dias_habil_registrados:
        max_semana = max((d.day - 1) // 7 + 1 for d in dias_habil_registrados)
    if max_semana == 0:
        max_semana = 4  # valor por defecto

    # ------------------------------------------------------------------
    #              RESÚMENES TRIMESTRAL Y ANUAL POR ALUMNO
    # ------------------------------------------------------------------

    # Trimestre actual (1: meses 1-3, 2: 4-6, 3: 7-9, 4: 10-12)
    trimestre_idx = (month - 1) // 3          # 0,1,2,3
    tri_start_month = trimestre_idx * 3 + 1   # 1,4,7,10
    tri_end_month = tri_start_month + 2       # 3,6,9,12

    tri_start_date = date(year, tri_start_month, 1)
    tri_end_date = date(year, tri_end_month, 1) + relativedelta(months=1) - timedelta(days=1)

    year_start_date = date(year, 1, 1)
    year_end_date = date(year, 12, 31)

    # Asistencias del trimestre
    tri_asist = COL_ASISTENCIA.find({
        'curso': curso,
        'fecha': {
            '$gte': tri_start_date.isoformat(),
            '$lte': tri_end_date.isoformat()
        }
    })

    # Asistencias del año
    year_asist = COL_ASISTENCIA.find({
        'curso': curso,
        'fecha': {
            '$gte': year_start_date.isoformat(),
            '$lte': year_end_date.isoformat()
        } 
    })

    resumen_trimestre = {}  # {alumno_id: {"P":x, "A":y, "D":z}}
    for reg in tri_asist:
        aid = str(reg['alumno_id'])
        estado = reg.get('estado', '')
        if estado not in ('P', 'A'):
            continue
        info = resumen_trimestre.setdefault(aid, {'P': 0, 'A': 0, 'D': 0})
        info[estado] += 1
        info['D'] += 1

    resumen_anual = {}      # {alumno_id: {"P":x, "A":y, "D":z}}
    for reg in year_asist:
        aid = str(reg['alumno_id'])
        estado = reg.get('estado', '')
        if estado not in ('P', 'A'):
            continue
        info = resumen_anual.setdefault(aid, {'P': 0, 'A': 0, 'D': 0})
        info[estado] += 1
        info['D'] += 1

    numero_trimestre = trimestre_idx + 1

    # ----------------------------------------------------------------------
    #                           RENDER TEMPLATE
    # ----------------------------------------------------------------------
    return render_template(
        'asistencia_mensual.html',
        curso=curso,
        cursos=cursos,
        alumnos=alumnos_con_asistencia,
        mes_actual=mes_actual,
        dias_habiles=dias_habil_registrados,
        current_year=year,
        current_month=month,
        prev_year=prev_month_date.year,
        prev_month=prev_month_date.month,
        next_year=next_month_date.year,
        next_month=next_month_date.month,
        today_func=today(),
        resumen_semanal=resumen_semanal,
        max_semana=max_semana,
        resumen_trimestre=resumen_trimestre,
        resumen_anual=resumen_anual,
        numero_trimestre=numero_trimestre,
    )



@app.route("/alumnos/<id>/mapa")
def mapa_alumno(id):
    a = COL_ALUMNOS.find_one({"_id": ObjectId(id)})
    if not a:
        abort(404)

    domicilio = (a.get("domicilio") or "").strip()
    localidad = (a.get("localidad") or "Isidro Casanova").strip()
    provincia = (a.get("provincia") or "Buenos Aires").strip()

    if not domicilio:
        # Sin domicilio no tiene sentido el mapa
        abort(404)

    destino = f"{domicilio}, {localidad}, {provincia}, Argentina"
    url = (
        "https://www.google.com/maps/dir/?api=1"
        f"&origin={quote('Tomás Edison 2164, Isidro Casanova, Buenos Aires, Argentina')}"
        f"&destination={quote(destino)}"
    )
    return redirect(url)

# ----------------- CALIFICACIONES (vista principal) -----------------

@app.route("/calificaciones")
def calificaciones_gestion():
    """
    Redirige a la vista única de gestión de calificaciones.
    Así evitamos tener dos rutas distintas con datos distintos.
    """
    return redirect(url_for("calificaciones_gestionar"))


@app.route("/calificaciones/gestionar")
def calificaciones_gestionar():
    """
    Pantalla principal de gestión de calificaciones.
    - Docentes para el combo
    - Configuración de escalas por asignatura
    - Lista fija de asignaturas (incluye MG)
    """
    docentes = [to_json(d) for d in COL_DOCENTES.find().sort([("apellido", 1), ("nombre", 1)])]

    cfg_cur = {
        c.get("asignatura"): c.get("escala", "conceptual")
        for c in COL_CFG_ASIGNATURAS.find({})
    }

    asignaturas = [
        "Prácticas del Lenguaje",
        "Matemática",
        "Ciencias Sociales",
        "Ciencias Naturales",
        "Inglés",
        "Danza",
        "Artística",
        "Música",
        "Educación Física",
        "Quinta Hora",
        
    ]

    return render_template(
        "calificaciones_gestion.html",
        docentes=docentes,
        cfg_asignaturas=cfg_cur,
        asignaturas=asignaturas,
    )


# ----------------- RESUMEN EDADES POR CURSO -----------------

from collections import defaultdict
from datetime import date, datetime 

def _orden_curso(curso: str):
    """
    Ordena cursos como:
      Turno mañana (A) 1°A..6°A, luego turno tarde (B) 1°B..6°B, luego el resto.
    """
    c = (curso or "").upper().replace("º", "°").replace(" ", "")
    turno = 2
    grado = 99
    seccion = ""

    if c[:1].isdigit():
        try:
            grado = int(c[0])
        except ValueError:
            grado = 99

    if "A" in c:
        turno = 0
        seccion = "A"
    elif "B" in c:
        turno = 1
        seccion = "B"

    return (turno, grado, seccion, c)



MESES_ES = [ 
    None,          # índice 0 (no se usa)
    "Enero", 
    "Febrero",
    "Marzo",
    "Abril",
    "Mayo",
    "Junio",
    "Julio",
    "Agosto",
    "Septiembre",
    "Octubre",
    "Noviembre",
    "Diciembre",
]

CURSOS = ["1º", "2º", "3º", "4º", "5º", "6º"]

def calcular_matricula_mensual(mes, anio):
    """
    Matrícula activa en el mes/anio dado.

    Activo = ingresó antes o durante el mes y
             NO tiene EGRESO ni PASE A OTRA ESCUELA efectivo
             antes o dentro de ese mes.

    Separa por curso (1º..6º) y sección A/B (A = mañana, B = tarde).
    """

    # 1. Rango del mes (fechas NAIVE, sin tz)
    try:
        primer_dia = datetime(anio, mes, 1)
        ultimo_dia = primer_dia + relativedelta(months=1) - timedelta(seconds=1)
    except ValueError:
        # Si vino algo raro en mes/año, usamos la fecha actual
        hoy = today()
        mes = hoy.month
        anio = hoy.year
        primer_dia = datetime(anio, mes, 1)
        ultimo_dia = primer_dia + relativedelta(months=1) - timedelta(seconds=1)

    # 2. Estructura base del parte
    estructura_parte = {}
    for curso in CURSOS:
        estructura_parte[f"{curso} A"] = {"VARONES": 0, "MUJERES": 0, "TOTAL": 0}
        estructura_parte[f"{curso} B"] = {"VARONES": 0, "MUJERES": 0, "TOTAL": 0}

    # 3. Alumnos desde Mongo
    alumnos = mongo.db.alumnos.find({})

    # Helper local para parsear fechas que puedan venir como str o datetime
    def parse_fecha(valor, default=None):
        if isinstance(valor, datetime):
            return valor
        if isinstance(valor, str) and valor.strip():
            try:
                # 'YYYY-MM-DD' o 'YYYY-MM-DDTHH:MM:SS'
                return datetime.fromisoformat(valor.strip())
            except ValueError:
                pass
        return default

    inicio_clases = datetime(1900, 1, 1)

    for alumno in alumnos:
        # === 3.1 Fechas de ingreso / salida ===
        fecha_ingreso = parse_fecha(alumno.get("fecha_ingreso"), default=inicio_clases)
        fecha_salida  = parse_fecha(alumno.get("fecha_salida"),  default=None)

        if fecha_ingreso is None:
            fecha_ingreso = inicio_clases

        motivo = (alumno.get("motivo_salida") or "").strip().upper()

        # Es baja definitiva si tiene EGRESO o PASE y fecha_salida efectiva
        es_baja_definitiva = (
    fecha_salida is not None
    and fecha_salida <= ultimo_dia
)

    

        # Activo en el mes si ingresó antes/durante y no tiene baja definitiva
        esta_activo = (fecha_ingreso <= ultimo_dia) and not es_baja_definitiva
        if not esta_activo:
            continue

        # === 3.2 Curso y sección desde "curso" (1°A, 2°B, etc.) ===
        curso_raw = (alumno.get("curso") or "").upper().replace("º", "°").replace(" ", "")
        if not curso_raw:
            continue

        grado_str = None
        for g in ("1", "2", "3", "4", "5", "6"):
            if curso_raw.startswith(g):
                grado_str = f"{g}º"
                break
        if grado_str is None:
            continue

        if "A" in curso_raw:
            seccion = "A"
        elif "B" in curso_raw:
            seccion = "B"
        else:
            continue

        clave_curso = f"{grado_str} {seccion}"
        if clave_curso not in estructura_parte:
            continue

        # === 3.3 Sexo ===
        sexo = (alumno.get("sexo") or "").strip().upper()
        if sexo not in ("M", "F"):
            continue

        if sexo == "M":
            estructura_parte[clave_curso]["VARONES"] += 1
        else:
            estructura_parte[clave_curso]["MUJERES"] += 1

        estructura_parte[clave_curso]["TOTAL"] += 1

    # 4. Totales por turno y general
    totales = defaultdict(lambda: {"VARONES": 0, "MUJERES": 0, "TOTAL": 0})
    for curso_seccion, datos in estructura_parte.items():
        if curso_seccion.endswith(" A"):
            turno = "MAÑANA"
        elif curso_seccion.endswith(" B"):
            turno = "TARDE"
        else:
            continue

        totales[turno]["VARONES"] += datos["VARONES"]
        totales[turno]["MUJERES"] += datos["MUJERES"]
        totales[turno]["TOTAL"]   += datos["TOTAL"]

    totales["GENERAL"]["VARONES"] = totales["MAÑANA"]["VARONES"] + totales["TARDE"]["VARONES"]
    totales["GENERAL"]["MUJERES"] = totales["MAÑANA"]["MUJERES"] + totales["TARDE"]["MUJERES"]
    totales["GENERAL"]["TOTAL"]   = totales["MAÑANA"]["TOTAL"]   + totales["TARDE"]["TOTAL"]

    # 5. Lista de cursos para la plantilla
    lista_cursos = []
    for curso in CURSOS:
        lista_cursos.append({
            "curso":  curso,
            "manana": estructura_parte[f"{curso} A"],
            "tarde":  estructura_parte[f"{curso} B"],
        })

    # Nombre del mes en castellano (MESES_ES debe estar definido arriba)
    nombre_mes = MESES_ES[mes]

    return { 
        "mes": nombre_mes,
        "mes_numero": mes,
        "anio": anio,
        "fecha_emision": today().strftime("%d/%m/%Y"),
        "cursos": lista_cursos,
        "totales": totales,
    }

# app.py (Ruta para el Parte Diario)

@app.route("/parte_diario", methods=["GET"])
def parte_diario():
    # Obtener mes y año de la URL o usar el actual
    hoy = today()
    try:
        mes = int(request.args.get("mes", hoy.month))
        anio = int(request.args.get("anio", hoy.year))
    except (ValueError, TypeError):
        # Fallback a la fecha actual si los parámetros son inválidos
        mes = hoy.month
        anio = hoy.year

    datos = calcular_matricula_mensual(mes, anio) 

    # Preparar el mes anterior y posterior para la navegación (flechitas)
    fecha_actual = datetime(anio, mes, 1)
    mes_anterior = fecha_actual - relativedelta(months=1)
    mes_posterior = fecha_actual + relativedelta(months=1)
    
    # Asignar URLs de navegación
    datos["mes_anterior_url"] = url_for("parte_diario", mes=mes_anterior.month, anio=mes_anterior.year)
    datos["mes_posterior_url"] = url_for("parte_diario", mes=mes_posterior.month, anio=mes_posterior.year)

    return render_template("parte_diario.html", **datos)

@app.route("/resumen/edades")
def resumen_edades():
    """Cuadros-resumen por curso: edades, sexo, nacionalidades y recursantes.

    Se calcula todo dinámicamente a partir de la colección de alumnos
    usando como referencia el 30/06 del año configurado en EDADES_REF_ANIO
    (o el año actual por defecto).
    """
    ref_year = int(os.getenv("EDADES_REF_ANIO", datetime.now().year))
    ref_fecha = date(ref_year, 6, 30)

    resumen = defaultdict(
        lambda: {
            "sexo": {"M": 0, "F": 0, "X": 0, "total": 0},
            "edades": defaultdict(lambda: {"M": 0, "F": 0, "X": 0, "total": 0}),
            "nacionalidades": defaultdict(lambda: {"M": 0, "F": 0, "X": 0, "total": 0}),
            "recursantes": {"M": 0, "F": 0, "X": 0, "total": 0},
        }
    )

    for a in COL_ALUMNOS.find(filtro_activos()):

        curso = (a.get("curso") or "").strip()
        if not curso:
            continue

        # Normalizar sexo
        sexo_raw = (a.get("sexo") or "").strip().upper()
        if sexo_raw.startswith("M"):
            sexo = "M"
        elif sexo_raw.startswith("F"):
            sexo = "F"
        else:
            sexo = "X"

        r = resumen[curso]

        # Matrícula por sexo
        r["sexo"][sexo] += 1
        r["sexo"]["total"] += 1

        # Edad al 30/06
        fn = a.get("fecha_nacimiento")
        edad = calcular_edad(fn, referencia=ref_fecha) if fn else None
        if edad is not None:
            e_bucket = r["edades"][edad]
            e_bucket[sexo] += 1
            e_bucket["total"] += 1

        # Nacionalidad
                # Nacionalidad (normalizada)
        raw_nac = (a.get("nacionalidad") or "").strip()
        if not raw_nac:
            nac = "SIN DATO"
        else:
            up = raw_nac.upper().replace(".", "").strip()
            if up in ("ARG", "ARGENTINA", "ARGENTINO", "ARGENTINOS", "ARGENTINAS"):
                nac = "Argentina"
            else:
                nac = raw_nac  # se respeta el texto tal cual

        n_bucket = r["nacionalidades"][nac]
        n_bucket[sexo] += 1
        n_bucket["total"] += 1


        # Recursantes
        if a.get("recursante"):
            r["recursantes"][sexo] += 1
            r["recursantes"]["total"] += 1

    cursos = sorted(resumen.keys(), key=_orden_curso)

    return render_template(
        "resumen_edades.html",
        resumen=resumen,
        cursos=cursos,
        ref_fecha=ref_fecha,
    )
@app.route("/resumen/inasistencias")
def resumen_inasistencias():
    """
    Resumen de inasistencias docentes:
      - Cantidad por mes
      - Porcentaje sobre días hábiles del mes
      - Resumen por trimestre
      - Top de docentes con más inasistencias

    El porcentaje se calcula como:
        inasistencias_del_mes / días_hábiles_del_mes * 100
    usando get_dias_habiles(year, month).
    """
    try:
        anio = int(request.args.get("anio") or datetime.now().year)
    except Exception:
        anio = datetime.now().year

    # Traer todas las inasistencias del año
    q = {"fecha": {"$regex": f"^{anio}-"}}
    registros = list(COL_INASISTENCIAS.find(q))

    # Resumen mensual y por docente
    resumen_mensual = {m: {"mes": m, "cantidad": 0} for m in range(1, 12 + 1)}
    por_docente = defaultdict(int)

    for ins in registros:
        fecha_str = ins.get("fecha") or ""
        try:
            f = datetime.strptime(fecha_str[:10], "%Y-%m-%d").date()
        except Exception:
            continue
        if f.year != anio:
            continue

        # Mes
        resumen_mensual[f.month]["cantidad"] += 1

        # Contador por docente
        docente_id = ins.get("docente_id")
        if docente_id:
            por_docente[str(docente_id)] += 1

    # Calcular días hábiles y porcentaje por mes
    for m in range(1, 12 + 1):
        dias = get_dias_habiles(anio, m)
        dias_habiles = len(dias)
        cant = resumen_mensual[m]["cantidad"]
        porcentaje = (cant * 100 / dias_habiles) if dias_habiles else 0
        resumen_mensual[m]["dias_habiles"] = dias_habiles
        resumen_mensual[m]["porcentaje"] = round(porcentaje, 2)
        resumen_mensual[m]["mes_nombre"] = calendar.month_name[m].capitalize()

    # Resumen por trimestre (1: mar-may, 2: jun-ago, 3: sep-nov)
    resumen_trimestres = {
        1: {"trimestre": 1, "meses": [3, 4, 5], "cantidad": 0, "dias_habiles": 0},
        2: {"trimestre": 2, "meses": [6, 7, 8], "cantidad": 0, "dias_habiles": 0},
        3: {"trimestre": 3, "meses": [9, 10, 11], "cantidad": 0, "dias_habiles": 0},
    }

    for t_data in resumen_trimestres.values():
        for m in t_data["meses"]:
            t_data["cantidad"] += resumen_mensual[m]["cantidad"]
            t_data["dias_habiles"] += resumen_mensual[m]["dias_habiles"]
        if t_data["dias_habiles"]:
            t_data["porcentaje"] = round(
                t_data["cantidad"] * 100 / t_data["dias_habiles"], 2
            )
        else:
            t_data["porcentaje"] = 0.0

    # Top docentes con más inasistencias (nombre + cantidad)
    docentes_ids = list({_maybe_oid(k) for k in por_docente.keys() if k})
    nombres_docentes = {}
    if docentes_ids:
        for d in COL_DOCENTES.find({"_id": {"$in": docentes_ids}}):
            key = str(d["_id"])
            nombres_docentes[key] = f"{d.get('apellido','')}, {d.get('nombre','')}"

    top_docentes = []
    for raw_id, cant in sorted(por_docente.items(), key=lambda x: x[1], reverse=True):
        nombre = nombres_docentes.get(raw_id, "Docente sin nombre")
        top_docentes.append({"docente": nombre, "cantidad": cant})

    # Ordenar meses por número
    lista_meses = [resumen_mensual[m] for m in range(1, 13)]
    lista_trimestres = [resumen_trimestres[1], resumen_trimestres[2], resumen_trimestres[3]]

    return render_template(
        "resumen_inasistencias.html",
        anio=anio,
        meses=lista_meses,
        trimestres=lista_trimestres,
        top_docentes=top_docentes,
    )
@app.route("/resumen/calificaciones")
def resumen_calificaciones():
    # 1) IDs activos (string)
    activos_ids = [str(a["_id"]) for a in COL_ALUMNOS.find(filtro_activos(), {"_id": 1})]

    # 2) Traer SOLO calificaciones de alumnos activos
    registros = list(COL_CALIFICACIONES.find({"alumno_id": {"$in": activos_ids}}))

    por_curso = {}
    por_asig = {}

    for c in registros:
        curso = (c.get("curso") or "").strip()
        asignatura = (c.get("asignatura") or "").strip()
        try:
            trimestre = int(c.get("trimestre") or 0)
        except Exception:
            trimestre = 0

        escala = (c.get("escala") or "").strip().lower()
        valor = (str(c.get("valor") or "")).strip()

        if not (curso and asignatura and trimestre):
            continue

        key = (curso, asignatura, trimestre)
        d_curso = por_curso.setdefault(key, {"total": 0, "desaprobados": 0})
        d_asig = por_asig.setdefault(asignatura, {"total": 0, "desaprobados": 0})

        d_curso["total"] += 1
        d_asig["total"] += 1

        desap = False 
        if escala == "conceptual":
            desap = (valor in ("R", "D"))
        elif escala == "numerica":
            try:
                n = float(valor.replace(",", "."))
                desap = (n < 6.0)
            except Exception:
                desap = False

        if desap:
            d_curso["desaprobados"] += 1
            d_asig["desaprobados"] += 1

    # ... el resto igual ...

    # Transformar a listas ordenadas + porcentaje
    resumen_curso = []
    for (curso, asignatura, trimestre), vals in sorted(por_curso.items()):
        total = vals["total"]
        desap = vals["desaprobados"]
        pct = round(desap * 100 / total, 1) if total else 0.0
        resumen_curso.append(
            {
                "curso": curso,
                "asignatura": asignatura,
                "trimestre": trimestre,
                "total": total,
                "desaprobados": desap,
                "porcentaje": pct,
            }
        )

    resumen_asig = []
    for asignatura, vals in sorted(por_asig.items()):
        total = vals["total"]
        desap = vals["desaprobados"]
        pct = round(desap * 100 / total, 1) if total else 0.0
        resumen_asig.append(
            {
                "asignatura": asignatura,
                "total": total,
                "desaprobados": desap,
                "porcentaje": pct,
            }
        )

    return render_template(
        "resumen_calificaciones.html",
        resumen_curso=resumen_curso,
        resumen_asig=resumen_asig,
    )

@app.route("/alumnos/nuevo", methods=["POST"])
def nuevo_alumno():
    data = request.form.to_dict()

    # 1) Fecha: del form viene como "fecha_nac"
    fecha_nac = (data.pop("fecha_nac", "") or "").strip()
    if fecha_nac:
        data["fecha_nacimiento"] = fecha_nac

    # 2) Responsable: "tutor" → "responsable"
    tutor = (data.pop("tutor", "") or "").strip()
    if tutor:
        data["responsable"] = tutor

    # 3) No guardamos edad_30jun
    data.pop("edad_30jun", None)

    # 4) RECURSANTES
    recursante_str = (data.pop("recursante", "") or "NO").strip().upper()
    data["recursante"] = (recursante_str == "SI")

    # 5) Normalizamos sexo
    sexo = (data.get("sexo") or "").strip().upper()
    if sexo:
        data["sexo"] = sexo

    # 6) Escuela de procedencia ya viene como "escuela_procedencia"
    escuela_procedencia = (data.get("escuela_procedencia") or "").strip()
    if escuela_procedencia:
        data["escuela_procedencia"] = escuela_procedencia
        # Fecha de ingreso: si no viene, usamos hoy
    if not data.get("fecha_ingreso"):
        data["fecha_ingreso"] = today().strftime("%Y-%m-%d")

    # Normalizamos campos de movimiento de salida (para altas normalmente quedan vacíos)
    motivo_salida = (data.get("motivo_salida") or "").strip()
    destino_salida = (data.get("destino_salida") or "").strip()
    data["motivo_salida"] = motivo_salida
    data["destino_salida"] = destino_salida

    # Un alumno nuevo NO debe tener fecha de salida
    data.pop("fecha_salida", None)

    # -------- Insertamos alumno --------
    res = COL_ALUMNOS.insert_one(data)  
    alumno_id = res.inserted_id

    # -------- Registramos movimiento ALTA --------
    try:
        COL_MOVIMIENTOS.insert_one({
            "alumno_id": alumno_id,
            "tipo": "ALTA",
            "curso": (data.get("curso") or "").strip(),
            "apellido": (data.get("apellido") or "").strip(),
            "nombre": (data.get("nombre") or "").strip(),
            "dni": (data.get("dni") or "").strip(),

            # NUEVO: tomamos escuela de procedencia del alumno
            "escuela_origen": escuela_procedencia,
            "escuela_destino": "E.P. N° 91",

            "con_pase": False,
            "con_acta": False,
            "fecha": datetime.now(),
        })
    except Exception as e:
        print("Error registrando movimiento ALTA:", e)

    return redirect(url_for("listar_alumnos"))



@app.route("/resumen/nacionalidades")
def resumen_nacionalidades():
    """
    Por curso, cuenta alumnos por nacionalidad (y sexo).
    """
    alumnos = list(COL_ALUMNOS.find(filtro_activos()))


    # data[curso][nacionalidad][sexo] = count
    data = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    cursos_set = set()
    nacs_set = set()

    for a in alumnos:
        curso = (a.get("curso") or "").strip()
        if not curso:
            continue
        cursos_set.add(curso)
        raw = (a.get("nacionalidad") or "").strip()
        up = raw.upper().replace(".", "").strip()

        if not up:
            nac = "SIN DATO"
        elif up in ("ARG", "ARGENTINA", "ARGENTINO", "ARGENTINOS", "ARGENTINAS"):
            nac = "ARGENTINA"
        else:
           nac = up

        
        nacs_set.add(nac)

        sexo = (a.get("sexo") or "").strip().upper()
        if sexo not in ("M", "F"):
            sexo = "X"

        data[curso][nac][sexo] += 1

    cursos = sorted(cursos_set)
    nacs = sorted(nacs_set)

    return render_template(
        "resumen_nacionalidades.html",
        data=data,
        cursos=cursos,
        nacs=nacs,
    )
@app.route("/resumen/movimientos")
def resumen_movimientos():
    """
    Movimientos separados:
      - Entradas (ALTA)
      - Salidas por PASE
      - Egresos
      - Cambios de turno
      - Otras bajas
    """
    movs = list(COL_MOVIMIENTOS.find({}).sort([("fecha", -1)]))

    entradas = []
    pases = []
    egresos = []
    cambios_turno = []
    otras_bajas = []

    for m in movs:
        tipo = (m.get("tipo") or "").upper().strip()
        motivo = (m.get("motivo") or m.get("motivo_salida") or "").upper().strip()

        if tipo == "ALTA":
            entradas.append(m)

        elif tipo == "CAMBIO_TURNO":
            cambios_turno.append(m)

        elif tipo in ("BAJA", "SALIDA"):
            if "PASE" in motivo:
                pases.append(m)
            elif "EGRESO" in motivo:
                egresos.append(m)
            else:
                otras_bajas.append(m)

        else:
            # Si aparece algún tipo raro, lo mandamos a "otras"
            otras_bajas.append(m)

    return render_template(
        "resumen_movimientos.html",
        entradas=entradas,
        pases=pases,
        egresos=egresos,
        cambios_turno=cambios_turno,
        otras_bajas=otras_bajas,
    )

@app.route("/legajos")
def legajos_cursos():
    cursos = sorted(
        [c for c in COL_ALUMNOS.distinct("curso", filtro_activos()) if c], 
        key=lambda x: str(x)
    )
    return render_template("legajos_cursos.html", cursos=cursos)

@app.route("/legajos/<curso>")
def legajos_curso(curso):
    q = {"curso": {"$regex": f"^{curso}$", "$options": "i"}}
    alumnos = list(COL_ALUMNOS.find({**filtro_activos(), **q}).sort([("apellido", 1), ("nombre", 1)]))


    # legajo viene como subdocumento: legajo.dni_menor = True/False, etc.
    return render_template(
        "legajos_curso.html",
        curso=curso,
        alumnos=alumnos,
        campos=LEG_CAMPOS,
    )
@app.route("/legajos/<id>/actualizar", methods=["POST"])
def legajo_actualizar(id):
    try:
        oid = ObjectId(id)
    except:
        abort(404)

    sets = {}
    for code, _label in LEG_CAMPOS:
        valor = bool(request.form.get(code))
        sets[f"legajo.{code}"] = valor

    # Buscamos al alumno para saber a qué curso volver
    alumno = COL_ALUMNOS.find_one({"_id": oid})
    if not alumno:
        abort(404)

    COL_ALUMNOS.update_one({"_id": oid}, {"$set": sets})

    curso = (alumno.get("curso") or "").strip()

    # Volvemos a la pantalla del curso con un flag ok=1
    return redirect(url_for("legajos_curso", curso=curso, ok="1"))


@app.route("/autorizados")
def autorizados_cursos():
    cursos = sorted(
        [c for c in COL_ALUMNOS.distinct("curso", filtro_activos()) if c],
        key=lambda x: str(x)
    )
    return render_template("autorizados_cursos.html", cursos=cursos)
@app.route("/autorizados/<curso>")
def autorizados_curso(curso):
    q = {"curso": {"$regex": f"^{curso}$", "$options": "i"}}
    alumnos = list(COL_ALUMNOS.find({**filtro_activos(), **q}).sort([("apellido", 1), ("nombre", 1)]))

    return render_template("autorizados_curso.html", curso=curso, alumnos=alumnos)

@app.route("/autorizados/alumno/<id>", methods=["GET", "POST"])
def autorizados_alumno(id):
    try:
        oid = ObjectId(id)
    except:
        abort(404)

    a = COL_ALUMNOS.find_one({"_id": oid})
    if not a:
        abort(404)

    if request.method == "POST":
        nombres = request.form.getlist("aut_nombre[]")
        dnis = request.form.getlist("aut_dni[]")
        fns = request.form.getlist("aut_fnac[]")
        parents = request.form.getlist("aut_parentesco[]")
        doms = request.form.getlist("aut_dom[]")
        tels = request.form.getlist("aut_tel[]")

        autorizados = []
        today = date.today()

        for i in range(len(nombres)):
            nombre = (nombres[i] or "").strip()
            if not nombre:
                continue

            dni = (dnis[i] or "").strip()
            fn = (fns[i] or "").strip()
            edad = None
            if fn:
                try:
                    y, m, d = map(int, fn.split("-"))
                    nac = date(y, m, d)
                    edad = today.year - nac.year - ((today.month, today.day) < (nac.month, nac.day))
                except Exception:
                    edad = None

            autorizados.append({
                "nombre": nombre,
                "dni": dni,
                "fecha_nac": fn,
                "edad": edad,
                "parentesco": (parents[i] or "").strip(),
                "domicilio": (doms[i] or "").strip(),
                "telefono": (tels[i] or "").strip(),
            })

        COL_ALUMNOS.update_one({"_id": oid}, {"$set": {"autorizados": autorizados}})
        return redirect(url_for("autorizados_curso", curso=a.get("curso","")))

    # GET
    autorizados = a.get("autorizados") or []
    return render_template("autorizados_alumno.html", alumno=a, autorizados=autorizados)

@app.route("/alumnos/<id>/editar", methods=["POST"])
def editar_alumno(id):
    alumno = COL_ALUMNOS.find_one({"_id": ObjectId(id)})
    if not alumno:
        abort(404)

    updates = request.form.to_dict()

    # ----------------- DATOS BÁSICOS -----------------
    fecha_nac = (updates.pop("fecha_nac", "") or "").strip()
    if fecha_nac:
        updates["fecha_nacimiento"] = fecha_nac

    tutor = (updates.pop("tutor", "") or "").strip()
    if tutor:
        updates["responsable"] = tutor

    updates.pop("edad_30jun", None)

    recursante_str = (updates.pop("recursante", "") or "NO").strip().upper()
    updates["recursante"] = (recursante_str == "SI")

    sexo = (updates.get("sexo") or "").strip().upper()
    if sexo:
        updates["sexo"] = sexo

    # ----------------- CURSO ACTUAL / DESTINO -----------------
    curso_actual = (alumno.get("curso") or "").strip()
    curso_form = (updates.get("curso") or "").strip()  # el "curso" normal del alumno
    curso_destino = (updates.get("curso_destino") or "").strip()  # clave para CAMBIO DE TURNO

    # ----------------- MOVIMIENTOS DE SALIDA -----------------
    motivo_salida = (updates.get("motivo_salida") or "").strip().upper()
    destino_salida = (updates.get("destino_salida") or "").strip()
    fecha_salida_form = (updates.pop("fecha_salida", "") or "").strip()

    updates["motivo_salida"] = motivo_salida
    updates["destino_salida"] = destino_salida

    # Regla:
    # - CAMBIO DE TURNO => sigue activo (fecha_salida = None) y cambia el curso al curso_destino
    # - Cualquier otro motivo no vacío => BAJA definitiva (fecha_salida = hoy o la del form)
    if motivo_salida == "CAMBIO DE TURNO":
        # si no cargaron destino, dejamos el curso igual (no rompemos nada)
        updates["curso"] = curso_destino or curso_actual or curso_form
        updates["fecha_salida"] = None

        # para "histórico del curso" (mostrar en gris en el curso viejo)
        updates["curso_origen"] = curso_actual
        updates["fecha_cambio_curso"] = fecha_salida_form or today().strftime("%Y-%m-%d")

    elif motivo_salida:
        # baja definitiva
        updates["curso"] = curso_form or curso_actual
        updates["fecha_salida"] = fecha_salida_form or today().strftime("%Y-%m-%d")

    else:
        # alumno activo sin movimiento
        updates["curso"] = curso_form or curso_actual
        updates["fecha_salida"] = None

        # si vuelve a activo, limpiamos histórico de cambio
        updates.pop("curso_origen", None)
        updates.pop("fecha_cambio_curso", None)

        # ----------------- OTROS CAMPOS -----------------
    if "escuela_procedencia" in updates:
        updates["escuela_procedencia"] = (updates["escuela_procedencia"] or "").strip()

    # ----------------- GUARDAR -----------------
    COL_ALUMNOS.update_one({"_id": ObjectId(id)}, {"$set": updates})

    # ----------------- REGISTRAR MOVIMIENTO -----------------
    try:
        if motivo_salida == "CAMBIO DE TURNO" and (curso_destino and curso_destino != curso_actual):
            _insert_movimiento_si_no_duplicado({
                "alumno_id": ObjectId(id),
                "tipo": "CAMBIO_TURNO",
                "curso_origen": curso_actual,
                "curso_destino": curso_destino,
                "apellido": (alumno.get("apellido") or "").strip(),
                "nombre": (alumno.get("nombre") or "").strip(),
                "dni": (alumno.get("dni") or "").strip(),
                "fecha": datetime.utcnow(),
            })

        elif motivo_salida and motivo_salida != "CAMBIO DE TURNO":
            _insert_movimiento_si_no_duplicado({
                "alumno_id": ObjectId(id),
                "tipo": "BAJA",
                "motivo": motivo_salida,
                "curso": curso_actual,
                "apellido": (alumno.get("apellido") or "").strip(),
                "nombre": (alumno.get("nombre") or "").strip(),
                "dni": (alumno.get("dni") or "").strip(),
                "escuela_origen": "E.P. N° 91",
                "escuela_destino": destino_salida,
                "fecha": datetime.utcnow(),
            })

    except Exception as e:
        print("Error registrando movimiento:", e)

    return redirect(url_for("listar_alumnos"))
@app.route("/certificados", methods=["GET", "POST"])
def certificados_pendientes():
    # Alta de nuevo certificado (desde el modal)
    if request.method == "POST":
        apellido = (request.form.get("apellido") or "").strip().upper()
        nombre = (request.form.get("nombre") or "").strip().upper()
        dni = (request.form.get("dni") or "").strip()
        anio_promocion_raw = (request.form.get("anio_promocion") or "").strip()
        observaciones = (request.form.get("observaciones") or "").strip()

        try:
            anio_promocion = int(anio_promocion_raw)
        except ValueError:
            anio_promocion = None   # ✔️ seguimos igual

        doc = {
            "apellido": apellido,
            "nombre": nombre,
            "dni": dni,
            "anio_promocion": anio_promocion,
            "observaciones": observaciones,
            "fecha_carga": datetime.utcnow(),
        }

        COL_CERTIFICADOS.insert_one(doc)

        # ✔️ UN SOLO return, siempre
        return redirect(url_for("certificados_pendientes"))

    # ---------------- GET ----------------
    q = (request.args.get("q") or "").strip()
    filtro = {}
    if q:
        regex = {"$regex": q, "$options": "i"}
        filtro = {"$or": [
            {"apellido": regex},
            {"dni": regex},
        ]}

    certificados = list(
        COL_CERTIFICADOS
        .find(filtro)
        .sort([("anio_promocion", -1), ("apellido", 1), ("nombre", 1)]) 
    )

    return render_template(
        "certificados_list.html",
        certificados=certificados,
        q=q,
    )


@app.route("/certificados/<id>/eliminar", methods=["POST"])
def eliminar_certificado(id):
    try:
        COL_CERTIFICADOS.delete_one({"_id": ObjectId(id)})
    except Exception:
        pass
    return redirect(url_for("certificados_pendientes"))


@app.route("/alumnos/<id>/eliminar", methods=["POST"])
def eliminar_alumno(id):
    COL_ALUMNOS.update_one(
        {"_id": ObjectId(id)},
        {"$set": {"fecha_salida": date.today().isoformat()}}
    )
    COL_CALIFICACIONES.delete_many({"alumno_id": id})
    return redirect(url_for("listar_alumnos"))


@app.route("/alumnos/<id>/certificado")
def certificado_finalizacion(id):
    a = COL_ALUMNOS.find_one({"_id": ObjectId(id)})
    if not a:
        abort(404)

    # Fecha de finalización configurable (.env), ej: CERT_FECHA_FIN=2025-12-22
    fecha_fin_str = os.getenv("CERT_FECHA_FIN", "2025-12-22")
    fecha_fin = datetime.strptime(fecha_fin_str, "%Y-%m-%d").date()

    alumno = to_json(a)

    dni = (alumno.get("dni") or "").strip()
    anexo = f"{dni}/069" if dni else "____/069"

    ctx = { 
        "alumno": alumno,
        "fecha_fin": fecha_fin,
        "fecha_fin_larga": fecha_larga_castellano(fecha_fin),
        "anexo": anexo,
    }
    return render_template("certificado_finalizacion.html", **ctx)



# ----------------- RESUMEN CURSO -----------------
@app.route("/api/resumen_curso")
def api_resumen_curso():
    curso = (request.args.get("curso") or "").strip()
    if not curso:
        return jsonify({"error": "curso requerido"}), 400

    q = {"curso": {"$regex": f"^{curso}$", "$options": "i"}}
    alumnos = list(COL_ALUMNOS.find({**filtro_activos(), **q}))


    total = len(alumnos)
    edades = {}
    nac = {}
    recursantes = 0
    sobreedad = 0

    for a in alumnos:
        edad = None
        if a.get("fecha_nacimiento"):
            edad = calcular_edad(a["fecha_nacimiento"])
            if edad is not None:
                edades[edad] = edades.get(edad, 0) + 1

        n = (a.get("nacionalidad") or "").strip().upper() or "SIN DATO"
        nac[n] = nac.get(n, 0) + 1

        if a.get("recursante"):
            recursantes += 1

        # sobreedad según grado (1°->6 años ... 6°->11)
        curso_txt = (a.get("curso") or "")
        grado = None
        for g in ("1", "2", "3", "4", "5", "6"):
            if curso_txt.startswith(g):
                grado = int(g)
                break
        if grado and edad and edad > (5 + grado):
            sobreedad += 1

    return jsonify({
        "curso": curso,
        "total": total,
        "edades_30jun": edades,
        "nacionalidades": nac,
        "recursantes": recursantes,
        "sobreedad": sobreedad
    })


@app.route("/mapa/recorridos", methods=["GET"])
def mapa_recorridos():
    """
    Pantalla para gestionar recorridos domiciliarios: 
    - filtros por curso / turno / texto
    - accesos rápidos a matrícula, legajo y autorizados
    - selección de alumnos para armar lista de visitas
    """
    q = (request.args.get("q") or "").strip()
    curso_sel = (request.args.get("curso") or "").strip()
    turno_sel = (request.args.get("turno") or "").strip()

    # Lista de cursos disponibles
    cursos = sorted(
        [c for c in COL_ALUMNOS.distinct("curso", filtro_activos()) if c],
        key=lambda x: str(x)
    )
    turnos = ["Mañana", "Tarde"]

    filtro = {}

    # Búsqueda de texto libre
    if q:
        regex = {"$regex": q, "$options": "i"}
        filtro["$or"] = [
            {"apellido": regex},
            {"nombre": regex},
            {"dni": regex},
            {"curso": regex},
            {"domicilio": regex},
            {"localidad": regex},
        ]

    # Filtro por curso exacto (si se eligió uno)
    if curso_sel:
        filtro["curso"] = {"$regex": f"^{curso_sel}$", "$options": "i"}
    # Si NO hay curso elegido, permitimos filtrar por turno (A/B)
    elif turno_sel:
        if turno_sel.lower() == "mañana":
            filtro["curso"] = {"$regex": "A$", "$options": "i"}
        elif turno_sel.lower() == "tarde":
            filtro["curso"] = {"$regex": "B$", "$options": "i"}

    alumnos_cur = COL_ALUMNOS.find ({**filtro_activos(), **filtro})
    alumnos = [to_json(a) for a in alumnos_cur]

    # Ordenar como en matrícula: turno, grado, sección, apellido, nombre
    alumnos.sort(key=_orden_alumno)

    return render_template(
        "mapa_recorridos.html",
        alumnos=alumnos,
        cursos=cursos,
        turnos=turnos,
        curso_sel=curso_sel,
        turno_sel=turno_sel,
        q=q,
    )


@app.route("/mapa/recorridos/visitas", methods=["POST"])
def mapa_recorridos_visitas():
    """
    Genera el ACTA DE VISITA DOMICILIARIA en HTML
    para los alumnos seleccionados.
    Desde esa página se imprime o se guarda como PDF
    con el botón Imprimir.
    """
    ids = request.form.getlist("alumno_id")
    oids = []
    for _id in ids:
        try:
            oids.append(ObjectId(_id))
        except Exception:
            continue

    if not oids:
        alumnos = []
    else:
        alumnos_cur = COL_ALUMNOS.find({**filtro_activos(), "_id": {"$in": oids}}) 
        alumnos = [to_json(a) for a in alumnos_cur]
        alumnos.sort(key=_orden_alumno)

    hoy = date.today().strftime("%d/%m/%Y")

    return render_template(
        "mapa_visitas.html",
        alumnos=alumnos,
        fecha=hoy,
    )

  
# ----------------- AUXILIARES -----------------
@app.route("/auxiliares")
def listar_auxiliares():
    auxs = [to_json(a) for a in COL_AUX.find().sort([("apellido",1),("nombre",1)])]
    return render_template("auxiliares.html", auxiliares=auxs)

@app.route("/auxiliares/nuevo", methods=["POST"])
def nuevo_auxiliar():
    data = request.form.to_dict()
    COL_AUX.insert_one(data)
    return redirect(url_for("listar_auxiliares"))

@app.route("/auxiliares/<id>/editar", methods=["POST"])
def editar_auxiliar(id):
    updates = request.form.to_dict()
    COL_AUX.update_one({"_id": ObjectId(id)}, {"$set": updates})
    return redirect(url_for("listar_auxiliares"))

@app.route("/auxiliares/<id>/eliminar", methods=["POST"])
def eliminar_auxiliar(id): 
    COL_AUX.delete_one({"_id": ObjectId(id)})
    return redirect(url_for("listar_auxiliares"))

# ----------------- INASISTENCIAS (robusto) -----------------
@app.route("/inasistencias")
def ver_inasistencias():
    return render_template("inasistencias.html")

@app.route("/api/inasistencias/<id>", methods=["GET"])
def api_inasistencia_get(id):
    try:
        oid = ObjectId(id)
    except Exception:
        return jsonify(ok=False, error="ID inválido"), 400

    ins = COL_INASISTENCIAS.find_one({"_id": oid})
    if not ins:
        return jsonify(ok=False, error="Inasistencia no encontrada"), 404

    ins["_id"] = str(ins["_id"])
    ins["docente_id"] = str(ins.get("docente_id"))
    return jsonify(ok=True, data=ins)


@app.route("/api/inasistencias/<id>", methods=["PUT"])
def api_inasistencia_update(id):
    try:
        oid = ObjectId(id)
    except Exception:
        return jsonify(ok=False, error="ID inválido"), 400

    data = request.get_json(force=True) or {}

    updates = {}
    for campo in ("fecha", "causa", "observaciones"):
        if campo in data and data[campo] is not None:
            updates[campo] = data[campo]

    # opcional: actualizar suplente
    sup_campos = ("nombre", "dni", "curso", "asignatura")
    sup = data.get("suplente_info") or {}
    sup = {k: v for k, v in sup.items() if k in sup_campos and v}
    if sup:
        updates["suplente_info"] = sup

    if not updates:
        return jsonify(ok=False, error="Sin cambios"), 400

    COL_INASISTENCIAS.update_one({"_id": oid}, {"$set": updates})
    return jsonify(ok=True)


@app.route("/api/inasistencias/<id>", methods=["DELETE"])
def api_inasistencia_delete(id):
    try:
        oid = ObjectId(id)
    except Exception:
        return jsonify(ok=False, error="ID inválido"), 400

    res = COL_INASISTENCIAS.delete_one({"_id": oid}) 
    return jsonify(ok=bool(res.deleted_count))


# SOLO POST: crear inasistencias
@app.route("/api/inasistencias", methods=["POST"]) 
def api_inasistencias():
    data = request.get_json(force=True) or {}

    docente_id_raw = data.get("docente_id") or data.get("docente")
    if not docente_id_raw:
        return jsonify(ok=False, error="Docente requerido."), 400

    docente_id = _maybe_oid(docente_id_raw)

    # Fechas (aceptamos 'fecha', 'desde', 'hasta')
    desde = _parse_date(data.get("desde") or data.get("fecha"))
    hasta = _parse_date(data.get("hasta") or data.get("fecha"))

    if not desde:
        return jsonify(ok=False, error="Fecha 'desde' requerida."), 400
    if not hasta:
        hasta = desde
    if hasta < desde:
        desde, hasta = hasta, desde

    causa = (data.get("causa") or "").strip()
    observ = (data.get("observaciones") or "").strip()

    suplente_info = {
        "nombre": (data.get("sup_nombre") or data.get("suplente_nombre") or "").strip(),
        "dni": (data.get("sup_dni") or "").strip(),
        "curso": (data.get("sup_curso") or "").strip(),
        "asignatura": (data.get("sup_asignatura") or "").strip(),
    }
    suplente_info = {k: v for k, v in suplente_info.items() if v}

    # ----- Topes y causas particulares por mes -----

    limites = _limites_restantes(docente_id, referencia_fecha=desde)
    causa_norm = _norm(causa)
    bucket = _causa_bucket(causa_norm)

    if bucket == "particulares":
        key_mes = f"{desde.year}-{desde.month:02d}"
        if limites["particulares_mes_lleno"].get(key_mes):
            return jsonify(
                ok=False,
                error="Ya hay una inasistencia por 'causas particulares' en este mes."
            ), 400
            
    # --- NUEVO BLOQUE: Validar tope anual ---
    # Si la causa tiene límite y ya no quedan días disponibles (restantes <= 0)
    if bucket in limites["restantes"]:
        if limites["restantes"][bucket] <= 0:
            return jsonify(
                ok=False,
                error=f"Se ha excedido el límite anual para '{causa}' (Tope: {LIMITES_ANUALES.get(bucket)})."
            ), 400
    # ----------------------------------------

    # ----- Evitar más de una inasistencia por día -----
    dias_a_insertar = []
    d = desde
    while d <= hasta:
        fecha_iso = d.isoformat()
        existente = COL_INASISTENCIAS.find_one(
            {"docente_id": docente_id, "fecha": fecha_iso}
        )
        if existente:
            msg = f"Ya hay una inasistencia cargada para este docente el {d.strftime('%d/%m/%Y')}."
            return jsonify(ok=False, error=msg), 400
        dias_a_insertar.append(fecha_iso)
        d += timedelta(days=1)

    # ----- Insertar -----
    docs = []
    for fecha_iso in dias_a_insertar:
        docs.append({
            "docente_id": docente_id,
            "fecha": fecha_iso,
            "causa": causa,
            "observaciones": observ,
            "suplente_info": suplente_info,
        })

    if docs:
        COL_INASISTENCIAS.insert_many(docs)

    return jsonify(ok=True, inserted=len(docs))


# GET: listar para HISTORIAL / CALENDARIO 

@app.route("/api/inasistencias/<id>", methods=["PATCH","DELETE"]) 
def api_inasistencia_id(id):
    # DELETE
    if request.method == "DELETE":
        COL_INASISTENCIAS.delete_one({"_id": ObjectId(id)})
        return jsonify({"ok":"deleted"})
    # PATCH (editar)
    payload = request.get_json(silent=True) or {} 
    fields = {}
    if "fecha" in payload:
        d = _parse_date(payload.get("fecha"))
        if not d:
         return jsonify({"ok": False, "error": "fecha invalida"}), 400
        fields["fecha"] = d.isoformat()
    if "causa" in payload:
        causa = (payload.get("causa") or "").strip()
        fields["causa"] = causa
        fields["color"] = _color_for_causa(_norm(causa))
    if "observaciones" in payload:
        fields["observaciones"] = (payload.get("observaciones") or "").strip()
    if "cubierto" in payload:
        fields["cubierto"] = bool(payload.get("cubierto"))
    if "cobertura_tipo" in payload:
        fields["cobertura_tipo"] = (payload.get("cobertura_tipo") or "").strip().upper()
    # Soportar tanto "suplente" como "suplente_info" en el payload 
    
    # Soportar tanto "suplente" como "suplente_info" en el payload
    sup = payload.get("suplente") or payload.get("suplente_info")

    if isinstance(sup, dict):
         fields["suplente_info"] = {
           "nombre": sup.get("nombre",""),
            "dni": sup.get("dni",""),
            "curso": sup.get("curso",""),
            "asignatura": sup.get("asignatura",""),
    }


    if not fields:
      return jsonify({"ok": False, "error": "sin cambios"}), 400
    COL_INASISTENCIAS.update_one({"_id": ObjectId(id)}, {"$set": fields})
    return jsonify({"ok":"updated"})

@app.route("/inasistencias/<id>/editar", methods=["GET", "POST"])
def editar_inasistencia(id):
    try:
        oid = ObjectId(id)
    except Exception:
        abort(404)

    ins = COL_INASISTENCIAS.find_one({"_id": oid})
    if not ins:
        abort(404)

    if request.method == "POST":
        data = request.form.to_dict()

        # Normalizamos nombres de campos según lo que guardás en /api_inasistencias
        update = {
            "fecha": data.get("fecha", "").strip(),
            "docente_id": data.get("docente_id", ins.get("docente_id")),
            "causa": data.get("causa", "").strip(),
            "suplente_nombre": data.get("suplente_nombre", "").strip(),
            "suplente_dni": data.get("suplente_dni", "").strip(),
            "suplente_curso": data.get("suplente_curso", "").strip(),
            "observaciones": data.get("observaciones", "").strip(),
        }

        COL_INASISTENCIAS.update_one({"_id": oid}, {"$set": update})
        # Después de editar, te vuelvo al historial, con los mismos filtros que tenías si querés
        return redirect(url_for("historial_inasistencias"))

    # GET → cargo docentes para el combo y muestro plantilla
    docentes = list(COL_DOCENTES.find().sort([("apellido", 1), ("nombre", 1)])) 
    # Convertimos ObjectId a string
    ins["_id"] = str(ins["_id"])
    return render_template("inasistencias_editar.html",
                           inasistencia=ins,
                           docentes=docentes)


# ----------------- HISTORIAL INASISTENCIAS (vista + APIs) -----------------

@app.route("/historial/inasistencias")
def historial_inasistencias():
    docentes = [to_json(d) for d in COL_DOCENTES.find().sort([("apellido",1),("nombre",1)])]
    return render_template("historial_inasistencias.html", docentes=docentes)
@app.get("/api/historial_resumen")
def api_historial_resumen():
    """
    Devuelve:
      {
        "total": N,
        "topes": { clave: tope, ... },
        "por_causa": { clave: cantidad, ... }
      }
    Las claves se usan tal cual en el front.
    """
    docente_id = (request.args.get("docente") or "").strip()
    causa_filtro = (request.args.get("causa") or "").strip()
    desde_s = (request.args.get("desde") or "").strip()
    hasta_s = (request.args.get("hasta") or "").strip()

    q = {}
    if docente_id and docente_id != "TODOS":
        q["docente_id"] = _maybe_oid(docente_id)

    # rango de fechas
    d1 = _parse_date(desde_s)
    d2 = _parse_date(hasta_s)
    if d1 and d2:
        q["fecha"] = {"$gte": d1.isoformat(), "$lte": d2.isoformat()}
    elif d1:
        q["fecha"] = {"$gte": d1.isoformat()}
    elif d2:
        q["fecha"] = {"$lte": d2.isoformat()}

    # Traemos de la DB
    cursor = COL_INASISTENCIAS.find(q)

    registros = []
    causa_norm_filtro = _normalizar_texto(causa_filtro)

    for ins in cursor:
        causa_raw = ins.get("causa", "") or ""
        c_norm = _normalizar_texto(causa_raw)

        # filtro por causa (si no es "TODAS")
        if causa_filtro and causa_filtro != "TODAS":
            if causa_norm_filtro not in c_norm:
                continue

        registros.append(ins)

    total = len(registros)

    # --- Contadores por tipo (para el cuadrito de la izquierda) ---
    # OJO: uso .get() para que no explote si LIMITES_ANUALES tiene otras claves
    topes = {
        "pre-examen": (LIMITES_ANUALES.get("pre-examen")
                       or LIMITES_ANUALES.get("preexamen")
                       or LIMITES_ANUALES.get("pre_examen")),
        "enfermedad personal": (LIMITES_ANUALES.get("enfermedad personal")
                                or LIMITES_ANUALES.get("enfermedad_personal")),
        "enfermedad familiar": (LIMITES_ANUALES.get("enfermedad familiar")
                                or LIMITES_ANUALES.get("enfermedad_familiar")),
        "causas particulares": (LIMITES_ANUALES.get("causas particulares")
                                or LIMITES_ANUALES.get("particulares")),
        "duelo": "",
        "examen": "",
        "paro": "",
        "otras": "",
    }

    # ✅ inicialización (lo que vos decías que “no tenías”)
    por_causa = {k: 0 for k in topes.keys()}

    # ✅ normalización + conteo
    for ins in registros:
        causa_raw = ins.get("causa", "") or ""
        c_norm = _normalizar_texto(causa_raw)

        # IMPORTANTE: pre-examen ANTES que examen
        if "pre" in c_norm and "examen" in c_norm:
            key = "pre-examen"
        elif "duelo" in c_norm:
            key = "duelo"
        elif "paro" in c_norm:
            key = "paro"
        # examen (pero no pre-examen)
        elif "examen" in c_norm:
            key = "examen"
        elif "enfermedad personal" in c_norm:
            key = "enfermedad personal"
        elif "enfermedad familiar" in c_norm:
            key = "enfermedad familiar"
        elif "particular" in c_norm:
            key = "causas particulares"
        else:
            key = "otras"

        por_causa[key] += 1

    return jsonify({
        "total": total,
        "topes": topes,
        "por_causa": por_causa,
    })

@app.route("/api/historial_lista")
def api_historial_lista():
    """
    Devuelve la lista detallada para la tabla del historial
    y opcionalmente para el calendario (modo=calendario).

    Cada item para historial:
      {
        "_id": "...",
        "fecha": "YYYY-MM-DD",
        "docente_nombre": "Apellido, Nombre",
        "causa": "...",
        "suplente_info": {...} | {},
        "observaciones": "...",
        "color": "#xxxxxx"
      }

    Para modo=calendario agrega:
      "title": causa,
      "start": fecha
    """
    docente_id    = (request.args.get("docente") or "").strip()
    causa_filtro  = (request.args.get("causa") or "").strip()
    desde_s       = (request.args.get("desde") or "").strip()
    hasta_s       = (request.args.get("hasta") or "").strip()
    modo          = (request.args.get("modo") or "historial").strip()

    # --- Filtro base por docente + fechas ---
    q = {}
    if docente_id and docente_id != "TODOS":
        q["docente_id"] = _maybe_oid(docente_id)

    d1 = _parse_date(desde_s)
    d2 = _parse_date(hasta_s)
    if d1 and d2:
        q["fecha"] = {"$gte": d1.isoformat(), "$lte": d2.isoformat()}
    elif d1:
        q["fecha"] = {"$gte": d1.isoformat()}
    elif d2:
        q["fecha"] = {"$lte": d2.isoformat()}

    # Traemos todo lo que cumple docente/fechas
    base_cursor = COL_INASISTENCIAS.find(q)

    causa_norm_filtro = _normalizar_texto(causa_filtro)
    registros = []

    for ins in base_cursor:
        causa_raw = ins.get("causa", "") or ""
        c_norm = _normalizar_texto(causa_raw)

        # filtro por causa (si no es "TODAS")
        if causa_filtro and causa_filtro != "TODAS":
            if causa_norm_filtro not in c_norm:
                continue

        registros.append(ins)

    # --- Orden: primero docente, luego fecha ---
    def _sort_key(ins):
        doc_id = ins.get("docente_id")
        doc = COL_DOCENTES.find_one({"_id": _maybe_oid(doc_id)}) or {}
        ape = (doc.get("apellido") or "").upper()
        nom = (doc.get("nombre") or "").upper()
        f   = _parse_date(ins.get("fecha"))
        return (ape, nom, f or date.min)

    registros.sort(key=_sort_key)

    # Cache de nombres de docentes para no ir mil veces a la DB
    cache_docentes = {}

    def _nombre_docente(doc_id):
        if not doc_id:
            return ""
        key = str(doc_id)
        if key in cache_docentes:
            return cache_docentes[key]
        d = COL_DOCENTES.find_one({"_id": _maybe_oid(doc_id)}) or {}
        nombre = f"{d.get('apellido','')}, {d.get('nombre','')}".strip(", ")
        cache_docentes[key] = nombre
        return nombre

    out = []
    for ins in registros:
        doc_id        = ins.get("docente_id")
        suplente_info = ins.get("suplente_info") or {}
        causa_raw     = ins.get("causa", "")
        fecha_raw     = ins.get("fecha", "")

        item = {
            "_id": str(ins.get("_id")),
            "fecha": fecha_raw,
            "docente_nombre": _nombre_docente(doc_id),
            "causa": causa_raw,
            "suplente_info": suplente_info,
            "observaciones": ins.get("observaciones", ""),
            "color": _color_for_causa(causa_raw),
        }

        if modo == "calendario":
            item["title"] = causa_raw
            item["start"] = fecha_raw

        out.append(item)

    return jsonify(out)


# ----------------- CALIFICACIONES APIs -----------------

@app.route("/api/asignaturas_escala", methods=["GET","POST"])
def api_asignaturas_escala():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        asignatura = (data.get("asignatura") or "").strip()
        escala = (data.get("escala") or "").strip().lower()
        if escala not in ("conceptual","numerica") or not asignatura:
            return jsonify({"error":"Datos inválidos"}), 400
        COL_CFG_ASIGNATURAS.update_one(
            {"asignatura": asignatura},
            {"$set": {"asignatura": asignatura, "escala": escala}},
            upsert=True
        )
        return jsonify({"status":"ok"})
    cfg = { c.get("asignatura"): c.get("escala","conceptual")
            for c in COL_CFG_ASIGNATURAS.find({}) }
    return jsonify(cfg)
@app.route("/api/alumnos_por_curso")
def api_alumnos_por_curso():
    curso = (request.args.get("curso") or "").strip()
    if not curso:
        return jsonify([])

    alumnos = []
    q = {**filtro_activos(), "curso": {"$regex": f"^{curso}$", "$options": "i"}}

    for a in COL_ALUMNOS.find(q).sort([("apellido", 1), ("nombre", 1)]):
        aj = to_json(a)

        if aj.get("fecha_nacimiento"):
            aj["edad_30jun"] = calcular_edad(aj["fecha_nacimiento"])

        autorizados_src = a.get("autorizados") or []
        if isinstance(autorizados_src, list):
            aj["autorizados_retirar"] = [
                {
                    "nombre": aut.get("nombre", ""),
                    "vinculo": aut.get("parentesco", ""),
                    "dni": aut.get("dni", ""),
                }
                for aut in autorizados_src
                if (aut.get("nombre") or "").strip()
            ]

        alumnos.append(aj)

    return jsonify(alumnos)



@app.route("/api/calificaciones")
def api_calificaciones_list():
    docente_id = (request.args.get("docente_id") or "").strip()
    asignatura = (request.args.get("asignatura") or "").strip()
    curso = (request.args.get("curso") or "").strip()
    trimestre_raw = (request.args.get("trimestre") or "").strip()

    # ✔ Aceptar "1", "2", "3" o "all"
    trimestre = trimestre_raw  # ya viene como string

    # --- Armar filtro ---
    q = {}
    if docente_id:
        q["docente_id"] = docente_id
    if asignatura:
        q["asignatura"] = asignatura
    if curso:
        q["curso"] = curso

    # ✔ Si NO es "all", se filtra por trimestre como entero.
    # ✔ Si es "all", muestra todo el año.
    if trimestre and trimestre != "all":
        try:
            q["trimestre"] = int(trimestre)
        except ValueError:
            # Si no es numérico, no filtramos por trimestre
            pass

    # --- Buscar en Mongo ---
    registros = [to_json(c) for c in COL_CALIFICACIONES.find(q)]
    return jsonify(registros)


@app.route("/api/calificaciones", methods=["POST"])
def api_calificaciones_upsert():
    data = request.get_json(silent=True) or {}
    alumno_id = (data.get("alumno_id") or "").strip()
    docente_id = (data.get("docente_id") or "").strip()
    asignatura = (data.get("asignatura") or "").strip()
    curso = (data.get("curso") or "").strip()
    try:
        trimestre = int(data.get("trimestre") or 0)
    except:
        trimestre = 0
    escala = (data.get("escala") or "").strip().lower()
    valor = (data.get("valor") or "").strip()
    observaciones = (data.get("observaciones") or "").strip()
    if not all([alumno_id, docente_id, asignatura, curso, trimestre, escala, valor]):
        return jsonify({"error":"Campos obligatorios faltantes"}), 400
    if escala not in ("conceptual","numerica"):
        return jsonify({"error":"Escala inválida"}), 400
    if escala == "numerica":
        try:
            n = float(valor.replace(",",".")) 
        except:
            return jsonify({"error":"Nota numérica inválida"}), 400
        if n < 1 or n > 10:
            return jsonify({"error":"Nota fuera de rango (1 a 10)"}), 400
        valor = str(n).rstrip("0").rstrip(".")
    key = {
        "alumno_id": alumno_id,
        "docente_id": docente_id,
        "asignatura": asignatura,
        "curso": curso,
        "trimestre": trimestre
    }
    COL_CALIFICACIONES.update_one(
        key,
        {"$set": {
            "escala": escala,
            "valor": valor,
            "observaciones": observaciones,
            "updated_at": datetime.utcnow()
        }, "$setOnInsert": {"created_at": datetime.utcnow()}},
        upsert=True
    )
    cal = COL_CALIFICACIONES.find_one(key)
    return jsonify(to_json(cal))

@app.route("/api/calificaciones/<id>", methods=["DELETE"])
def api_calificaciones_delete(id):
    COL_CALIFICACIONES.delete_one({"_id": ObjectId(id)})
    return jsonify({"status":"ok"})

# ----------------- ESTADOS ADMINISTRATIVOS -----------------
def calc_fecha_limite_salida(fecha_salida_str, zona):
    f = _parse_date(fecha_salida_str)
    if not f:
        return None
    z = (zona or "").strip().lower()
    if z == "distrito":
        delta = 10
    elif z == "caba":
        delta = 30
    else:
        delta = 15
    return (f - timedelta(days=delta)).strftime("%Y-%m-%d")

@app.route("/estados_admin")
def estados_admin():
    docentes = [to_json(d) for d in COL_DOCENTES.find().sort([("apellido",1),("nombre",1)])]
    tipos = [
        "salidas_educativas","planificaciones","secuencias_didacticas","proyectos",
        "reuniones_de_padres","clases_abiertas","incompatibilidad_horaria","legajo_docente",
        "cuaderno_de_actuacion","legajos_de_alumnos","boletines","registro",
        "llegadas_tarde_inasistencias","reeb","carteleras","observaciones"
    ]
    return render_template("estados_admin.html", docentes=docentes, tipos=tipos)

@app.route("/api/estados_admin", methods=["GET","POST"])
def api_estados_admin():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        docente_id = (data.get("docente_id") or "").strip()
        tipo = (data.get("tipo") or "").strip()
        descripcion = (data.get("descripcion") or "").strip()
        fecha_notificacion = (data.get("fecha_notificacion") or "").strip()
        fecha_limite = (data.get("fecha_limite") or "").strip()
        cumplido = bool(data.get("cumplido", False))
        fecha_cumplido = (data.get("fecha_cumplido") or "").strip()
        adjunto_url = (data.get("adjunto_url") or "").strip()
        observaciones = (data.get("observaciones") or "").strip()
        fecha_salida = (data.get("fecha_salida") or "").strip()
        lugar = (data.get("lugar") or "").strip()
        zona = (data.get("zona") or "").strip()
        if tipo == "salidas_educativas" and fecha_salida and not fecha_limite:
            fecha_limite = calc_fecha_limite_salida(fecha_salida, zona)
        eid = COL_ESTADOS_ADMIN.insert_one({
            "docente_id": docente_id,
            "tipo": tipo,
            "descripcion": descripcion,
            "fecha_notificacion": fecha_notificacion,
            "fecha_limite": fecha_limite,
            "cumplido": cumplido,
            "fecha_cumplido": fecha_cumplido,
            "adjunto_url": adjunto_url,
            "observaciones": observaciones,
            "fecha_salida": fecha_salida,
            "lugar": lugar,
            "zona": zona,
            "created_at": datetime.utcnow()
        }).inserted_id
        return jsonify({"status":"ok","id":str(eid)}), 201

    # GET (con filtros)
    docente_id = request.args.get("docente_id")
    tipo = request.args.get("tipo")
    estado = (request.args.get("estado") or "").lower()
    desde = request.args.get("desde")
    hasta = request.args.get("hasta")
    q = {}
    if docente_id and docente_id != "TODOS": q["docente_id"] = docente_id
    if tipo and tipo != "TODOS": q["tipo"] = tipo
    if estado == "pendiente": q["cumplido"] = False
    elif estado == "cumplido": q["cumplido"] = True
    if desde and hasta:
        q["$or"] = [
            {"fecha_limite": {"$gte": desde, "$lte": hasta}},
            {"fecha_notificacion": {"$gte": desde, "$lte": hasta}}
        ]
    out = []
    for e in COL_ESTADOS_ADMIN.find(q).sort([("fecha_limite",1),("fecha_notificacion",1)]):
        ej = to_json(e)
        nom_doc = "—"
        if ej.get("docente_id"):
            try:
                d = COL_DOCENTES.find_one({"_id": _maybe_oid(ej["docente_id"])})
                if d: nom_doc = f"{d.get('apellido','')}, {d.get('nombre','')}".strip(", ")
            except:
                pass
        ej["docente_nombre"] = nom_doc
        ej["dias_restantes"] = dias_restantes(ej.get("fecha_limite"))
        out.append(ej)
    return jsonify(out)

@app.route("/api/estados_admin/<id>", methods=["PUT","DELETE"])
def api_estados_admin_id(id):
    if request.method == "DELETE":
        COL_ESTADOS_ADMIN.delete_one({"_id": ObjectId(id)})
        return jsonify({"status":"ok"})
    data = request.get_json(silent=True) or {}
    if data.get("tipo") == "salidas_educativas":
        if (data.get("fecha_salida") and not data.get("fecha_limite")):
            data["fecha_limite"] = calc_fecha_limite_salida(data["fecha_salida"], data.get("zona"))
    COL_ESTADOS_ADMIN.update_one({"_id": ObjectId(id)}, {"$set": data})
    e = COL_ESTADOS_ADMIN.find_one({"_id": ObjectId(id)})
    return jsonify(to_json(e))

@app.route("/api/estados_resumen")
def api_estados_resumen():
    docente_id = request.args.get("docente_id")
    tipo = request.args.get("tipo")
    q = {}
    if docente_id and docente_id != "TODOS": q["docente_id"] = docente_id
    if tipo and tipo != "TODOS": q["tipo"] = tipo
    total = COL_ESTADOS_ADMIN.count_documents(q)
    pipeline_tipo = [{"$match": q}, {"$group": {"_id": "$tipo", "count": {"$sum": 1}}}]
    agg_tipo = {a["_id"]: a["count"] for a in COL_ESTADOS_ADMIN.aggregate(pipeline_tipo) if a["_id"]}
    pendientes = COL_ESTADOS_ADMIN.count_documents({**q, "cumplido": False})
    cumplidos = COL_ESTADOS_ADMIN.count_documents({**q, "cumplido": True})
    proximos = []
    for e in COL_ESTADOS_ADMIN.find({**q, "cumplido": False}):
        dr = dias_restantes(e.get("fecha_limite"))
        if dr is not None and dr <= 5:
            ej = to_json(e); ej["dias_restantes"] = dr; proximos.append(ej)
    en_10 = []
    for e in COL_ESTADOS_ADMIN.find({**q, "cumplido": False}):
        dr = dias_restantes(e.get("fecha_limite"))
        if dr is not None and 0 <= dr <= 10: en_10.append(to_json(e))
    return jsonify({
        "total": total, "por_tipo": agg_tipo,
        "pendientes": pendientes, "cumplidos": cumplidos,
        "criticos_5dias": proximos, "en_10dias": en_10,
        "hoy": today().strftime("%Y-%m-%d")
    })

# ----------------- ANEXOS (Punto 4) -----------------
# ----------------- ANEXOS (Modelos oficiales exactos) -----------------

@app.route("/anexos")
def anexos_index():
    # Cursos desde alumnos
    cursos = COL_ALUMNOS.distinct("curso")
    cursos = sorted(
    [c for c in COL_ALUMNOS.distinct("curso", filtro_activos()) if c],
    key=lambda x: str(x)
    )

    docentes = [to_json(d) for d in COL_DOCENTES.find().sort([("apellido",1),("nombre",1)])]
    return render_template("anexos_index.html", cursos=cursos, docentes=docentes)
@app.route("/anexos/render", methods=["POST"])
def anexos_render():
    form = request.form.to_dict()
    tipo = (form.get("tipo") or "").strip().lower()
    curso = (form.get("curso") or "").strip()
    docente_id = (form.get("docente_id") or "").strip()

    # Docente
    docente = None
    if docente_id:
        d = COL_DOCENTES.find_one({"_id": _maybe_oid(docente_id)})
        if d:
            docente = to_json(d)

    # ✅ ALUMNOS SIEMPRE DEFINIDA (evita UnboundLocalError)
    alumnos = []

    # Alumnos del curso (solo activos)
    if curso:
        cur_regex = f"^{curso}$"
        cur = COL_ALUMNOS.find(
            {**filtro_activos(), "curso": {"$regex": cur_regex, "$options": "i"}}
        ).sort([("apellido", 1), ("nombre", 1)])

        for a in cur:
            aj = to_json(a)
            if aj.get("fecha_nacimiento"):
                aj["edad_30jun"] = calcular_edad(aj["fecha_nacimiento"])
            alumnos.append(aj)

    # Datos de salida
    salida = {
        "fecha": form.get("fecha_salida") or "",
        "hora": form.get("hora_salida") or "",
        "lugar": form.get("lugar") or "",
        "zona": form.get("zona") or "",
    }

    transporte = {
        "empresa": form.get("micro_empresa") or "",
        "patente": form.get("micro_patente") or "",
        "chofer_nombre": form.get("chofer_nombre") or "",
        "chofer_dni": form.get("chofer_dni") or "",
        "chofer_licencia": form.get("chofer_licencia") or "",
        "seguro": form.get("seguro") or "",
    }

    escuela = {
        "nombre": form.get("escuela_nombre") or 'EP N° 91 "PROVINCIAS ARGENTINAS"',
        "domicilio": form.get("escuela_domicilio") or "Tomás Edison 2164, Isidro Casanova",
        "telefono": form.get("escuela_telefono") or "",
        "distrito": form.get("escuela_distrito") or "La Matanza",
        "region": form.get("escuela_region") or "III",
    }

    ctx = {
        "curso": curso,
        "docente": docente,
        "alumnos": alumnos,
        "salida": salida,
        "transporte": transporte,
        "escuela": escuela,
        "hoy": today().strftime("%d/%m/%Y"),
    }

    template_map_exact = {
        "anexo_3":  "anexo_3_exact.html",
        "anexo_4":  "anexo_4_exact.html",
        "anexo_v":  "anexo_v_exact.html",
        "anexo_vi": "anexo_vi_exact.html",
        "anexo_7":  "anexo_7_exact.html",
        "anexo_8":  "anexo_8_exact.html",
    }

    if not tipo:
        return jsonify({"ok": False, "error": "Falta elegir tipo de anexo"}), 400

    tmpl = template_map_exact.get(tipo)
    if not tmpl:
        return jsonify({"ok": False, "error": f"Tipo de anexo inválido: {tipo}"}), 400

    return render_template(tmpl, **ctx)



# ----------------- Main -----------------
if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

