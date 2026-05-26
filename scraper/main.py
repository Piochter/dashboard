#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║       SCRAPER ALERTAS VIALES MÉXICO v3.3 — AssistCargo              ║
║  Fuentes:                                                           ║
║    • Google News RSS   — queries específicos de vialidad            ║
║    • RSS nacionales    — medios con filtro estricto                 ║
║    • RSS regionales    — medios estatales                           ║
║    • CAPUFE directo    — XML oficial                                ║
║    • CONAGUA/SMN       — avisos meteorológicos                      ║
║    • Telegram          — canales oficiales de vialidad              ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import json, re, hashlib, logging, time, os, unicodedata, asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests, feedparser
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────
LOOKBACK_MINUTES          = int(os.getenv("SCRAPER_LOOKBACK_MINUTES", str(12 * 60)))
LOOKBACK_HORAS            = LOOKBACK_MINUTES / 60
MAX_POR_FEED              = int(os.getenv("MAX_POR_FEED", "30"))
RUN_MODE                  = os.getenv("SCRAPER_RUN_MODE", "all")
ACCEPT_UNDATED            = os.getenv("SCRAPER_ACCEPT_UNDATED", "true").lower() == "true"
DEBUG_DATES               = os.getenv("SCRAPER_DEBUG_DATES", "false").lower() == "true"
DEBUG_REJECTIONS          = os.getenv("SCRAPER_DEBUG_REJECTIONS", "false").lower() == "true"

TELEGRAM_CANALES = [
    # Verificados y activos
    "monitorcarreteras",    # Monitor Carreteras 57 — muy activo
    "NOTMEX",               # México Noticias — noticias generales con vialidad
    "jornadaedomex",        # Jornada Estado de México — accidentes y bloqueos
    "AlertaChiapas",        # Alerta Chiapas — bloqueos y carreteras
    "ElDiarioDeJuarez",     # El Diario de Juárez — vialidad norte
]
# ─────────────────────────────────────────────────────────────────────
# CANALES TELEGRAM DE VIALIDAD
# ─────────────────────────────────────────────────────────────────────
TELEGRAM_CANALES = [
    # Oficiales federales
    "capufe_mx",
    "GN_Carreteras",
    "proteccion_civil_mexico",
    # CDMX y zona metropolitana
    "vialidadcdmx",
    "c5cdmx",
    "ovialcdmx",
    # Regionales
    "vialidad_edomex",
    "ssp_jalisco_vial",
    "vialidad_nl",
    "transito_puebla",
    # Especializados transporte de carga
    "transportistas_mx",
    "carga_carretera_mexico",
    "alertas_viales_mexico",
]

# ─────────────────────────────────────────────────────────────────────
# GOOGLE NEWS RSS
# ─────────────────────────────────────────────────────────────────────
GNEWS_BASE = "https://news.google.com/rss/search?q={query}&hl=es-419&gl=MX&ceid=MX%3Aes-419"

GNEWS_QUERIES = [
    "cierre carretero México autopista hoy",
    "cierre vial autopista federal México",
    "carretera cerrada México accidente hoy",
    "CAPUFE cierre vial alerta",
    "GN_Carreteras cierre bloqueo México",
    "Guardia Nacional carreteras cierre México",
    "SCT SICT cierre carretera federal",
    "volcadura tractocamión autopista México",
    "accidente carretera México tráiler camión",
    "derrumbe deslave carretera México",
    "inundación carretera autopista México",
    "neblina cierre autopista México",
    "incendio vehículo autopista México",
    "bloqueo manifestación carretera federal México",
    "manifestantes toma caseta autopista México",
    "comuneros bloqueo carretera México",
    "huelga paro carretera México bloqueo",
    "robo carretera autopista México asalto",
    "robo transporte de carga autopista México",
    "asalto tractocamión carretera México",
    "robo combustible pipa autopista México",
    "autopista México Querétaro 57D cierre accidente",
    "autopista México Puebla 150D cierre volcadura",
    "autopista México Acapulco 95D cierre bloqueo",
    "autopista Siglo XXI Manzanillo cierre accidente",
    "autopista México Veracruz 140D cierre",
    "autopista México Laredo 85D cierre accidente",
    "cierre carretera Oaxaca Guerrero Chiapas",
    "bloqueo carretera Tamaulipas Nuevo León",
]

# ─────────────────────────────────────────────────────────────────────
# RSS MEDIOS — solo secciones de tráfico/vialidad cuando existen
# ─────────────────────────────────────────────────────────────────────
RSS_NACIONALES = [
    ("24 Horas",         "https://www.24-horas.mx/feed/"),
    ("Milenio Policial", "https://www.milenio.com/rss/policia"),
    ("El Universal MX",  "https://www.eluniversal.com.mx/nacion/rss.xml"),
    ("La Silla Rota",    "https://lasillarota.com/feed/"),
    ("SDP Noticias",     "https://www.sdpnoticias.com/feed/"),
]

RSS_REGIONALES = [
    ("El Sol de México",      "https://www.elsoldemexico.com.mx/rss.xml"),
    ("El Informador Jalisco", "https://www.informador.mx/rss/ultimas-noticias.xml"),
    ("Quadratín Michoacán",   "https://www.quadratin.com.mx/rss"),
    ("AM Guanajuato",         "https://www.am.com.mx/rss"),
    ("NTR Guadalajara",       "https://www.ntrguadalajara.com/feed/"),
    ("E-consulta Puebla",     "https://e-consulta.com/feed/"),
    ("El Horizonte NL",       "https://www.elhorizonte.mx/feed"),
]

