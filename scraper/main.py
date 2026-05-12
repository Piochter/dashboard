#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║       SCRAPER ALERTAS VIALES MÉXICO v2 — AssistCargo                ║
║  Fuentes:                                                           ║
║    • Google News RSS   — 28 búsquedas específicas (sin API key)     ║
║    • RSS nacionales    — 14 medios (Milenio, Universal, Reforma…)   ║
║    • RSS regionales    — 10 medios estatales                        ║
║    • CAPUFE directo    — scraping página oficial                    ║
║    • CONAGUA/SMN       — XML oficial                                ║
║    • Nitter/X          — RSS de @CAPUFE_Oficial y @GN_Carreteras    ║
║  Ventana: últimas LOOKBACK_HORAS horas (default 24, configurable)   ║
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
LOOKBACK_HORAS    = int(os.getenv("SCRAPER_LOOKBACK_MINUTES", str(24*60))) // 60  # ventana de tiempo
MAX_POR_FEED      = int(os.getenv("MAX_POR_FEED",   "30"))   # entradas a leer por feed
RUN_MODE          = os.getenv("SCRAPER_RUN_MODE", "all")             # all | official_only | media_only
ACCEPT_UNDATED    = os.getenv("SCRAPER_ACCEPT_UNDATED", "false").lower() == "true"  # rechazar sin fecha

# ─────────────────────────────────────────────────────────────────────
# GOOGLE NEWS RSS — 28 queries segmentadas por tipo y región
# ─────────────────────────────────────────────────────────────────────
GNEWS_BASE = (
    "https://news.google.com/rss/search"
    "?q={query}&hl=es-419&gl=MX&ceid=MX%3Aes-419"
)

GNEWS_QUERIES = [
    # ── Cierres generales ──────────────────────────────────────────
    "cierre carretero México autopista hoy",
    "cierre vial autopista federal México",
    "carretera cerrada México accidente hoy",
    "CAPUFE cierre vial alerta",
    "GN_Carreteras cierre bloqueo México",
    "Guardia Nacional carreteras cierre México",
    "SCT SICT cierre carretera federal",
    # ── Por tipo de incidente ──────────────────────────────────────
    "volcadura tractocamión autopista México",
    "accidente carretera México tráiler camión",
    "derrumbe deslave carretera México",
    "inundación carretera autopista México",
    "neblina cierre autopista México",
    "incendio vehículo autopista México",
    # ── Bloqueos y manifestaciones ─────────────────────────────────
    "bloqueo manifestación carretera federal México",
    "manifestantes toma caseta autopista México",
    "comuneros bloqueo carretera México",
    "huelga paro carretera México bloqueo",
    # ── Robos ──────────────────────────────────────────────────────
    "robo carretera autopista México asalto",
    "robo transporte de carga autopista México",
    "asalto tractocamión carretera México",
    "robo combustible pipa autopista México",
    # ── Por autopista específica ────────────────────────────────────
    "autopista México Querétaro 57D cierre accidente",
    "autopista México Puebla 150D cierre volcadura",
    "autopista México Acapulco 95D cierre bloqueo",
    "autopista Siglo XXI Manzanillo cierre accidente",
    "autopista México Veracruz 140D cierre",
    "autopista México Laredo 85D cierre accidente",
    # ── Regiones críticas ──────────────────────────────────────────
    "cierre carretera Oaxaca Guerrero Chiapas",
    "bloqueo carretera Tamaulipas Nuevo León",
]

# ─────────────────────────────────────────────────────────────────────
# RSS MEDIOS NACIONALES
# ─────────────────────────────────────────────────────────────────────
RSS_NACIONALES = [
    ("Milenio",         "https://www.milenio.com/rss"),
    ("El Universal",    "https://www.eluniversal.com.mx/rss.xml"),
    ("Excélsior",       "https://www.excelsior.com.mx/rss.xml"),
    ("Infobae MX",      "https://www.infobae.com/feeds/rss/"),
    ("El Heraldo",      "https://heraldodemexico.com.mx/feed/"),
    ("La Silla Rota",   "https://lasillarota.com/feed"),
    ("SDP Noticias",    "https://www.sdpnoticias.com/rss"),
    ("24 Horas",        "https://www.24-horas.mx/feed/"),
    ("Aristegui",       "https://aristeguinoticias.com/feed/"),
    ("El Financiero",   "https://www.elfinanciero.com.mx/rss"),
    ("Proceso",         "https://www.proceso.com.mx/?feed=rss2"),
    ("Animal Político", "https://www.animalpolitico.com/feed"),
    ("Expansión",       "https://expansion.mx/rss"),
    ("Forbes MX",       "https://www.forbes.com.mx/feed/"),
]

