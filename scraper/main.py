#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║       SCRAPER ALERTAS VIALES MÉXICO v3.1 — AssistCargo              ║
║  FIX 2026-05-21: parsing robusto de fechas en RSS                   ║
║                  (causa raíz del problema "0 alertas")              ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import json, re, hashlib, logging, time, os
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests, feedparser
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN  (sobreescribible con variables de entorno)
# ─────────────────────────────────────────────────────────────────────
# FIX: ventana subida de 4h → 12h por defecto.
# Razón: muchos feeds (Google News, agregados oficiales) tienen retraso
# de indexación de 2-6h. Con 4h se perdían casi todas las alertas reales.
LOOKBACK_HORAS    = int(os.getenv("SCRAPER_LOOKBACK_MINUTES", str(12*60))) // 60
MAX_POR_FEED      = int(os.getenv("MAX_POR_FEED",   "30"))
RUN_MODE          = os.getenv("SCRAPER_RUN_MODE", "all")
# FIX: default cambiado a True. Sin esto, cualquier feed con formato
# de fecha raro (Quadratín, varios regionales) descartaba TODO.
ACCEPT_UNDATED    = os.getenv("SCRAPER_ACCEPT_UNDATED", "true").lower() == "true"
DEBUG_DATES       = os.getenv("SCRAPER_DEBUG_DATES", "false").lower() == "true"

# ─────────────────────────────────────────────────────────────────────
# GOOGLE NEWS RSS — 28 queries
# ─────────────────────────────────────────────────────────────────────
GNEWS_BASE = (
    "https://news.google.com/rss/search"
    "?q={query}&hl=es-419&gl=MX&ceid=MX%3Aes-419"
)

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
# RSS MEDIOS NACIONALES
# ─────────────────────────────────────────────────────────────────────
RSS_NACIONALES = [
    ("24 Horas",        "https://www.24-horas.mx/feed/"),
    ("El Financiero",   "https://www.elfinanciero.com.mx/rss/nacional"),
    ("Aristegui",       "https://aristeguinoticias.com/feed/"),
    ("Expansión",       "https://expansion.mx/rss"),
    ("Proceso",         "https://www.proceso.com.mx/?feed=rss2"),
    ("Milenio Policial","https://www.milenio.com/rss/policia"),
    ("El Universal MX", "https://www.eluniversal.com.mx/nacion/rss.xml"),
    ("La Silla Rota",   "https://lasillarota.com/feed/"),
    ("SDP Noticias",    "https://www.sdpnoticias.com/feed/"),
]