RSS_VIALES_EXTRA = [
    ("Quadratín Oaxaca",    "https://oaxaca.quadratin.com.mx/feed/"),
    ("Quadratín Veracruz",  "https://veracruz.quadratin.com.mx/feed/"),
    ("Quadratín Puebla",    "https://puebla.quadratin.com.mx/feed/"),
    ("Quadratín Jalisco",   "https://jalisco.quadratin.com.mx/feed/"),
    ("Quadratín Guerrero",  "https://guerrero.quadratin.com.mx/feed/"),
    ("Quadratín Michoacán", "https://michoacan.quadratin.com.mx/feed/"),
    ("Quadratín Tamaulipas","https://tamaulipas.quadratin.com.mx/feed/"),
    ("El Imparcial Oaxaca", "https://www.imparcialoaxaca.mx/feed/"),
    ("Debate Sinaloa",      "https://www.debate.com.mx/feed/"),
]

CONAGUA_URLS = [
    "https://smn.conagua.gob.mx/tools/RESOURCES/Avisos/AvisoMeteorologico.xml",
    "https://smn.conagua.gob.mx/tools/RESOURCES/avisos/avisos.xml",
]

CAPUFE_URLS = [
    "https://www.capufe.gob.mx/site/xml/ReporteVialidad.xml",
    "https://www.capufe.gob.mx/site/webSCT/comunicados.xml",
    "https://www.capufe.gob.mx/norteMonitor/",
]

# ─────────────────────────────────────────────────────────────────────
# CLASIFICACIÓN
# ─────────────────────────────────────────────────────────────────────
KEYWORDS = {
    "cierre_total": [
        "cierre total", "cerrado totalmente", "sin circulación", "volcadura",
        "derrumbe", "deslave", "inundación total", "completamente cerrado",
        "ambos carriles cerrados", "cierre completo", "totalmente obstruido",
        "hundimiento", "colapso", "puente cerrado",
    ],
    "bloqueo": [
        "bloqueo", "manifestación", "manifestantes", "protesta", "inconformes",
        "comuneros", "pobladores", "toma de caseta", "huelga", "paro",
        "encadenados", "quema de llantas", "retención de personas",
    ],
    "robo": [
        "robo", "asalto", "asaltantes", "delincuentes carretera", "banda",
        "robo a transporte", "pipas robadas", "robo de combustible",
        "motochorros", "asalto a mano armada", "jalón",
    ],
    "cierre_parcial": [
        "cierre parcial", "un carril", "reducción de carril", "maniobras",
        "percance", "accidente", "choque", "volcadura parcial", "neblina",
        "falla mecánica", "tractocamión varado", "carril cerrado",
        "colisión", "impacto vial", "auto volcado", "camión volcado",
    ],
    "carga_vehicular": [
        "carga vehicular", "tránsito lento", "lento avance", "saturación",
        "congestionamiento", "avance lento", "tráfico pesado", "cola",
        "kilometros de fila", "fila de vehículos", "retención vial",
    ],
    "obra": [
        "obra vial", "trabajos viales", "rehabilitación",
        "mantenimiento vial", "bacheo", "pavimentación", "reparación",
        "señalización vial", "ampliación carretera",
    ],
    "clima": [
        "inundación", "lluvia intensa", "neblina densa", "granizo", "tormenta",
        "alerta meteorológica", "onda de calor", "frente frío",
        "huracán", "ciclón", "viento fuerte", "visibilidad reducida",
        "niebla", "helada", "nevada", "pavimento resbaladizo",
    ],
}

TIPO_CONFIG = {
    "cierre_total":    dict(color="rojo",    icono="🔴", label="CIERRE TOTAL",                dot="#e74c3c", badge="badge-cierre-total",   badge_txt="CIERRE TOTAL",    col2=False, orden=0),
    "bloqueo":         dict(color="rojo",    icono="⛔", label="BLOQUEOS / MANIFESTACIONES",  dot="#8e44ad", badge="badge-bloqueo",         badge_txt="BLOQUEO",         col2=False, orden=1),
    "robo":            dict(color="rojo",    icono="🚨", label="ROBOS EN CARRETERA",           dot="#c0392b", badge="badge-robo",            badge_txt="ROBO",            col2=True,  orden=2),
    "cierre_parcial":  dict(color="amarillo",icono="🟡", label="CIERRE PARCIAL / ACCIDENTES",  dot="#e67e22", badge="badge-cierre-parcial",  badge_txt="CIERRE PARCIAL",  col2=True,  orden=3),
    "carga_vehicular": dict(color="azul",    icono="🚗", label="CARGA VEHICULAR",              dot="#2980b9", badge="badge-carga",           badge_txt="CARGA VEHICULAR", col2=True,  orden=4),
    "obra":            dict(color="verde",   icono="🚧", label="OBRA CONTINUA",                dot="#27ae60", badge="badge-obra",            badge_txt="OBRA CONTINUA",   col2=False, orden=5),
    "clima":           dict(color="azul",    icono="🌧️", label="ALERTA METEOROLÓGICA",         dot="#3498db", badge="badge-clima",           badge_txt="ALERTA CLIMA",    col2=False, orden=6),
}

