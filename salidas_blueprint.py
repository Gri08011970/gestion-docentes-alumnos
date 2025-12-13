# salidas_blueprint.py
# =========================================================
#  Salidas Educativas – Blueprint
#  (usa current_app.mongo para acceder a la DB)
# =========================================================

from datetime import datetime, timedelta, timezone
from flask import Blueprint, request, jsonify, render_template, abort, current_app
from bson.objectid import ObjectId
from notify import send_email

salidas_bp = Blueprint("salidas", __name__, template_folder="templates")

# Datos básicos de la escuela para los anexos
ESCUELA_INFO = {
    "nombre": 'E.P. N° 91 "Provincias Argentinas"',
    "distrito": "La Matanza",
    "cue": "0607912-00",
    "region": "Región 3",
}

# =========================================================
# Helpers base
# =========================================================

def _db():
    """Acceso centralizado a la DB desde el blueprint."""
    return current_app.mongo


def _to_date(s):
    """
    Convierte 'YYYY-MM-DD' a datetime(YYYY, MM, DD, 0, 0).
    Si viene vacío o inválido, devuelve None.
    """
    if not s:
        return None
    try:
        # strptime devuelve un datetime (no un date)
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except Exception:
        return None


def _bool(v):
    """Normaliza valores de formulario a booleano."""
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in ("1", "true", "t", "si", "sí", "on", "yes", "y")

# =========================================================
# Reglas de plazos
# =========================================================

def _deadline_days(tipo_destino, regreso_en_el_dia, duracion_horas):
    """
    Días de antelación según tipo de salida (reglas simplificadas):

      - barrio/área inmediata: 0
      - dentro/fuera del distrito con regreso en el día: 5
      - >24h: 10
      - otra provincia / fuera del país: 20
    """
    tipo = (tipo_destino or "").lower()
    if "fuera del país" in tipo:
        return 20
    if "fuera de la provincia" in tipo or "otra provincia" in tipo:
        return 20
    if duracion_horas and int(duracion_horas) > 24:
        return 10
    if regreso_en_el_dia:
        return 5
    return 0  # barrio / área inmediata


def _deadline_status(fecha_salida, dias_anticipacion):
    """
    Devuelve un dict:

      {
        "estado": "OK" | "PROXIMO" | "VENCIDO" | "SIN_FECHA",
        "dias_restantes": N (puede ser negativo)
      }

    Considera PRÓXIMO si faltan <= 5 días para el límite.
    Acepta fecha_salida como datetime o date.
    """
    if not fecha_salida:
        return {"estado": "SIN_FECHA", "dias_restantes": None}

    # Normalizamos a date para las cuentas
    if isinstance(fecha_salida, datetime):
        fs_date = fecha_salida.date()
    else:
        fs_date = fecha_salida

    # TZ Buenos Aires (UTC-3)
    hoy = datetime.now(timezone(timedelta(hours=-3))).date()
    limite = fs_date - timedelta(days=dias_anticipacion)

    if hoy > limite:
        # Límite superado
        return {"estado": "VENCIDO", "dias_restantes": (limite - hoy).days}

    resta = (limite - hoy).days
    if resta <= 5:
        return {"estado": "PROXIMO", "dias_restantes": resta}
    return {"estado": "OK", "dias_restantes": resta}


def _estado_plazo(salida: dict):
    """Atajo: calcula el estado de plazo para una salida ya normalizada."""
    dias = _deadline_days(
        salida.get("tipo_destino"),
        salida.get("regreso_en_el_dia"),
        salida.get("duracion_horas"),
    )
    return _deadline_status(salida.get("fecha_salida"), dias)


def _mail_estado(subject_prefix, salida, nuevo_estado, dias_restantes):
    """Envía un mail simple con el estado de plazo de una salida."""
    html = f"""
    <h3>{subject_prefix}: {salida.get('proyecto','(Sin título)')}</h3>
    <p><b>Lugar:</b> {salida.get('lugar','')}</p>
    <p><b>Fecha de salida:</b> {salida.get('fecha_salida')}</p>
    <p><b>Estado de plazo:</b> {nuevo_estado} {'' if dias_restantes is None else f'({dias_restantes} días)'}</p>
    <p><b>Acceso rápido:</b> /salidas/{str(salida.get('_id'))}</p>
    """
    send_email(f"[Salidas] {subject_prefix}", html)