# ─────────────────────────────────────────────────────────────────────
# RSS MEDIOS REGIONALES
# ─────────────────────────────────────────────────────────────────────
RSS_REGIONALES = [
    ("El Sol de México",     "https://www.elsoldemexico.com.mx/rss.xml"),
    ("El Informador Jalisco","https://www.informador.mx/rss/ultimas-noticias.xml"),
    ("Quadratín Michoacán",  "https://www.quadratin.com.mx/rss"),
    ("El Sol de Sinaloa",    "https://www.elsoldesinaloa.com.mx/rss.xml"),
    ("AM Guanajuato",        "https://www.am.com.mx/rss"),
    ("NTR Guadalajara",      "https://www.ntrguadalajara.com/feed/"),
    ("Milenio Jalisco",      "https://www.milenio.com/rss/estados/jalisco"),
    ("E-consulta Puebla",    "https://e-consulta.com/feed/"),
    ("Jornada Veracruz",     "https://www.jornadaveracruz.com.mx/feed/"),
    ("El Horizonte NL",      "https://www.elhorizonte.mx/feed"),
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

NMAS_RSS = "https://www.nmas.com.mx/feed/"
NMAS_KEYWORDS = ["carretera", "autopista", "bloqueada", "cierre", "bloqueo", "vial"]
MIRADAS_RSS  = "https://miradas.mx/feed"

RSS_VIALES_EXTRA = [
    ("Quadratín Oaxaca",     "https://oaxaca.quadratin.com.mx/feed/"),
    ("Quadratín Veracruz",   "https://veracruz.quadratin.com.mx/feed/"),
    ("Quadratín Puebla",     "https://puebla.quadratin.com.mx/feed/"),
    ("Quadratín Jalisco",    "https://jalisco.quadratin.com.mx/feed/"),
    ("Quadratín Sinaloa",    "https://sinaloa.quadratin.com.mx/feed/"),
    ("El Sol de Puebla",     "https://www.elsoldepuebla.com.mx/rss.xml"),
    ("El Sol de Sinaloa",    "https://www.elsoldesinaloa.com.mx/rss.xml"),
    ("Noticias Veracruz",    "https://www.noticiasveracruz.com.mx/feed/"),
    ("El Norte NL",          "https://www.elnorte.com/rss/portada.xml"),
    ("Quadratín Guerrero",   "https://guerrero.quadratin.com.mx/feed/"),
    ("Quadratín Michoacán",  "https://michoacan.quadratin.com.mx/feed/"),
    ("Quadratín Tamaulipas", "https://tamaulipas.quadratin.com.mx/feed/"),
    ("Debate Sinaloa",       "https://www.debate.com.mx/feed/"),
    ("El Imparcial Oaxaca",  "https://www.imparcialoaxaca.mx/feed/"),
    ("Info7 NL",             "https://www.info7.mx/rss/noticias.xml"),
    ("Multimedios",          "https://www.multimedios.com/feed/"),
]

# ─────────────────────────────────────────────────────────────────────
# CLASIFICACIÓN — sin cambios
# ─────────────────────────────────────────────────────────────────────
KEYWORDS = {
    "cierre_total": [
        "cierre total","cerrado totalmente","sin circulación","volcadura",
        "derrumbe","deslave","inundación total","completamente cerrado",
        "ambos carriles cerrados","cierre completo","totalmente obstruido",
        "hundimiento","colapso","puente cerrado",
    ],
    "bloqueo": [
        "bloqueo","manifestación","manifestantes","protesta","inconformes",
        "comuneros","pobladores","toma de caseta","huelga","paro",
        "encadenados","quema de llantas","retención de personas",
    ],
    "robo": [
        "robo","asalto","asaltantes","delincuentes carretera","banda",
        "robo a transporte","pipas robadas","robo de combustible",
        "motochorros","delincuentes","asalto a mano armada","jalón",
    ],
    "cierre_parcial": [
        "cierre parcial","un carril","reducción de carril","maniobras",
        "percance","accidente","choque","volcadura parcial","neblina",
        "falla mecánica","tractocamión varado","carril cerrado",
        "colisión","impacto vial","auto volcado","camión volcado",
    ],
    "carga_vehicular": [
        "carga vehicular","tránsito lento","lento avance","saturación",
        "congestionamiento","avance lento","tráfico pesado","cola",
        "kilometros de fila","fila de vehículos","retención vial",
    ],
    "obra": [
        "obra vial","trabajos viales","instalación","rehabilitación",
        "mantenimiento vial","bacheo","pavimentación","wim","reparación",
        "señalización vial","ampliación carretera","modernización",
    ],
    "clima": [
        "inundación","lluvia intensa","neblina densa","granizo","tormenta",
        "alerta meteorológica","onda de calor","frente frío","norte",
        "huracán","ciclón","viento fuerte","visibilidad reducida",
        "niebla","helada","nevada","pavimento resbaladizo",
    ],
}

TIPO_CONFIG = {
    "cierre_total":    dict(color="rojo",    icono="🔴", label="CIERRE TOTAL",
                            dot="#e74c3c", badge="badge-cierre-total",
                            badge_txt="CIERRE TOTAL",    col2=False, orden=0),
    "bloqueo":         dict(color="rojo",    icono="⛔", label="BLOQUEOS / MANIFESTACIONES",
                            dot="#8e44ad", badge="badge-bloqueo",
                            badge_txt="BLOQUEO",          col2=False, orden=1),
    "robo":            dict(color="rojo",    icono="🚨", label="ROBOS EN CARRETERA",
                            dot="#c0392b", badge="badge-robo",
                            badge_txt="ROBO",             col2=True,  orden=2),
    "cierre_parcial":  dict(color="amarillo",icono="🟡", label="CIERRE PARCIAL / ACCIDENTES",
                            dot="#e67e22", badge="badge-cierre-parcial",
                            badge_txt="CIERRE PARCIAL",   col2=True,  orden=3),
    "carga_vehicular": dict(color="azul",    icono="🚗", label="CARGA VEHICULAR",
                            dot="#2980b9", badge="badge-carga",
                            badge_txt="CARGA VEHICULAR",  col2=True,  orden=4),
    "obra":            dict(color="verde",   icono="🚧", label="OBRA CONTINUA",
                            dot="#27ae60", badge="badge-obra",
                            badge_txt="OBRA CONTINUA",    col2=False, orden=5),
    "clima":           dict(color="azul",    icono="🌧️", label="ALERTA METEOROLÓGICA",
                            dot="#3498db", badge="badge-clima",
                            badge_txt="ALERTA CLIMA",     col2=False, orden=6),
}

# ─────────────────────────────────────────────────────────────────────
# MAPA DE COORDENADAS — sin cambios
# ─────────────────────────────────────────────────────────────────────
COORD_MAP = {
    "mexico puebla":           (19.35, -98.40),  "150d":            (19.35, -98.40),
    "mexico queretaro":        (20.10, -99.50),  "57d":             (20.10, -99.50),
    "mexico guadalajara":      (20.40,-103.35),  "15d":             (20.40,-103.35),
    "mexico veracruz":         (19.20, -96.80),  "140d":            (19.20, -96.80),
    "mexico acapulco":         (17.55, -99.50),  "95d":             (17.55, -99.50),
    "mexico laredo":           (24.00, -99.00),  "85d":             (24.00, -99.00),
    "mexico tuxpan":           (20.50, -97.90),  "130d":            (20.50, -97.90),
    "tepic guadalajara":       (21.00,-104.00),  "15":              (21.00,-104.00),
    "puebla cordoba":          (18.90, -97.00),  "150":             (18.90, -97.00),
    "siglo xxi":               (19.30,-104.00),  "jiquilpan manzanillo": (19.30,-104.00),
    "cuernavaca acapulco":     (18.20, -99.20),
    "amozoc":                  (19.10, -98.00),
    "tinaja isla":             (18.10, -95.20),
    "arco norte":              (19.80, -99.10),
    "monterrey saltillo":      (25.50,-100.90),  "40d":             (25.50,-100.90),
    "monterrey laredo":        (26.50, -99.50),  "85":              (26.50, -99.50),
    "guadalajara zapotlanejo": (20.65,-103.00),
    "colima manzanillo":       (19.10,-104.30),  "200":             (19.10,-104.30),
    "aguascalientes guadalajara":(21.20,-102.50),"45d":             (21.20,-102.50),
    "irapuato guadalajara":    (20.70,-102.60),
    "leon silao":              (21.12,-101.68),
    "celaya queretaro":        (20.55,-100.60),
    "queretaro san luis":      (21.50,-100.00),  "57":              (21.50,-100.00),
    "san luis tampico":        (22.50, -99.00),  "80":              (22.50, -99.00),
    "tampico ciudad mante":    (22.80, -98.20),
    "torreon saltillo":        (25.20,-102.00),  "40":              (25.20,-102.00),
    "culiacan mazatlan":       (23.70,-106.70),
    "mazatlan durango":        (23.60,-105.90),
    "tijuana ensenada":        (31.70,-116.70),
    "mexicali tijuana":        (32.20,-115.50),
    "navojoa hermosillo":      (28.00,-109.90),
    "chihuahua ciudad juarez": (30.40,-106.40),
    "parral chihuahua":        (27.00,-105.70),
    "durango mazatlan":        (24.50,-106.00),  "40 libre":        (24.50,-106.00),
    "veracruz coatzacoalcos":  (18.20, -94.80),
    "oaxaca istmo":            (16.50, -95.00),
    "cordoba veracruz":        (18.80, -96.90),
    "xalapa veracruz":         (19.35, -96.60),
    "tuxpan tampico":          (21.60, -97.60),
    "villahermosa cardenas":   (18.10, -94.00),
    "merida cancun":           (20.50, -87.90),
    "campeche merida":         (20.30, -90.30),
    "chetumal cancun":         (19.80, -87.40),
    "palenque san cristobal":  (17.00, -92.70),
    "tuxtla gutierrez":        (16.75, -93.12),  "190":             (16.75, -93.12),
    "arriaga tonala":          (15.90, -93.90),
    "carretera federal 2":     (30.00,-108.00),
    "carretera federal 45":    (23.00,-102.50),
    "libre federal":           (22.00,-100.00),
    "jalisco":         (20.66,-103.35), "veracruz":    (19.18, -96.14),
    "oaxaca":          (17.06, -96.72), "guerrero":    (17.55, -99.50),
    "chiapas":         (16.75, -93.12), "puebla":      (19.04, -98.20),
    "hidalgo":         (20.11, -98.73), "michoacan":   (19.70,-101.19),
    "guanajuato":      (21.02,-101.26), "cdmx":        (19.43, -99.13),
    "edomex":          (19.35, -99.70), "tamaulipas":  (24.26, -98.84),
    "nuevo leon":      (25.67,-100.31), "sinaloa":     (24.80,-107.39),
    "sonora":          (29.07,-110.96), "chihuahua":   (28.64,-106.08),
    "baja california": (30.84,-115.28), "baja sur":    (23.70,-110.00),
    "coahuila":        (27.06,-101.71), "durango":     (24.02,-104.66),
    "zacatecas":       (22.77,-102.58), "san luis":    (22.15,-100.97),
    "nayarit":         (21.75,-104.85), "colima":      (19.24,-103.72),
    "tlaxcala":        (19.32, -98.24), "morelos":     (18.67, -99.10),
    "queretaro":       (20.59,-100.39), "aguascalientes":(21.88,-102.29),
    "tabasco":         (17.99, -92.93), "campeche":    (19.83, -90.53),
    "yucatan":         (20.97, -89.62), "quintana roo":(18.50, -88.30),
    "monterrey":       (25.67,-100.31), "guadalajara": (20.66,-103.35),
    "hermosillo":      (29.07,-110.96), "tijuana":     (32.52,-117.00),
    "ciudad juarez":   (31.69,-106.42), "culiacan":    (24.80,-107.39),
    "mazatlan":        (23.24,-106.41), "manzanillo":  (19.05,-104.32),
    "acapulco":        (16.86, -99.88), "cancun":      (21.16, -86.85),
    "merida":          (20.97, -89.62), "cuernavaca":  (18.92, -99.23),
    "toluca":          (19.29, -99.66), "pachuca":     (20.12, -98.73),
    "xalapa":          (19.53, -96.91), "villahermosa":(17.99, -92.93),
    "tuxtla":          (16.75, -93.12),
}

# ─────────────────────────────────────────────────────────────────────
# FALSOS POSITIVOS — sin cambios
# ─────────────────────────────────────────────────────────────────────
FALSOS_POSITIVOS = [
    "reabre","restablece circulación","circulación normal","sin novedad",
    "se normaliza","ya liberaron","retiraron bloqueo","fue detenido",
    "fueron detenidos","capturan","capturaron","detienen banda",
    "simulacro","en memoria","aniversario","recuerdan","conmemoran",
    "historia","hace 10 años","hace un año","archivo","reportaje especial",
    "análisis de","tendencias de","estadísticas de","ranking de",
    "cierre de mercado","cierre bursátil","bolsa de valores",
    "cierre de año","cierre fiscal","cierre de operaciones",
    "cierre de empresa","cierre de negocio","cierre de planta",
    "petróleo","irán","trump","sanciones","aranceles","dólar",
    "inflación","banco","pib","economía","inversión","exporta",
    "importa","comercio exterior","finanzas","presupuesto",
    "pensión afore","crédito","hipoteca","deuda pública",
    "elección","candidato","político","congreso","senado","diputado",
    "hidro sustentable","ahorro de agua","sustentabilidad ambiental",
    "distintivo","certificación","premio","reconocimiento",
    "fútbol","liga mx","deportes","beisbol","basquetbol",
    "concierto","festival","espectáculo","cine","televisión",
    "receta","cocina","gastronomía","restaurante",
    "covid","vacuna","salud","hospital","médico","enfermedad",
    "inmobiliaria","vivienda","departamento","construcción residencial",
]

CST   = timezone(timedelta(hours=-6))
MESES = ["enero","febrero","marzo","abril","mayo","junio",
         "julio","agosto","septiembre","octubre","noviembre","diciembre"]

VIAL_CONTEXTO = [
    "carretera","autopista","capufe","guardia nacional",
    "tractocamión","caseta","peaje","tramo","libramiento",
    "57d","95d","150d","15d","85d","140d","siglo xxi","arco norte",
    "km ","kilómetro","viaducto","periférico","carretera federal",
    "vialidad","circulación","transporte de carga",
    # FIX: agregadas rutas mencionadas frecuentemente sin el prefijo "carretera"
    "méxico-querétaro","méxico-puebla","méxico-cuernavaca","méxico-pachuca",
    "méxico-toluca","méxico-acapulco","méxico-veracruz","méxico-laredo",
    "mexico-queretaro","mexico-puebla","mexico-cuernavaca","mexico-pachuca",
]

VIAL_EVENTO = [
    "cierre","bloqueo","accidente","volcadura","choque","colisión",
    "derrumbe","deslave","inundación","neblina","incendio",
    "robo","asalto","manifestación","manifestantes","protesta",
    "comuneros","huelga","paro","reducción","percance",
    "congestionamiento","retención","obstrucción","daño",
    "falla mecánica","vehículo varado","tráfico lento",
    # FIX: agregadas variantes comunes en titulares
    "cerrada","cerrado","bloqueada","bloqueado","cierran","bloquean",
]

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("alertas")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "es-MX,es;q=0.9",
}