# ─────────────────────────────────────────────────────────────────────
# COORDENADAS
# ─────────────────────────────────────────────────────────────────────
COORD_MAP = {
    "mexico puebla": (19.35, -98.40),          "150d": (19.35, -98.40),
    "mexico queretaro": (20.10, -99.50),        "57d": (20.10, -99.50),
    "mexico guadalajara": (20.40, -103.35),     "15d": (20.40, -103.35),
    "mexico veracruz": (19.20, -96.80),         "140d": (19.20, -96.80),
    "mexico acapulco": (17.55, -99.50),         "95d": (17.55, -99.50),
    "mexico laredo": (24.00, -99.00),           "85d": (24.00, -99.00),
    "mexico tuxpan": (20.50, -97.90),           "130d": (20.50, -97.90),
    "tepic guadalajara": (21.00, -104.00),      "15": (21.00, -104.00),
    "puebla cordoba": (18.90, -97.00),          "150": (18.90, -97.00),
    "siglo xxi": (19.30, -104.00),
    "cuernavaca acapulco": (18.20, -99.20),
    "amozoc": (19.10, -98.00),
    "arco norte": (19.80, -99.10),
    "monterrey saltillo": (25.50, -100.90),     "40d": (25.50, -100.90),
    "monterrey laredo": (26.50, -99.50),        "85": (26.50, -99.50),
    "colima manzanillo": (19.10, -104.30),      "200": (19.10, -104.30),
    "aguascalientes guadalajara": (21.20, -102.50), "45d": (21.20, -102.50),
    "celaya queretaro": (20.55, -100.60),
    "queretaro san luis": (21.50, -100.00),     "57": (21.50, -100.00),
    "torreon saltillo": (25.20, -102.00),       "40": (25.20, -102.00),
    "tijuana ensenada": (31.70, -116.70),
    "chihuahua ciudad juarez": (30.40, -106.40),
    "durango mazatlan": (24.50, -106.00),
    "oaxaca istmo": (16.50, -95.00),
    "xalapa veracruz": (19.35, -96.60),
    "merida cancun": (20.50, -87.90),
    "tuxtla gutierrez": (16.75, -93.12),        "190": (16.75, -93.12),
    "jalisco": (20.66, -103.35),   "veracruz": (19.18, -96.14),
    "oaxaca": (17.06, -96.72),     "guerrero": (17.55, -99.50),
    "chiapas": (16.75, -93.12),    "puebla": (19.04, -98.20),
    "hidalgo": (20.11, -98.73),    "michoacan": (19.70, -101.19),
    "guanajuato": (21.02, -101.26),"cdmx": (19.43, -99.13),
    "edomex": (19.35, -99.70),     "tamaulipas": (24.26, -98.84),
    "nuevo leon": (25.67, -100.31),"sinaloa": (24.80, -107.39),
    "sonora": (29.07, -110.96),    "chihuahua": (28.64, -106.08),
    "baja california": (30.84, -115.28),
    "coahuila": (27.06, -101.71),  "durango": (24.02, -104.66),
    "zacatecas": (22.77, -102.58), "san luis": (22.15, -100.97),
    "nayarit": (21.75, -104.85),   "colima": (19.24, -103.72),
    "morelos": (18.67, -99.10),    "queretaro": (20.59, -100.39),
    "tabasco": (17.99, -92.93),    "campeche": (19.83, -90.53),
    "yucatan": (20.97, -89.62),    "quintana roo": (18.50, -88.30),
    "monterrey": (25.67, -100.31), "guadalajara": (20.66, -103.35),
    "tijuana": (32.52, -117.00),   "culiacan": (24.80, -107.39),
    "mazatlan": (23.24, -106.41),  "manzanillo": (19.05, -104.32),
    "acapulco": (16.86, -99.88),   "cancun": (21.16, -86.85),
    "merida": (20.97, -89.62),     "cuernavaca": (18.92, -99.23),
    "toluca": (19.29, -99.66),     "pachuca": (20.12, -98.73),
    "xalapa": (19.53, -96.91),     "villahermosa": (17.99, -92.93),
    "tuxtla": (16.75, -93.12),     "hermosillo": (29.07, -110.96),
}