def _required_annexes(tipo_destino, duracion_horas, regreso_en_el_dia):
    """
    Requisitos mínimos de anexos (ajustables):

      IV (itinerario), V (nómina), VI (autorización familiar) / VII (si +18),
      VIII (transporte/seguros), IX (declaración directivo).

    Por ahora todos activados; “III” lo manejamos como opcional (checklist).
    """
    req = {"IV": True, "V": True, "VI": True, "VIII": True, "IX": True}
    opcionales = {"III": True}
    return req, opcionales

# =========================================================
# Rutas CRUD Salidas
# =========================================================

@salidas_bp.route("/", methods=["GET"])
def listado_salidas():
    """Listado principal de salidas (vista HTML)."""
    db = _db()
    q = {}
    texto = (request.args.get("q") or "").strip()
    if texto:
        q["$or"] = [
            {"proyecto": {"$regex": texto, "$options": "i"}},
            {"lugar": {"$regex": texto, "$options": "i"}},
            {"institucion": {"$regex": texto, "$options": "i"}},
        ]
    salidas = list(db.salidas.find(q).sort([("fecha_salida", 1)]))
    for s in salidas:
        s["_id"] = str(s["_id"])
    return render_template("salidas_list.html", salidas=salidas)


@salidas_bp.route("/api", methods=["POST"])
def crear_salida():
    """Crea una salida nueva (JSON → Mongo)."""
    db = _db()
    data = request.json or {}
    doc = {
        "region": data.get("region", ""),
        "distrito": data.get("distrito", ""),
        "institucion": data.get("institucion", ""),
        "domicilio": data.get("domicilio", ""),
        "telefono": data.get("telefono", ""),
        "proyecto": data.get("proyecto", ""),
        "lugar": data.get("lugar", ""),
        "fecha_salida": _to_date(data.get("fecha_salida")),
        "fecha_regreso": _to_date(data.get("fecha_regreso")),
        "regreso_en_el_dia": _bool(data.get("regreso_en_el_dia")),
        "duracion_horas": int(data.get("duracion_horas") or 0),
        # barrio / dentro / fuera distrito / otra provincia / exterior
        "tipo_destino": data.get("tipo_destino", ""),
        "hospedaje": data.get("hospedaje", ""),
        "transporte": data.get("transporte", ""),
        "docentes_responsables": data.get("docentes_responsables", []),
        "docentes_reemplazantes": data.get("docentes_reemplazantes", []),
        "cant_alumnos": int(data.get("cant_alumnos") or 0),
        "cant_docentes_acomp": int(data.get("cant_docentes_acomp") or 0),
        "cant_nodocentes_acomp": int(data.get("cant_nodocentes_acomp") or 0),
        "gastos": data.get("gastos", ""),
        "observaciones": data.get("observaciones", ""),
        "anexos": {k: False for k in ("III", "IV", "V", "VI", "VII", "VIII", "IX")},
        "archivos": {},
    }

    # estado de plazo inicial
    doc["estado_plazo"] = _estado_plazo(doc)

    r = db.salidas.insert_one(doc)
    return jsonify({"ok": True, "id": str(r.inserted_id)})


@salidas_bp.route("/api/<id>", methods=["PATCH"])
def actualizar_salida(id):
    """Actualiza campos editables de una salida existente."""
    db = _db()
    try:
        _id = ObjectId(id)
    except Exception:
        abort(404)

    data = request.json or {}

    # estado previo (para ver si cambia a PROXIMO/VENCIDO)
    prev = db.salidas.find_one({"_id": _id}) or {}
    prev_estado = (prev.get("estado_plazo") or {}).get("estado")

    # Campos permitidos
    fields = {
        k: v
        for k, v in data.items()
        if k
        in (
            "region",
            "distrito",
            "institucion",
            "domicilio",
            "telefono",
            "proyecto",
            "lugar",
            "fecha_salida",
            "fecha_regreso",
            "regreso_en_el_dia",
            "duracion_horas",
            "tipo_destino",
            "hospedaje",
            "transporte",
            "docentes_responsables",
            "docentes_reemplazantes",
            "cant_alumnos",
            "cant_docentes_acomp",
            "cant_nodocentes_acomp",
            "gastos",
            "observaciones",
            "anexos",
            "archivos",
        )
    }

    if "fecha_salida" in fields:
        fields["fecha_salida"] = _to_date(fields["fecha_salida"])
    if "fecha_regreso" in fields:
        fields["fecha_regreso"] = _to_date(fields["fecha_regreso"])

    db.salidas.update_one({"_id": _id}, {"$set": fields})

    # Recalculo estado de plazo + posible notificación
    s = db.salidas.find_one({"_id": _id}) or {}
    nuevo = _estado_plazo(s)
    db.salidas.update_one({"_id": _id}, {"$set": {"estado_plazo": nuevo}})

    nuevo_estado = nuevo.get("estado")
    if prev_estado in ("OK", "PROXIMO") and nuevo_estado in ("PROXIMO", "VENCIDO") and nuevo_estado != prev_estado:
        prefix = "Plazo próximo" if nuevo_estado == "PROXIMO" else "Plazo vencido"
        _mail_estado(prefix, s, nuevo_estado, nuevo.get("dias_restantes"))

    return jsonify({"ok": True})