# ─────────────────────────────────────────────────────────────────────
# RSS MEDIOS REGIONALES (cobertura por estado)
# ─────────────────────────────────────────────────────────────────────
RSS_REGIONALES = [
    ("El Sol de México",     "https://www.elsoldemexico.com.mx/rss.xml"),
    ("NTR Guadalajara",      "https://ntrgdl.com/feed/"),
    ("El Informador Jalisco","https://www.informador.mx/rss/ultimas-noticias.xml"),
    ("Milenio Jalisco",      "https://www.milenio.com/rss/jalisco"),
    ("La Jornada Veracruz",  "https://www.jornadaveracruz.com.mx/feed/"),
    ("E-consulta Puebla",    "https://e-consulta.com/feed/"),
    ("Quadratín Michoacán",  "https://www.quadratin.com.mx/rss"),
    ("El Sol de Sinaloa",    "https://www.elsoldesinaloa.com.mx/rss.xml"),
    ("El Horizonte NL",      "https://www.elhorizonte.mx/rss"),
    ("AM Guanajuato",        "https://www.am.com.mx/rss"),
]

# ─────────────────────────────────────────────────────────────────────
# CONAGUA / SMN
# ─────────────────────────────────────────────────────────────────────
CONAGUA_URLS = [
    "https://smn.conagua.gob.mx/tools/RESOURCES/Avisos/AvisoMeteorologico.xml",
    "https://smn.conagua.gob.mx/tools/RESOURCES/avisos/avisos.xml",
]

# ─────────────────────────────────────────────────────────────────────
# CAPUFE directo
# ─────────────────────────────────────────────────────────────────────
CAPUFE_URLS = [
    "https://www.capufe.gob.mx/site/xml/ReporteVialidad.xml",
    "https://www.capufe.gob.mx/site/webSCT/comunicados.xml",
    "https://www.capufe.gob.mx/norteMonitor/",   # HTML fallback
]

# ─────────────────────────────────────────────────────────────────────
# NITTER — Más instancias y más cuentas
# ─────────────────────────────────────────────────────────────────────
NITTER_INSTANCES = [
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://xcancel.com",
    "https://nitter.cz",
    "https://nitter.1d4.us",
    "https://nitter.nicfab.eu",
    "https://nitter.rawbit.ninja",
    "https://nitter.mint.lgbt",
]

CUENTAS_X = [
    # ── Federales y carreteras ────────────────────────────────────
    "CAPUFE_Oficial",       # CAPUFE oficial
    "GN_Carreteras",        # Guardia Nacional Carreteras
    "SICT_mx",              # Sec. Infraestructura y Transporte
    "088_GN",               # GN emergencias
    "SEMAR_mx",             # Secretaría de Marina
    "conagua_clima",        # CONAGUA clima/inundaciones
    # ── Vialidad CDMX y área metropolitana ───────────────────────
    "C5_CDMX",              # C5 Ciudad de México
    "OVIALCDMX",            # Observatorio Vial CDMX
    "SSC_CDMX",             # Secretaría de Seguridad CDMX
    "LaDeTrafico",          # Tráfico en tiempo real CDMX/ZMV
    "CAE_AAM",              # Centro de Atención a Emergencias CDMX
    "Circuito_mx",          # Circuito Interior / vialidades CDMX
    # ── Estado de México ─────────────────────────────────────────
    "C5Edomex",             # C5 Estado de México
    "Vialidad_EDOMEX",      # Vialidad EdoMex
    # ── Regionales ───────────────────────────────────────────────
    "PolVial_GobOax",       # Policía Vial Oaxaca
    "PC_Oaxaca",            # Protección Civil Oaxaca
    "RED_Michoacan",        # Red de emergencias Michoacán
    "SICT_Michoacan",       # SICT Michoacán
    "SSP_Jalisco",          # SSP Jalisco
    "C4iSinaloa",           # C4i Sinaloa
    "SICT_BC",              # SICT Baja California
    "nSaltillo",            # Noticias Saltillo / Coahuila
    "RedVialRC",            # Red Vial Región Centro
    # ── Rastreadores / medios especializados ─────────────────────
    "SatelTrack",           # Seguimiento satelital de transporte
    "LaHoraMX",             # La Hora MX — noticias viales
    "InformaOriente",       # Informa Oriente (Puebla/Veracruz)
]