# Contadores globales para diagnóstico
_STATS = {
    "fuera_ventana": 0,
    "no_relevante": 0,
    "falso_positivo": 0,
    "duplicado_en_feed": 0,
    "sin_fecha_parseable": 0,
}

# ─────────────────────────────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────────────────────────────

def get(url: str, timeout: int = 15) -> Optional[requests.Response]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception as e:
        log.debug(f"GET {url}  →  {e}")
        return None


def limpiar(html: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text()).strip()


def make_id(texto: str) -> str:
    return hashlib.md5(texto.encode()).hexdigest()[:8]


def clasificar(texto: str) -> str:
    t = texto.lower()
    for tipo, palabras in KEYWORDS.items():
        if any(p in t for p in palabras):
            return tipo
    return "cierre_parcial"


def es_relevante(texto: str) -> bool:
    t = texto.lower()
    tiene_contexto = any(k in t for k in VIAL_CONTEXTO)
    tiene_evento   = any(k in t for k in VIAL_EVENTO)
    return tiene_contexto and tiene_evento


def es_falso_positivo(texto: str) -> bool:
    t = texto.lower()
    return any(fp in t for fp in FALSOS_POSITIVOS)


def extraer_coords(texto: str) -> Optional[tuple]:
    t = texto.lower()
    km_match = re.search(r"km\s*(\d+)", t)
    for nombre, coords in COORD_MAP.items():
        if nombre in t:
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
        m = re.search(p, texto, re.IGNORECASE)
        if m:
            return m.group(0).strip()[:90]
    return " ".join(texto.split()[:10]) + "…"