# ─────────────────────────────────────────────────────────────────────
# FALSOS POSITIVOS — muy ampliado
# ─────────────────────────────────────────────────────────────────────
FALSOS_POSITIVOS = [
    # Resolución de incidentes
    "reabre", "restablece circulacion", "circulacion normal", "sin novedad",
    "se normaliza", "ya liberaron", "retiraron bloqueo", "fue detenido",
    "fueron detenidos", "capturan", "capturaron", "detienen banda",
    # Histórico
    "simulacro", "en memoria", "aniversario", "recuerdan", "conmemoran",
    "hace 10 anos", "hace un ano", "archivo", "reportaje especial",
    "analisis de", "tendencias de", "estadisticas de", "ranking de",
    # Finanzas y economía
    "cierre de mercado", "cierre bursatil", "bolsa de valores",
    "cierre de ano", "cierre fiscal", "cierre de operaciones",
    "cierre de empresa", "cierre de negocio", "cierre de planta",
    "petroleo", "iran", "trump", "sanciones", "aranceles", "dolar",
    "inflacion", "banco ", "pib ", "inversion", "exporta",
    "importa", "comercio exterior", "finanzas", "presupuesto",
    "pension afore", "credito", "hipoteca", "deuda publica",
    "tipo de cambio", "banxico", "banco de mexico", "reforma fiscal",
    # Política
    "eleccion", "candidato", "politico", "congreso", "senado", "diputado",
    "partido ", "morena", "pan ", "pri ", "gobierno federal anuncia",
    # Deportes
    "futbol", "liga mx", "deportes", "beisbol", "basquetbol", "nfl", "nba",
    "champions league", "copa mx", "derrota", "victoria", "gol", "partido",
    "jugador", "equipo ", "tecnico ", "torneo", "atleta",
    "rayadas", "chivas", "america fc", "cruz azul", "pumas", "tigres",
    # Entretenimiento
    "concierto", "festival", "espectaculo", "cine", "television",
    "serie de tv", "pelicula", "estreno", "netflix", "disney",
    "the boys", "amazon prime", "spotify", "cantante", "actor", "actriz",
    # Salud
    "covid", "vacuna", "salud ", "hospital", "medico", "enfermedad",
    "sindrome", "padecimiento", "tratamiento medico", "clinica",
    "ovario poliquistico", "diabetes", "cancer", "obesidad",
    # Inmobiliaria
    "inmobiliaria", "vivienda", "departamento", "construccion residencial",
    # Tecnología
    "inteligencia artificial", "startup", "software", "hardware",
    "criptomoneda", "bitcoin", "nft", "metaverso",
    # AIFA / aeropuerto (no es carretera)
    "aifa", "aeropuerto", "vuelo", "aerolinea", "terminal aerea",
    "pasajeros aereos", "pista de aterrizaje",
    # Gastronomía
    "receta", "cocina", "gastronomia", "restaurante",
    # Medio ambiente no vial
    "hidro sustentable", "ahorro de agua", "sustentabilidad ambiental",
    "distintivo", "certificacion", "premio", "reconocimiento",
]

# ─────────────────────────────────────────────────────────────────────
# KEYWORDS VIALES — primarios (incidente) + contexto (lugar)
# ─────────────────────────────────────────────────────────────────────
VIAL_CONTEXTO = [
    "carretera", "autopista", "capufe", "guardia nacional",
    "tractocamion", "caseta", "peaje", "tramo", "libramiento",
    "57d", "95d", "150d", "15d", "85d", "140d", "siglo xxi", "arco norte",
    "km ", "kilometro", "viaducto", "periferico", "carretera federal",
    "vialidad", "circulacion", "transporte de carga",
    "mexico queretaro", "mexico puebla", "mexico cuernavaca",
    "mexico pachuca", "mexico toluca", "mexico acapulco",
    "mexico veracruz", "mexico laredo",
]

VIAL_EVENTO = [
    "cierre", "bloqueo", "accidente", "volcadura", "choque", "colision",
    "derrumbe", "deslave", "inundacion", "neblina", "incendio",
    "robo", "asalto", "manifestacion", "manifestantes", "protesta",
    "comuneros", "huelga", "paro", "reduccion", "percance",
    "congestionamiento", "retencion", "obstruccion",
    "falla mecanica", "vehiculo varado", "trafico lento",
    "cerrada", "cerrado", "bloqueada", "bloqueado",
]

CST   = timezone(timedelta(hours=-6))
MESES = ["enero","febrero","marzo","abril","mayo","junio",
         "julio","agosto","septiembre","octubre","noviembre","diciembre"]

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("alertas")

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept-Language": "es-MX,es;q=0.9",
    "Accept":          "application/rss+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.8",
}

_STATS = {
    "fuera_ventana": 0, "no_relevante": 0,
    "falso_positivo": 0, "duplicado_en_feed": 0,
    "sin_fecha_parseable": 0,
}

# ─────────────────────────────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────────────────────────────

def get(url: str, timeout: int = 15) -> Optional[requests.Response]:
    for intento in range(1, 4):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            log.debug(f"GET {url} intento {intento} → {e}")
            if intento < 3:
                time.sleep(1.5 * intento)
    return None


def limpiar(html: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(html or "", "html.parser").get_text()).strip()


def normalizar(texto: str) -> str:
    texto = (texto or "").lower()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = texto.replace("–", "-").replace("—", "-")
    texto = re.sub(r"[-_/]+", " ", texto)
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def make_id(texto: str) -> str:
    return hashlib.md5((texto or "").encode()).hexdigest()[:8]


def clasificar(texto: str) -> str:
    t = normalizar(texto)
    for tipo, palabras in KEYWORDS.items():
        if any(normalizar(p) in t for p in palabras):
            return tipo
    return "cierre_parcial"


def es_relevante(texto: str) -> bool:
    """
    Requiere:
      1. Al menos UN keyword de evento vial
      2. Al menos UN keyword de contexto carretero
    Ambos deben estar presentes — evita noticias que solo mencionan
    'accidente' o 'bloqueo' en contexto no vial.
    """
    t = normalizar(texto)
    tiene_contexto = any(normalizar(k) in t for k in VIAL_CONTEXTO)
    tiene_evento   = any(normalizar(k) in t for k in VIAL_EVENTO)
    tiene_ruta     = bool(re.search(
        r"\b(\d{2,3}d?)\b|mexico\s+\w+|autopista|carretera|libramiento|caseta|km\s*\d+",
        t
    ))
    return tiene_evento and (tiene_contexto or tiene_ruta)