# ─────────────────────────────────────────────────────────────────────
# CLASIFICACIÓN
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
# MAPA DE COORDENADAS — 70+ autopistas, carreteras y estados
# ─────────────────────────────────────────────────────────────────────
COORD_MAP = {
    # ── Autopistas federales de cuota ────────────────────────────
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
    # ── Carreteras federales libres ───────────────────────────────
    "carretera federal 2":     (30.00,-108.00),
    "carretera federal 45":    (23.00,-102.50),
    "libre federal":           (22.00,-100.00),
    # ── Estados y zonas ───────────────────────────────────────────
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
# FALSOS POSITIVOS — noticias que no son alertas activas
# ─────────────────────────────────────────────────────────────────────
FALSOS_POSITIVOS = [
    "reabre","restablece circulación","circulación normal","sin novedad",
    "se normaliza","ya liberaron","retiraron bloqueo","fue detenido",
    "fueron detenidos","capturan","capturaron","detienen banda",
    "simulacro","en memoria","aniversario","recuerdan","conmemoran",
    "historia","hace 10 años","hace un año","archivo","reportaje especial",
    "análisis de","tendencias de","estadísticas de","ranking de",
]

CST   = timezone(timedelta(hours=-6))
MESES = ["enero","febrero","marzo","abril","mayo","junio",
         "julio","agosto","septiembre","octubre","noviembre","diciembre"]

VIAL_KW = [
    "carretera","autopista","km ","cierre","bloqueo","manifestación",
    "accidente","volcadura","derrumbe","inundación","neblina",
    "robo","asalto","capufe","gn_carreteras","guardia nacional",
    "transporte de carga","tractocamión","manifestantes","protesta",
    "obra vial","tránsito","vialidad","carril","caseta","peaje",
    "57d","95d","150d","15d","85d","140d","siglo xxi","arco norte",
    "percance","colisión","impacto","volcó","chocó","choque","fila",
    "retención","congestionamiento","tráfico pesado","lento avance",
]

# ─────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("alertas")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "es-MX,es;q=0.9",
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
    return any(k in t for k in VIAL_KW)


def es_falso_positivo(texto: str) -> bool:
    t = texto.lower()
    return any(fp in t for fp in FALSOS_POSITIVOS)


def extraer_coords(texto: str) -> Optional[tuple]:
    t = texto.lower()
    # Búsqueda de km específico + carretera
    km_match = re.search(r"km\s*(\d+)", t)
    for nombre, coords in COORD_MAP.items():
        if nombre in t:
            if km_match:
                # Desplazar coords ligeramente para diferenciar km distintos
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
    # Fallback: primeras palabras significativas
    return " ".join(texto.split()[:10]) + "…"


def extraer_rec(texto: str) -> str:
    m = re.search(
        r"(?:se recomienda|alternativa[:\s]|usar[:\s]|evitar[:\s]|desvío[:\s])"
        r"[^.!?\n]{10,160}",
        texto, re.IGNORECASE)
    return m.group(0).strip() if m else ""


def fmt_fecha(rss_date: str = "") -> str:
    if not rss_date:
        return _ahora_str()
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(rss_date).astimezone(CST)
        return f"{dt.day} {MESES[dt.month-1]} {dt.year} · {dt.strftime('%H:%M')} CST"
    except Exception:
        return rss_date[:16]


def parse_fecha_rss(rss_date: str) -> Optional[datetime]:
    if not rss_date:
        return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(rss_date).astimezone(CST)
    except Exception:
        return None


def esta_en_ventana(rss_date: str) -> bool:
    """True si el artículo fue publicado dentro de las últimas LOOKBACK_HORAS horas.
    Si no se puede parsear la fecha se rechaza (evita alertas viejas sin fecha)
    salvo que ACCEPT_UNDATED=true esté configurado explícitamente."""
    dt = parse_fecha_rss(rss_date)
    if dt is None:
        return ACCEPT_UNDATED   # por defecto rechazar sin fecha
    delta = datetime.now(CST) - dt
    return delta.total_seconds() <= LOOKBACK_HORAS * 3600


def _ahora_str() -> str:
    n = datetime.now(CST)
    return f"{n.day} {MESES[n.month-1]} {n.year} · {n.strftime('%H:%M')} CST"


def hacer_alerta(tipo, ruta, desc, rec, fecha, fuente, url,
                 extra_texto="") -> dict:
    c = TIPO_CONFIG[tipo]
    coords = extraer_coords(desc + " " + ruta + " " + extra_texto)
    # Calcular fecha_iso para que el frontend pueda filtrar/ordenar fácilmente
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

        for entry in feed.entries[:MAX_POR_FEED]:
            pub_date = entry.get("published", "")
            if not esta_en_ventana(pub_date):
                continue

            titulo  = limpiar(entry.get("title", ""))
            resumen = limpiar(entry.get("summary", ""))
            texto   = f"{titulo}. {resumen}"

            if not es_relevante(texto) or es_falso_positivo(texto):
                continue

            key = make_id(titulo[:60].lower())
            if key in vistos:
                continue
            vistos.add(key)

            tipo   = clasificar(texto)
            ruta   = extraer_ruta(titulo)          # ruta desde el titular (más limpio)
            fecha  = fmt_fecha(pub_date)
            fuente = entry.get("source", {}).get("title", "Google News")
            link   = entry.get("link", "")

            alertas.append(hacer_alerta(tipo, ruta, texto[:500],
                                        extraer_rec(texto), fecha, fuente, link, texto))
            nuevas += 1

        log.info(f"  GNews '{query[:38]}': {len(feed.entries)} entradas → {nuevas} alertas")
        time.sleep(0.4)   # cortesía con Google

    log.info(f"  ✓ Google News total: {len(alertas)} alertas")
    return alertas


# ─────────────────────────────────────────────────────────────────────
# FUENTE 2 — RSS PERIÓDICOS (nacionales + regionales)
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

        for entry in feed.entries[:MAX_POR_FEED]:
            pub_date = entry.get("published", "")
            if not esta_en_ventana(pub_date):
                continue

            titulo  = limpiar(entry.get("title", ""))
            resumen = limpiar(entry.get("summary", ""))
            texto   = f"{titulo}. {resumen}"

            if not es_relevante(texto) or es_falso_positivo(texto):
                continue

            key = make_id(titulo[:60].lower())
            if key in vistos:
                continue
            vistos.add(key)

            alertas.append(hacer_alerta(
                clasificar(texto),
                extraer_ruta(titulo),
                texto[:500],
                extraer_rec(texto),
                fmt_fecha(pub_date),
                nombre,
                entry.get("link", rss_url),
                texto,
            ))
            nuevas += 1

        log.info(f"  RSS {nombre}: {len(feed.entries)} entradas → {nuevas} alertas")

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
# FUENTE 3 — CAPUFE DIRECTO
# ─────────────────────────────────────────────────────────────────────

def fetch_capufe() -> list[dict]:
    alertas = []

    for url in CAPUFE_URLS:
        resp = get(url, timeout=15)
        if not resp:
            continue

        ct = resp.headers.get("content-type", "")

        # ── XML / RSS ──────────────────────────────────────────────
        if "xml" in ct or url.endswith(".xml"):
            feed = feedparser.parse(resp.text)
            if feed.entries:
                log.info(f"  CAPUFE XML: {len(feed.entries)} registros")
                for entry in feed.entries[:30]:
                    pub_date = entry.get("published", "")
                    if not esta_en_ventana(pub_date):
                        continue
                    titulo  = limpiar(entry.get("title", "Alerta CAPUFE"))
                    resumen = limpiar(entry.get("summary", titulo))
                    texto   = f"{titulo}. {resumen}"
                    tipo    = clasificar(texto)
                    alertas.append(hacer_alerta(
                        tipo, extraer_ruta(titulo), texto[:500],
                        extraer_rec(texto), fmt_fecha(pub_date),
                        "CAPUFE", entry.get("link", url), texto,
                    ))
                return alertas

        # ── HTML ───────────────────────────────────────────────────
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
# FUENTE 4 — CONAGUA / SMN
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
                pub_date = entry.get("published", "")
                if not esta_en_ventana(pub_date):
                    continue
                titulo  = entry.get("title", "Aviso meteorológico")
                resumen = limpiar(entry.get("summary", titulo))
                texto   = f"{titulo}. {resumen}"
                alertas.append(hacer_alerta(
                    "clima", titulo[:90], texto[:500],
                    "Maneja con precaución.",
                    fmt_fecha(pub_date), "CONAGUA/SMN",
                    entry.get("link", url), texto,
                ))
            return alertas

    log.info(f"  ✓ CONAGUA: {len(alertas)} avisos")
    return alertas


# ─────────────────────────────────────────────────────────────────────
# FUENTE 5 — NITTER / X RSS
# ─────────────────────────────────────────────────────────────────────

def fetch_nitter() -> list[dict]:
    alertas = []
    ok_instances: dict[str, str] = {}   # cuenta → instancia que funciona

    for cuenta in CUENTAS_X:
        for base in NITTER_INSTANCES:
            url  = f"{base}/{cuenta}/rss"
            resp = get(url, timeout=10)
            if not resp:
                continue
            ct = resp.headers.get("content-type", "")
            if "xml" not in ct and "rss" not in ct:
                continue
            feed = feedparser.parse(resp.text)
            if not feed.entries:
                continue

            ok_instances[cuenta] = base
            log.info(f"  Nitter @{cuenta} via {base}: {len(feed.entries)} tweets")

            for entry in feed.entries[:30]:
                pub_date = entry.get("published", "")
                if not esta_en_ventana(pub_date):
                    continue
                texto = limpiar(entry.get("summary", entry.get("title", "")))
                if len(texto) < 20 or not es_relevante(texto):
                    continue
                if es_falso_positivo(texto):
                    continue
                tipo  = clasificar(texto)
                fecha = fmt_fecha(pub_date)
                alertas.append(hacer_alerta(
                    tipo, extraer_ruta(texto), texto[:500],
                    extraer_rec(texto), fecha,
                    f"@{cuenta}", entry.get("link", ""), texto,
                ))
            break   # un nitter ok basta para esta cuenta

    log.info(f"  ✓ Nitter/X (cuentas: {list(ok_instances.keys())}): {len(alertas)} tweets")
    return alertas


# ─────────────────────────────────────────────────────────────────────
# DEDUP  (por similitud de texto, no solo hash exacto)
# ─────────────────────────────────────────────────────────────────────

def _tokens(texto: str) -> set:
    return set(re.findall(r"\b[a-záéíóúñ]{4,}\b", texto.lower()))


def _parse_fecha_alerta(fecha_str: str) -> datetime:
    """Parsea la fecha legible de la alerta para ordenar. Devuelve epoch si falla."""
    try:
        # Formato: "12 mayo 2026 · 14:30 CST"
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
    # Ordenar por fecha más reciente primero para que el dedup conserve las nuevas
    alertas_sorted = sorted(alertas, key=lambda a: _parse_fecha_alerta(a.get("fecha", "")), reverse=True)

    seen_ids, seen_tokens, out = set(), [], []
    for a in alertas_sorted:
        uid = make_id(a["descripcion"][:80].lower())
        if uid in seen_ids:
            continue
        tok = _tokens(a["descripcion"][:120])
        # Rechazar si 70%+ de tokens coinciden (más estricto que antes)
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
        # Ordenar alertas dentro del grupo por fecha más reciente primero
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
    log.info(f"═══  Scraper Alertas Viales México v2  ══  "
             f"ventana={LOOKBACK_HORAS}h  modo={RUN_MODE}  ═══")
    todas: list[dict] = []

    fuentes_usadas = []

    # ── Google News (siempre) ─────────────────────────────────────
    log.info("► Google News RSS …")
    try:
        r = fetch_google_news(); todas.extend(r)
        if r: fuentes_usadas.append("Google News")
    except Exception as e:
        log.error(f"  Google News: {e}")

    # ── Periódicos nacionales ─────────────────────────────────────
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

    # ── CAPUFE ─────────────────────────────────────────────────────
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

    # ── Nitter/X ───────────────────────────────────────────────────
    if RUN_MODE in ("all", "official_only"):
        log.info("► Nitter/X …")
        try:
            r = fetch_nitter(); todas.extend(r)
            if r: fuentes_usadas.append("@CAPUFE / @GN_Carreteras")
        except Exception as e:
            log.error(f"  Nitter: {e}")

    # ── Dedup y salida ─────────────────────────────────────────────
    antes = len(todas)
    todas = dedup(todas)
    log.info(f"Total alertas: {antes} brutas → {len(todas)} únicas "
             f"({antes - len(todas)} duplicados eliminados)")

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

    # Sin alertas → NO conservar JSON anterior (evita mostrar alertas de ayer)
    if not todas:
        log.warning("⚠  Sin alertas — escribiendo JSON vacío (no se conservan datos viejos).")
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