def extraer_rec(texto: str) -> str:
    m = re.search(
        r"(?:se recomienda|alternativa[:\s]|usar[:\s]|evitar[:\s]|desvío[:\s])"
        r"[^.!?\n]{10,160}",
        texto, re.IGNORECASE)
    return m.group(0).strip() if m else ""


# ─────────────────────────────────────────────────────────────────────
# FIX PRINCIPAL — parsing robusto de fechas
# ─────────────────────────────────────────────────────────────────────

def _entry_fecha(entry) -> Optional[datetime]:
    """
    FIX: Extrae datetime de un entry de feedparser usando MÚLTIPLES estrategias.

    El bug original: el código solo leía entry.get('published') como string,
    luego intentaba parsedate_to_datetime(). Si el feed usa 'updated' en vez
    de 'published' (común en Atom/Google News) o un formato no estándar,
    fallaba silenciosamente y descartaba la entrada.

    Esta función intenta en orden:
      1. published_parsed (struct_time pre-parseado por feedparser)
      2. updated_parsed   (struct_time pre-parseado)
      3. published string + parsedate_to_datetime
      4. updated string + parsedate_to_datetime
      5. dc:date u otros formatos ISO
    """
    # Estrategia 1 y 2: feedparser ya parsea estos a struct_time
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        st = entry.get(key)
        if st:
            try:
                # struct_time de feedparser está en UTC
                dt_utc = datetime(*st[:6], tzinfo=timezone.utc)
                return dt_utc.astimezone(CST)
            except Exception:
                pass

    # Estrategia 3 y 4: parsear el string crudo
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
        # Fallback ISO 8601
        try:
            iso_s = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso_s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(CST)
        except Exception:
            pass

    return None