def es_falso_positivo(texto: str) -> bool:
    t = normalizar(texto)
    return any(normalizar(fp) in t for fp in FALSOS_POSITIVOS)


def extraer_coords(texto: str) -> Optional[tuple]:
    t = normalizar(texto)
    km_match = re.search(r"km\s*(\d+)", t)
    for nombre, coords in COORD_MAP.items():
        if normalizar(nombre) in t:
            if km_match:
                km = int(km_match.group(1))
                return (coords[0] + (km % 10) * 0.01,
                        coords[1] + (km % 10) * 0.01)
            return coords
    return None


def extraer_ruta(texto: str) -> str:
    patrones = [
        r"(?:Autopista|Carretera|Libramiento|Periférico|Viaducto|Boulevard|Blvd\.?)\s+"
        r"[\w\s\-–áéíóúÁÉÍÓÚñÑ]+?(?=\s*(?:km\b|tramo|,|\.|·|$))",
        r"(?:km|kilómetro)\s+\d+[\+\.\d]*(?:\s*[\+\-]\s*\d+)?",
        r"\b\d{1,3}D?\b(?=\s+(?:km|tramo|libre|cuota))",
    ]
    for p in patrones:
        m = re.search(p, texto or "", re.IGNORECASE)
        if m:
            return m.group(0).strip()[:90]
    return " ".join((texto or "").split()[:10]) + "…"


def extraer_rec(texto: str) -> str:
    m = re.search(
        r"(?:se recomienda|alternativa[:\s]|usar[:\s]|evitar[:\s]|desvío[:\s])"
        r"[^.!?\n]{10,160}",
        texto or "", re.IGNORECASE)
    return m.group(0).strip() if m else ""


def _entry_fecha(entry) -> Optional[datetime]:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        st = entry.get(key)
        if st:
            try:
                return datetime(*st[:6], tzinfo=timezone.utc).astimezone(CST)
            except Exception:
                pass
    from email.utils import parsedate_to_datetime
    for key in ("published", "updated", "pubDate", "date"):
        s = entry.get(key, "")
        if not s:
            continue
        try:
            dt = parsedate_to_datetime(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(CST)
        except Exception:
            pass
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(CST)
        except Exception:
            pass
    return None


def esta_en_ventana_entry(entry) -> tuple[bool, Optional[datetime]]:
    dt = _entry_fecha(entry)
    if dt is None:
        _STATS["sin_fecha_parseable"] += 1
        return (ACCEPT_UNDATED, None)
    delta = datetime.now(CST) - dt
    en_ventana = 0 <= delta.total_seconds() <= LOOKBACK_MINUTES * 60
    if not en_ventana:
        _STATS["fuera_ventana"] += 1
    return (en_ventana, dt)


def fmt_fecha_dt(dt: Optional[datetime]) -> str:
    if dt is None:
        return _ahora_str()
    return f"{dt.day} {MESES[dt.month-1]} {dt.year} · {dt.strftime('%H:%M')} CST"


def _ahora_str() -> str:
    n = datetime.now(CST)
    return f"{n.day} {MESES[n.month-1]} {n.year} · {n.strftime('%H:%M')} CST"


def _parse_fecha_alerta(fecha_str: str) -> datetime:
    try:
        m = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})\s*·\s*(\d{2}:\d{2})", fecha_str or "")
        if m:
            dia, mes_str, anio, hora = m.groups()
            mes = MESES.index(mes_str.lower()) + 1
            h, mi = map(int, hora.split(":"))
            return datetime(int(anio), mes, int(dia), h, mi, tzinfo=CST)
    except Exception:
        pass
    return datetime(1970, 1, 1, tzinfo=CST)


def hacer_alerta(tipo, ruta, desc, rec, fecha, fuente, url, extra_texto="") -> dict:
    c      = TIPO_CONFIG[tipo]
    coords = extraer_coords(desc + " " + ruta + " " + extra_texto)
    dt_al  = _parse_fecha_alerta(fecha)
    a = {
        "id":            make_id(desc),
        "tipo":          tipo,
        "ruta":          ruta,
        "descripcion":   desc[:500],
        "recomendacion": rec,
        "fecha":         fecha,
        "fecha_iso":     dt_al.isoformat() if dt_al.year > 1970 else None,
        "fuente":        fuente,
        "url":           url,
        "dot_color":     c["dot"],
        "badge":         c["badge"],
        "badge_txt":     c["badge_txt"],
    }
    if coords:
        a["lat"] = round(coords[0], 5)
        a["lon"] = round(coords[1], 5)
    return a


def _debug_rechazo(motivo: str, titulo: str):
    if DEBUG_REJECTIONS:
        log.info(f"    [rechazo:{motivo}] {titulo[:130]}")


# ─────────────────────────────────────────────────────────────────────
# FUENTE 1 — GOOGLE NEWS RSS
# ─────────────────────────────────────────────────────────────────────