@salidas_bp.route("/api/<id>", methods=["GET"])
def obtener_salida(id):
    """Devuelve la salida en JSON + info de alertas / anexos."""
    db = _db()
    try:
        _id = ObjectId(id)
    except Exception:
        abort(404)

    s = db.salidas.find_one({"_id": _id})
    if not s:
        abort(404)

    s["_id"] = str(s["_id"])

    dias = _deadline_days(
        s.get("tipo_destino"), s.get("regreso_en_el_dia"), s.get("duracion_horas")
    )
    status = _deadline_status(s.get("fecha_salida"), dias)
    req, opc = _required_annexes(
        s.get("tipo_destino"), s.get("duracion_horas"), s.get("regreso_en_el_dia")
    )

    s["alertas"] = {
        "dias_anticipacion_requeridos": dias,
        "deadline_status": status,
        "requisitos_minimos": req,
        "opcionales": opc,
    }
    return jsonify(s)


@salidas_bp.route("/api/<id>/elevar", methods=["POST"])
def elevar_salida(id):
    """
    Marca una salida como “elevada” solo si tiene los anexos obligatorios.
    """
    db = _db()
    try:
        _id = ObjectId(id)
    except Exception:
        abort(404)

    s = db.salidas.find_one({"_id": _id}) or {}
    req, _ = _required_annexes(
        s.get("tipo_destino"),
        s.get("duracion_horas"),
        s.get("regreso_en_el_dia"),
    )
    anexos = s.get("anexos", {})
    faltantes = [k for k, v in req.items() if v and not anexos.get(k)]
    if faltantes:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Faltan anexos requeridos",
                    "faltantes": faltantes,
                }
            ),
            400,
        )

    db.salidas.update_one({"_id": _id}, {"$set": {"elevado_en": datetime.utcnow()}})
    est = s.get("estado_plazo", {}) or {}
    _mail_estado(
        "Salida ELEVADA",
        s,
        est.get("estado"),
        est.get("dias_restantes"),
    )
    return jsonify({"ok": True})

# =========================================================
# Helpers específicos para anexos
# =========================================================

def _cargar_salida(id: str) -> dict:
    """Carga una salida por ID o devuelve 404 si no existe."""
    db = _db()
    try:
        _id = ObjectId(id)
    except Exception:
        abort(404)

    s = db.salidas.find_one({"_id": _id})
    if not s:
        abort(404)

    s["_id"] = str(s["_id"])
    return s

# =========================================================
# ANEXOS III a IX  (todas las vistas HTML)
# =========================================================

@salidas_bp.route("/<id>/anexo/III")
def anexo_iii(id):
    """
    ANEXO III – Lista de verificación / proyecto.
    El template usa:
      - {{ s.* }} / {{ salida.* }}
      - {{ escuela.* }}
    """
    s = _cargar_salida(id)
    return render_template(
        "anexo_iii.html",
        s=s,
        salida=s,
        escuela=ESCUELA_INFO,
    )


@salidas_bp.route("/<id>/anexo/IV")
def anexo_iv(id):
    """
    ANEXO IV – Nómina general de estudiantes y acompañantes.
    El template usa:
      - {{ s.* }} / {{ salida.* }}
      - {{ escuela.* }}
      - {{ estudiantes }}
    """
    db = _db()
    s = _cargar_salida(id)

    estudiantes = list(
        db.estudiantes.find({"curso_actual": {"$exists": True}}).sort(
            [("apellido", 1), ("nombre", 1)]
        )
    )

    return render_template(
        "anexo_iv.html",
        s=s,
        salida=s,
        escuela=ESCUELA_INFO,
        estudiantes=estudiantes,
    )