def esta_en_ventana_entry(entry) -> tuple[bool, Optional[datetime]]:
    """FIX: nueva versión que recibe el entry completo (no solo el string)."""
    dt = _entry_fecha(entry)
    if dt is None:
        _STATS["sin_fecha_parseable"] += 1
        if DEBUG_DATES:
            log.info(f"    [date-debug] sin fecha parseable. Keys: {list(entry.keys())[:8]}")
        return (ACCEPT_UNDATED, None)
    delta = datetime.now(CST) - dt
    en_ventana = 0 <= delta.total_seconds() <= LOOKBACK_HORAS * 3600
    if DEBUG_DATES and not en_ventana:
        log.info(f"    [date-debug] fuera de ventana: dt={dt.isoformat()} "
                 f"delta={delta.total_seconds()/3600:.1f}h")
    return (en_ventana, dt)


def fmt_fecha_dt(dt: Optional[datetime]) -> str:
    if dt is None:
        return _ahora_str()
    return f"{dt.day} {MESES[dt.month-1]} {dt.year} · {dt.strftime('%H:%M')} CST"


def fmt_fecha(rss_date: str = "") -> str:
    """Mantiene compatibilidad con scraping HTML que no tiene entry."""
    if not rss_date:
        return _ahora_str()
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(rss_date)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(CST)
        return f"{dt.day} {MESES[dt.month-1]} {dt.year} · {dt.strftime('%H:%M')} CST"
    except Exception:
        return rss_date[:16]