def fetch_google_news() -> list[dict]:
    alertas, vistos = [], set()

    for query in GNEWS_QUERIES:
        url  = GNEWS_BASE.format(query=requests.utils.quote(query))
        resp = get(url, timeout=15)
        if not resp:
            log.warning(f"  GNews sin respuesta: {query[:40]}")
            continue

        feed = feedparser.parse(resp.text)
        nuevas = 0
        rec = {"ventana": 0, "relevancia": 0, "falso_pos": 0, "dup": 0}

        for entry in feed.entries[:MAX_POR_FEED]:
            titulo  = limpiar(entry.get("title", ""))
            resumen = limpiar(entry.get("summary", ""))
            texto   = f"{titulo}. {resumen}"

            en_ventana, dt = esta_en_ventana_entry(entry)
            if not en_ventana:
                rec["ventana"] += 1; _debug_rechazo("ventana", titulo); continue

            if not es_relevante(texto):
                rec["relevancia"] += 1; _STATS["no_relevante"] += 1
                _debug_rechazo("relevancia", titulo); continue

            if es_falso_positivo(texto):
                rec["falso_pos"] += 1; _STATS["falso_positivo"] += 1
                _debug_rechazo("falso_positivo", titulo); continue

            key = make_id(titulo[:60].lower())
            if key in vistos:
                rec["dup"] += 1; _STATS["duplicado_en_feed"] += 1; continue
            vistos.add(key)

            alertas.append(hacer_alerta(
                clasificar(texto), extraer_ruta(titulo), texto[:500],
                extraer_rec(texto), fmt_fecha_dt(dt),
                entry.get("source", {}).get("title", "Google News"),
                entry.get("link", ""), texto,
            ))
            nuevas += 1

        log.info(f"  GNews '{query[:38]}': {len(feed.entries)} → {nuevas} alertas "
                 f"[vent:{rec['ventana']} rel:{rec['relevancia']} fp:{rec['falso_pos']} dup:{rec['dup']}]")
        time.sleep(0.4)

    log.info(f"  ✓ Google News total: {len(alertas)} alertas")
    return alertas


# ─────────────────────────────────────────────────────────────────────
# FUENTE 2 — RSS PERIÓDICOS
# ─────────────────────────────────────────────────────────────────────

def _procesar_rss(feeds: list[tuple]) -> list[dict]:
    alertas, vistos = [], set()

    for nombre, rss_url in feeds:
        resp = get(rss_url, timeout=15)
        if not resp:
            log.warning(f"  RSS {nombre}: sin respuesta"); continue

        feed   = feedparser.parse(resp.text)
        nuevas = 0
        rec    = {"ventana": 0, "relevancia": 0, "falso_pos": 0, "dup": 0}

        for entry in feed.entries[:MAX_POR_FEED]:
            titulo  = limpiar(entry.get("title", ""))
            resumen = limpiar(entry.get("summary", ""))
            texto   = f"{titulo}. {resumen}"

            en_ventana, dt = esta_en_ventana_entry(entry)
            if not en_ventana:
                rec["ventana"] += 1; _debug_rechazo("ventana", titulo); continue

            if not es_relevante(texto):
                rec["relevancia"] += 1; _STATS["no_relevante"] += 1
                _debug_rechazo("relevancia", titulo); continue

            if es_falso_positivo(texto):
                rec["falso_pos"] += 1; _STATS["falso_positivo"] += 1
                _debug_rechazo("falso_positivo", titulo); continue

            key = make_id(titulo[:60].lower())
            if key in vistos:
                rec["dup"] += 1; _STATS["duplicado_en_feed"] += 1; continue
            vistos.add(key)

            alertas.append(hacer_alerta(
                clasificar(texto), extraer_ruta(titulo), texto[:500],
                extraer_rec(texto), fmt_fecha_dt(dt),
                nombre, entry.get("link", rss_url), texto,
            ))
            nuevas += 1

        log.info(f"  RSS {nombre}: {len(feed.entries)} → {nuevas} alertas "
                 f"[vent:{rec['ventana']} rel:{rec['relevancia']} fp:{rec['falso_pos']} dup:{rec['dup']}]")

    return alertas


def fetch_rss_nacionales() -> list[dict]:
    r = _procesar_rss(RSS_NACIONALES)
    log.info(f"  ✓ Periódicos nacionales: {len(r)} alertas")
    return r


def fetch_rss_regionales() -> list[dict]:
    r = _procesar_rss(RSS_REGIONALES)
    log.info(f"  ✓ Periódicos regionales: {len(r)} alertas")
    return r


def fetch_rss_extra() -> list[dict]:
    r = _procesar_rss(RSS_VIALES_EXTRA)
    log.info(f"  ✓ RSS extra (Quadratín + regionales): {len(r)} alertas")
    return r


# ─────────────────────────────────────────────────────────────────────
# FUENTE 3 — CAPUFE
# ─────────────────────────────────────────────────────────────────────

def fetch_capufe() -> list[dict]:
    alertas = []
    for url in CAPUFE_URLS:
        resp = get(url, timeout=15)
        if not resp:
            continue
        ct = resp.headers.get("content-type", "")
        if "xml" in ct or url.endswith(".xml"):
            feed = feedparser.parse(resp.text)
            if feed.entries:
                log.info(f"  CAPUFE XML: {len(feed.entries)} registros")
                for entry in feed.entries[:30]:
                    en_ventana, dt = esta_en_ventana_entry(entry)
                    if not en_ventana:
                        continue
                    titulo  = limpiar(entry.get("title", "Alerta CAPUFE"))
                    resumen = limpiar(entry.get("summary", titulo))
                    texto   = f"{titulo}. {resumen}"
                    alertas.append(hacer_alerta(
                        clasificar(texto), extraer_ruta(titulo), texto[:500],
                        extraer_rec(texto), fmt_fecha_dt(dt),
                        "CAPUFE", entry.get("link", url), texto,
                    ))
                return alertas
        soup  = BeautifulSoup(resp.text, "html.parser")
        filas = soup.select("tr, .reporte, .alerta-row, .vialidad-item, article")
        for fila in filas[:40]:
            texto = fila.get_text(separator=" ", strip=True)
            if len(texto) < 30 or not es_relevante(texto) or es_falso_positivo(texto):
                continue
            alertas.append(hacer_alerta(
                clasificar(texto), extraer_ruta(texto), texto[:500],
                extraer_rec(texto), _ahora_str(), "CAPUFE", url, texto,
            ))
        if alertas:
            log.info(f"  CAPUFE HTML: {len(alertas)} reportes")
            return alertas

    log.info(f"  ✓ CAPUFE: {len(alertas)} alertas")
    return alertas