@salidas_bp.route("/<id>/anexo/V")
def anexo_v(id):
    """
    ANEXO V – Nómina de estudiantes y acompañantes específicos de la salida.
    El template usa:
      - {{ salida.* }} / {{ s.* }}
      - {{ participantes }}
      - {{ escuela.* }}
    """
    db = _db()
    s = _cargar_salida(id)

    # En la DB veníamos guardando salida_id como string
    participantes = list(
        db.participantes.find({"salida_id": id}).sort(
            [("apellido", 1), ("nombre", 1)]
        )
    )

    return render_template(
        "anexo_v.html",
        s=s,
        salida=s,
        participantes=participantes,
        escuela=ESCUELA_INFO,
    )


@salidas_bp.route("/<id>/anexo/VI")
def anexo_vi(id):
    """
    ANEXO VI – Autorización familiar (por estudiante).
    Si se pasa ?est=<id_estudiante> se precarga ese alumno, si no el
    template queda más genérico.
    """
    db = _db()
    s = _cargar_salida(id)

    est_id = request.args.get("est")
    estudiante = None
    if est_id:
        try:
            estudiante = db.estudiantes.find_one({"_id": ObjectId(est_id)})
        except Exception:
            estudiante = None

    return render_template(
        "anexo_vi.html",
        s=s,
        salida=s,
        escuela=ESCUELA_INFO,
        estudiante=estudiante,
    )


@salidas_bp.route("/<id>/anexo/VII")
def anexo_vii(id):
    """
    ANEXO VII – Declaración para mayores de edad (si aplica).
    """
    s = _cargar_salida(id)
    return render_template(
        "anexo_vii.html",
        s=s,
        salida=s,
        escuela=ESCUELA_INFO,
    )


@salidas_bp.route("/<id>/anexo/VIII")
def anexo_viii(id):
    """
    ANEXO VIII – Transporte / seguros.
    El template usa:
      - {{ transporte.* }} (documento externo o campo de la salida)
      - {{ escuela.* }}
    """
    db = _db()
    s = _cargar_salida(id)

    # Si existe una colección específica de transportes, la usamos.
    transporte = db.transportes.find_one({"salida_id": id}) or {}
    if not transporte:
        # fallback: datos básicos guardados en la propia salida
        transporte = s.get("transporte", {}) or {}

    return render_template(
        "anexo_viii.html",
        s=s,
        salida=s,
        escuela=ESCUELA_INFO,
        transporte=transporte,
    )


@salidas_bp.route("/<id>/anexo/IX")
def anexo_ix(id):
    """
    ANEXO IX – Declaración jurada de directivos / representantes legales.
    """
    db = _db()
    s = _cargar_salida(id)

    directivos = list(db.directivos.find().sort([("apellido", 1)]))

    return render_template(
        "anexo_ix.html",
        s=s,
        salida=s,
        escuela=ESCUELA_INFO,
        directivos=directivos,
    )

# =========================================================
# API de alertas / tablero de control
# =========================================================

@salidas_bp.route("/api/alertas", methods=["GET"])
def alertas_tablero():
    """
    Devuelve listado de salidas con:
      - estado de plazo (OK/PROXIMO/VENCIDO)
      - días requeridos / pendientes
      - anexos faltantes
    """
    db = _db()
    out = []
    for s in db.salidas.find({}).sort([("fecha_salida", 1)]):
        dias = _deadline_days(
            s.get("tipo_destino"),
            s.get("regreso_en_el_dia"),
            s.get("duracion_horas"),
        )
        status = _deadline_status(s.get("fecha_salida"), dias)
        req, _ = _required_annexes(
            s.get("tipo_destino"),
            s.get("duracion_horas"),
            s.get("regreso_en_el_dia"),
        )
        anexos = s.get("anexos", {})
        faltantes = [k for k, v in req.items() if v and not anexos.get(k)]
        out.append(
            {
                "id": str(s["_id"]),
                "proyecto": s.get("proyecto", ""),
                "lugar": s.get("lugar", ""),
                "fecha_salida": s.get("fecha_salida"),
                "dias_requeridos": dias,
                "deadline": status,
                "anexos_faltantes": faltantes,
            }
        )
    return jsonify(out)