def _ahora_str() -> str:
    n = datetime.now(CST)
    return f"{n.day} {MESES[n.month-1]} {n.year} · {n.strftime('%H:%M')} CST"


def hacer_alerta(tipo, ruta, desc, rec, fecha, fuente, url,
                 extra_texto="") -> dict:
    c = TIPO_CONFIG[tipo]
    coords = extraer_coords(desc + " " + ruta + " " + extra_texto)
    dt_alerta = _parse_fecha_alerta(fecha)
    fecha_iso = dt_alerta.isoformat() if dt_alerta.year > 1970 else None
    a = {
        "id":            make_id(desc),
        "tipo":          tipo,
        "ruta":          ruta,
        "descripcion":   desc[:500],
        "recomendacion": rec,
        "fecha":         fecha,
        "fecha_iso":     fecha_iso,
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
        rechazadas = {"ventana": 0, "relevancia": 0, "falso_pos": 0, "dup": 0}

        for entry in feed.entries[:MAX_POR_FEED]:
            # FIX: usar la nueva función que recibe el entry completo
            en_ventana, dt = esta_en_ventana_entry(entry)
            if not en_ventana:
                rechazadas["ventana"] += 1
                continue

            titulo  = limpiar(entry.get("title", ""))
            resumen = limpiar(entry.get("summary", ""))
            texto   = f"{titulo}. {resumen}"

            if not es_relevante(texto):
                rechazadas["relevancia"] += 1
                continue
            if es_falso_positivo(texto):
                rechazadas["falso_pos"] += 1
                continue

            key = make_id(titulo[:60].lower())
            if key in vistos:
                rechazadas["dup"] += 1
                continue
            vistos.add(key)

            tipo   = clasificar(texto)
            ruta   = extraer_ruta(titulo)
            fecha  = fmt_fecha_dt(dt)
            fuente = entry.get("source", {}).get("title", "Google News")
            link   = entry.get("link", "")

            alertas.append(hacer_alerta(tipo, ruta, texto[:500],
                                        extraer_rec(texto), fecha, fuente, link, texto))
            nuevas += 1

        log.info(f"  GNews '{query[:38]}': {len(feed.entries)} entradas → "
                 f"{nuevas} alertas  [vent:{rechazadas['ventana']} "
                 f"rel:{rechazadas['relevancia']} fp:{rechazadas['falso_pos']} "
                 f"dup:{rechazadas['dup']}]")
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
            log.warning(f"  RSS {nombre}: sin respuesta")
            continue

        feed = feedparser.parse(resp.text)
        nuevas = 0
        rechazadas = {"ventana": 0, "relevancia": 0, "falso_pos": 0, "dup": 0}

        for entry in feed.entries[:MAX_POR_FEED]:
            en_ventana, dt = esta_en_ventana_entry(entry)
            if not en_ventana:
                rechazadas["ventana"] += 1
                continue

            titulo  = limpiar(entry.get("title", ""))
            resumen = limpiar(entry.get("summary", ""))
            texto   = f"{titulo}. {resumen}"

            if not es_relevante(texto):
                rechazadas["relevancia"] += 1
                continue
            if es_falso_positivo(texto):
                rechazadas["falso_pos"] += 1
                continue

            key = make_id(titulo[:60].lower())
            if key in vistos:
                rechazadas["dup"] += 1
                continue
            vistos.add(key)

            alertas.append(hacer_alerta(
                clasificar(texto),
                extraer_ruta(titulo),
                texto[:500],
                extraer_rec(texto),
                fmt_fecha_dt(dt),
                nombre,
                entry.get("link", rss_url),
                texto,
            ))
            nuevas += 1

        log.info(f"  RSS {nombre}: {len(feed.entries)} entradas → "
                 f"{nuevas} alertas  [vent:{rechazadas['ventana']} "
                 f"rel:{rechazadas['relevancia']} fp:{rechazadas['falso_pos']} "
                 f"dup:{rechazadas['dup']}]")

    return alertas


def fetch_rss_nacionales() -> list[dict]:
    alertas = _procesar_rss(RSS_NACIONALES)
    log.info(f"  ✓ Periódicos nacionales: {len(alertas)} alertas")
    return alertas


def fetch_rss_regionales() -> list[dict]:
    alertas = _procesar_rss(RSS_REGIONALES)
    log.info(f"  ✓ Periódicos regionales: {len(alertas)} alertas")
    return alertas


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
                    tipo    = clasificar(texto)
                    alertas.append(hacer_alerta(
                        tipo, extraer_ruta(titulo), texto[:500],
                        extraer_rec(texto), fmt_fecha_dt(dt),
                        "CAPUFE", entry.get("link", url), texto,
                    ))
                return alertas

        soup = BeautifulSoup(resp.text, "html.parser")
        filas = soup.select("tr, .reporte, .alerta-row, .vialidad-item, article")
        for fila in filas[:40]:
            texto = fila.get_text(separator=" ", strip=True)
            if len(texto) < 30 or not es_relevante(texto):
                continue
            if es_falso_positivo(texto):
                continue
            tipo = clasificar(texto)
            alertas.append(hacer_alerta(
                tipo, extraer_ruta(texto), texto[:500],
                extraer_rec(texto), _ahora_str(),
                "CAPUFE", url, texto,
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
                    "Maneja con precaución.",
                    fmt_fecha_dt(dt), "CONAGUA/SMN",
                    entry.get("link", url), texto,
                ))
            return alertas

    log.info(f"  ✓ CONAGUA: {len(alertas)} avisos")
    return alertas


# ─────────────────────────────────────────────────────────────────────
# FUENTE 5 — N+ / MIRADAS
# ─────────────────────────────────────────────────────────────────────

def _extraer_items_articulo(soup: BeautifulSoup, fuente: str, url_base: str) -> list[dict]:
    alertas = []
    patron_hora = re.compile(r"\d{1,2}:\d{2}\s*[Hh]oras?\.?", re.IGNORECASE)
    patron_km   = re.compile(r"km\s*\d+", re.IGNORECASE)

    bloques = soup.find_all(["p", "li"], string=True)
    for bloque in bloques:
        texto = bloque.get_text(separator=" ", strip=True)
        if len(texto) < 30:
            continue
        if not (patron_hora.search(texto) or patron_km.search(texto)):
            continue
        if not es_relevante(texto):
            continue
        if es_falso_positivo(texto):
            continue
        tipo = clasificar(texto)
        alertas.append(hacer_alerta(
            tipo, extraer_ruta(texto), texto[:500],
            extraer_rec(texto), _ahora_str(),
            fuente, url_base, texto,
        ))
    return alertas


def fetch_sitios_viales() -> list[dict]:
    alertas = []

    resp = get(NMAS_RSS, timeout=15)
    if resp:
        feed = feedparser.parse(resp.text)
        for entry in feed.entries[:20]:
            titulo = limpiar(entry.get("title", "")).lower()
            if not any(kw in titulo for kw in NMAS_KEYWORDS):
                continue
            # FIX: usar nueva función de fecha
            en_ventana, dt = esta_en_ventana_entry(entry)
            if not en_ventana:
                # Ventana extendida a 26h para listados diarios
                if dt is None:
                    continue
                if (datetime.now(CST) - dt).total_seconds() > 26 * 3600:
                    continue

            link = entry.get("link", "")
            if not link:
                continue
            page = get(link, timeout=20)
            if not page:
                continue
            soup = BeautifulSoup(page.text, "html.parser")
            nuevas = _extraer_items_articulo(soup, "N+/CAPUFE-GN", link)
            alertas.extend(nuevas)
            log.info(f"  N+ '{entry.get('title','')[:50]}': {len(nuevas)} alertas")
            time.sleep(1)

    resp2 = get(MIRADAS_RSS, timeout=15)
    if resp2:
        feed2 = feedparser.parse(resp2.text)
        for entry in feed2.entries[:10]:
            titulo = limpiar(entry.get("title", "")).lower()
            if not any(kw in titulo for kw in ["carretera", "bloqueo", "cierre", "autopista"]):
                continue
            en_ventana, dt = esta_en_ventana_entry(entry)
            if dt and (datetime.now(CST) - dt).total_seconds() > 26 * 3600:
                continue
            link = entry.get("link", "")
            if not link:
                continue
            page = get(link, timeout=20)
            if not page:
                continue
            soup = BeautifulSoup(page.text, "html.parser")
            nuevas = _extraer_items_articulo(soup, "Miradas.mx/GN", link)
            alertas.extend(nuevas)
            log.info(f"  Miradas.mx '{entry.get('title','')[:50]}': {len(nuevas)} alertas")
            time.sleep(1)

    log.info(f"  ✓ Sitios especializados: {len(alertas)} alertas")
    return alertas


# ─────────────────────────────────────────────────────────────────────
# FUENTE 6 — RSS EXTRA
# ─────────────────────────────────────────────────────────────────────

def fetch_rss_extra() -> list[dict]:
    alertas = _procesar_rss(RSS_VIALES_EXTRA)
    log.info(f"  ✓ RSS extra (Quadratín + regionales): {len(alertas)} alertas")
    return alertas


# ─────────────────────────────────────────────────────────────────────
# DEDUP
# ─────────────────────────────────────────────────────────────────────

def _tokens(texto: str) -> set:
    return set(re.findall(r"\b[a-záéíóúñ]{4,}\b", texto.lower()))


def _parse_fecha_alerta(fecha_str: str) -> datetime:
    try:
        m = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})\s*·\s*(\d{2}:\d{2})", fecha_str)
        if m:
            dia, mes_str, anio, hora = m.groups()
            mes = MESES.index(mes_str.lower()) + 1
            h, mi = map(int, hora.split(":"))
            return datetime(int(anio), mes, int(dia), h, mi, tzinfo=CST)
    except Exception:
        pass
    return datetime(1970, 1, 1, tzinfo=CST)