# ─────────────────────────────────────────────────────────────────────
# FUENTE 4 — CONAGUA
# ─────────────────────────────────────────────────────────────────────

def fetch_conagua() -> list[dict]:
    alertas = []
    for url in CONAGUA_URLS:
        resp = get(url, timeout=15)
        if not resp:
            continue
        ct = resp.headers.get("content-type", "")
        if "xml" in ct or url.endswith(".xml"):
            feed = feedparser.parse(resp.text)
            if not feed.entries:
                continue
            log.info(f"  CONAGUA XML: {len(feed.entries)} avisos")
            for entry in feed.entries[:20]:
                en_ventana, dt = esta_en_ventana_entry(entry)
                if not en_ventana:
                    continue
                titulo  = entry.get("title", "Aviso meteorológico")
                resumen = limpiar(entry.get("summary", titulo))
                texto   = f"{titulo}. {resumen}"
                alertas.append(hacer_alerta(
                    "clima", titulo[:90], texto[:500],
                    "Maneja con precaución.", fmt_fecha_dt(dt),
                    "CONAGUA/SMN", entry.get("link", url), texto,
                ))
            return alertas
    log.info(f"  ✓ CONAGUA: {len(alertas)} avisos")
    return alertas


# ─────────────────────────────────────────────────────────────────────
# FUENTE 5 — TELEGRAM (canales públicos de vialidad)
# ─────────────────────────────────────────────────────────────────────

async def _fetch_telegram_async() -> list[dict]:
    """
    Lee mensajes recientes de canales públicos de Telegram.
    Requiere TELEGRAM_API_ID, TELEGRAM_API_HASH y TELEGRAM_SESSION_STRING
    configurados como secrets en GitHub Actions.
    """
    alertas = []

    if not all([TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION_STRING]):
        log.warning("  Telegram: credenciales no configuradas — omitiendo")
        return alertas

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        client = TelegramClient(
            StringSession(TELEGRAM_SESSION_STRING),
            int(TELEGRAM_API_ID),
            TELEGRAM_API_HASH,
        )

        await client.connect()

        if not await client.is_user_authorized():
            log.warning("  Telegram: sesión no autorizada")
            await client.disconnect()
            return alertas

        ahora = datetime.now(timezone.utc)

        for canal in TELEGRAM_CANALES:
            try:
                entity = await client.get_entity(canal)
                mensajes = await client.get_messages(entity, limit=50)

                nuevas = 0
                for msg in mensajes:
                    if not msg.text:
                        continue

                    # Verificar ventana de tiempo
                    msg_dt = msg.date
                    if msg_dt.tzinfo is None:
                        msg_dt = msg_dt.replace(tzinfo=timezone.utc)
                    delta = ahora - msg_dt
                    if delta.total_seconds() > LOOKBACK_MINUTES * 60:
                        continue

                    texto = msg.text.strip()
                    if len(texto) < 20:
                        continue

                    if not es_relevante(texto):
                        continue

                    if es_falso_positivo(texto):
                        continue

                    dt_cst = msg_dt.astimezone(CST)
                    alertas.append(hacer_alerta(
                        clasificar(texto),
                        extraer_ruta(texto),
                        texto[:500],
                        extraer_rec(texto),
                        fmt_fecha_dt(dt_cst),
                        f"Telegram @{canal}",
                        f"https://t.me/{canal}/{msg.id}",
                        texto,
                    ))
                    nuevas += 1

                log.info(f"  Telegram @{canal}: {nuevas} alertas")
                await asyncio.sleep(0.5)  # cortesía con la API

            except Exception as e:
                log.warning(f"  Telegram @{canal}: {e}")
                continue

        await client.disconnect()

    except ImportError:
        log.warning("  Telegram: telethon no instalado")
    except Exception as e:
        log.error(f"  Telegram error general: {e}")

    log.info(f"  ✓ Telegram total: {len(alertas)} mensajes")
    return alertas