def dedup(alertas: list[dict]) -> list[dict]:
    alertas_sorted = sorted(alertas, key=lambda a: _parse_fecha_alerta(a.get("fecha", "")), reverse=True)

    seen_ids, seen_tokens, out = set(), [], []
    for a in alertas_sorted:
        uid = make_id(a["descripcion"][:80].lower())
        if uid in seen_ids:
            continue
        tok = _tokens(a["descripcion"][:120])
        duplicado = False
        for st in seen_tokens:
            if len(tok) > 3 and len(tok & st) / max(len(tok), len(st), 1) >= 0.70:
                duplicado = True
                break
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
        alertas_grupo = sorted(
            grupos[tipo],
            key=lambda a: _parse_fecha_alerta(a.get("fecha", "")),
            reverse=True
        )
        resultado.append({
            "tipo":    tipo,
            "color":   cfg["color"],
            "icono":   cfg["icono"],
            "label":   cfg["label"],
            "col2":    cfg["col2"],
            "alertas": alertas_grupo,
        })
    return resultado


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

def main():
    log.info(f"═══  Scraper Alertas Viales México v3.1  ══  "
             f"ventana={LOOKBACK_HORAS}h  modo={RUN_MODE}  "
             f"undated={'sí' if ACCEPT_UNDATED else 'no'}  ═══")
    todas: list[dict] = []
    fuentes_usadas = []

    log.info("► Google News RSS …")
    try:
        r = fetch_google_news(); todas.extend(r)
        if r: fuentes_usadas.append("Google News")
    except Exception as e:
        log.error(f"  Google News: {e}")

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

    if RUN_MODE in ("all", "official_only"):
        log.info("► Scraping N+ / Miradas …")
        try:
            r = fetch_sitios_viales(); todas.extend(r)
            if r: fuentes_usadas.append("N+/Miradas")
        except Exception as e:
            log.error(f"  Sitios viales: {e}")

    if RUN_MODE in ("all", "media_only"):
        log.info("► RSS extra …")
        try:
            r = fetch_rss_extra(); todas.extend(r)
            if r: fuentes_usadas.append("Quadratín + regionales")
        except Exception as e:
            log.error(f"  RSS extra: {e}")

    antes = len(todas)
    todas = dedup(todas)
    log.info(f"Total alertas: {antes} brutas → {len(todas)} únicas "
             f"({antes - len(todas)} duplicados)")
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
        "ventana":  f"últimas {LOOKBACK_HORAS} horas",
        "fuentes":  fuentes_usadas or ["Google News"],
        "grupos":   grupos,
        "para_mapa": [
            {k: a[k] for k in ("id","tipo","ruta","descripcion","fecha",
                                "fuente","url","dot_color","badge_txt","lat","lon")
             if k in a}
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
        return

    with open("alertas.json", "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    log.info(f"✅  alertas.json listo · {len(todas)} alertas · "
             f"{con_coords} con coordenadas")


if __name__ == "__main__":
    main()