def fetch_telegram() -> list[dict]:
    """Wrapper síncrono para la función async de Telegram."""
    try:
        return asyncio.run(_fetch_telegram_async())
    except Exception as e:
        log.error(f"  Telegram: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────
# DEDUP
# ─────────────────────────────────────────────────────────────────────

def _tokens(texto: str) -> set:
    return set(re.findall(r"\b[a-záéíóúñ]{4,}\b", normalizar(texto)))


def dedup(alertas: list[dict]) -> list[dict]:
    alertas_sorted = sorted(
        alertas,
        key=lambda a: _parse_fecha_alerta(a.get("fecha", "")),
        reverse=True,
    )
    seen_ids, seen_tokens, out = set(), [], []

    for a in alertas_sorted:
        uid = make_id(a["descripcion"][:80].lower())
        if uid in seen_ids:
            continue
        tok = _tokens(a["descripcion"][:120])
        duplicado = any(
            len(tok) > 3 and len(tok & st) / max(len(tok), len(st), 1) >= 0.70
            for st in seen_tokens
        )
        if duplicado:
            continue
        seen_ids.add(uid)
        seen_tokens.append(tok)
        out.append(a)

    return out


# ─────────────────────────────────────────────────────────────────────
# AGRUPAR
# ─────────────────────────────────────────────────────────────────────

def agrupar(alertas: list[dict]) -> list[dict]:
    grupos: dict[str, list] = {}
    for a in alertas:
        grupos.setdefault(a["tipo"], []).append(a)

    resultado = []
    for tipo, cfg in sorted(TIPO_CONFIG.items(), key=lambda x: x[1]["orden"]):
        if tipo not in grupos:
            continue
        resultado.append({
            "tipo":    tipo,
            "color":   cfg["color"],
            "icono":   cfg["icono"],
            "label":   cfg["label"],
            "col2":    cfg["col2"],
            "alertas": sorted(
                grupos[tipo],
                key=lambda a: _parse_fecha_alerta(a.get("fecha", "")),
                reverse=True,
            ),
        })

    return resultado


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

def main():
    log.info(
        f"═══  Scraper Alertas Viales México v3.3  ══  "
        f"ventana={LOOKBACK_MINUTES}min  modo={RUN_MODE}  "
        f"undated={'sí' if ACCEPT_UNDATED else 'no'}  ═══"
    )

    todas: list[dict] = []
    fuentes_usadas    = []

    # ── Google News ───────────────────────────────────────────────
    log.info("► Google News RSS …")
    try:
        r = fetch_google_news(); todas.extend(r)
        if r: fuentes_usadas.append("Google News")
    except Exception as e:
        log.error(f"  Google News: {e}")

    # ── Periódicos ────────────────────────────────────────────────
    if RUN_MODE in ("all", "media_only"):
        log.info("► RSS Periódicos nacionales …")
        try:
            r = fetch_rss_nacionales(); todas.extend(r)
            if r: fuentes_usadas.append("Medios nacionales")
        except Exception as e:
            log.error(f"  Nacionales: {e}")

        log.info("► RSS Periódicos regionales …")
        try:
            r = fetch_rss_regionales(); todas.extend(r)
            if r: fuentes_usadas.append("Medios regionales")
        except Exception as e:
            log.error(f"  Regionales: {e}")

        log.info("► RSS extra …")
        try:
            r = fetch_rss_extra(); todas.extend(r)
            if r: fuentes_usadas.append("Quadratín + regionales")
        except Exception as e:
            log.error(f"  RSS extra: {e}")

    # ── Oficiales ─────────────────────────────────────────────────
    if RUN_MODE in ("all", "official_only"):
        log.info("► CAPUFE directo …")
        try:
            r = fetch_capufe(); todas.extend(r)
            if r: fuentes_usadas.append("CAPUFE")
        except Exception as e:
            log.error(f"  CAPUFE: {e}")

        log.info("► CONAGUA/SMN …")
        try:
            r = fetch_conagua(); todas.extend(r)
            if r: fuentes_usadas.append("CONAGUA/SMN")
        except Exception as e:
            log.error(f"  CONAGUA: {e}")

    # ── Telegram ──────────────────────────────────────────────────
    if RUN_MODE in ("all", "official_only"):
        log.info("► Telegram canales viales …")
        try:
            r = fetch_telegram(); todas.extend(r)
            if r: fuentes_usadas.append("Telegram")
        except Exception as e:
            log.error(f"  Telegram: {e}")

    # ── Dedup + salida ────────────────────────────────────────────
    antes = len(todas)
    todas = dedup(todas)
    log.info(
        f"Total alertas: {antes} brutas → {len(todas)} únicas "
        f"({antes - len(todas)} duplicados eliminados)"
    )
    log.info(f"Stats globales: {_STATS}")

    ahora  = datetime.now(CST)
    grupos = agrupar(todas)

    salida = {
        "ultima_actualizacion":         ahora.isoformat(),
        "ultima_actualizacion_legible": (
            f"{ahora.day} de {MESES[ahora.month-1]} de {ahora.year}"
            f" · {ahora.strftime('%H:%M')} CST"
        ),
        "total":    len(todas),
        "ventana":  f"últimos {LOOKBACK_MINUTES} minutos",
        "fuentes":  fuentes_usadas or ["Google News"],
        "grupos":   grupos,
        "para_mapa": [
            {k: a[k] for k in (
                "id","tipo","ruta","descripcion","fecha",
                "fuente","url","dot_color","badge_txt","lat","lon"
            ) if k in a}
            for a in todas if "lat" in a
        ],
    }

    con_coords = len(salida["para_mapa"])
    log.info(f"  → {con_coords} alertas con coordenadas para el mapa")

    if not todas:
        log.warning("⚠  Sin alertas — escribiendo JSON vacío.")
        salida["nota"] = "Sin alertas activas en la ventana de tiempo configurada."

    with open("alertas.json", "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    log.info(
        f"✅  alertas.json listo · {len(todas)} alertas · "
        f"{con_coords} con coordenadas"
    )


if __name__ == "__main__":
    main()
