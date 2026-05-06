#!/usr/bin/env python3
"""
╔════╗
║         SCRAPER ALERTAS VIALES MÉXICO — AssistCargo             ║
║  Fuentes primarias:                                             ║
║    • Google News RSS  (sin API key)                             ║
║    • CONAGUA / SMN    (XML/HTML oficial)                        ║
║    • RSS periódicos   (Milenio, El Universal, Excélsior, etc.)  ║
║    • Nitter/X RSS     (CAPUFE y GN Carreteras si está disponible)║
║  Salida: alertas.json                                           ║
╚════╝
"""

import json
import re
import hashlib
import logging
import time
import os
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
import feedparser
from bs4 import BeautifulSoup


def _env_int(nombre: str, default: int) -> int:
    try:
        return int(os.getenv(nombre, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(nombre: str, default: float) -> float:
    try:
        return float(os.getenv(nombre, str(default)))
    except (TypeError, ValueError):
        return default


RUN_MODE = os.getenv("SCRAPER_RUN_MODE", "all").strip().lower()
LOOKBACK_MINUTES = _env_int("SCRAPER_LOOKBACK_MINUTES", 240)
OUTPUT_FILE = os.getenv("SCRAPER_OUTPUT_FILE", "alertas.json").strip() or "alertas.json"
DEDUPE_WINDOW_HOURS = _env_int("SCRAPER_DEDUPE_WINDOW_HOURS", 4)
DEDUPE_KM_TOLERANCE = max(1, _env_int("SCRAPER_DEDUPE_KM_TOLERANCE", 5))
OFFICIAL_MIN_CONFIDENCE = _env_float("SCRAPER_OFFICIAL_MIN_CONFIDENCE", 0.85)
PROBABLE_MIN_CONFIDENCE = _env_float("SCRAPER_PROBABLE_MIN_CONFIDENCE", 0.60)
ACCEPT_UNDATED = os.getenv("SCRAPER_ACCEPT_UNDATED", "false").strip().lower() in {
    "1", "true", "yes", "si", "sí"
}

CST = timezone(timedelta(hours=-6))

MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


GNEWS_BASE = (
    "https://news.google.com/rss/search"
    "?q={query}&hl=es-419&gl=MX&ceid=MX%3Aes-419"
)

GNEWS_QUERIES = [
    "CAPUFE cierre accidente autopista México",
    "GN_Carreteras cierre bloqueo accidente carretera México",
    "Guardia Nacional Carreteras cierre circulación carretera",
    "Protección Civil cierre carretera inundación derrumbe México",
    "CONAGUA lluvia intensa carretera inundación deslave México",
    "cierre carretero hoy autopista México",
    "bloqueo manifestación carretera hoy México",
    "accidente autopista hoy volcadura tractocamión México",
    "inundación carretera hoy derrumbe deslave México",
    "neblina cierre autopista hoy México",
    "obras vialidad cierre carril carretera México",
    "robo transporte de carga carretera México hoy",
    "asalto carretera autopista transporte carga México",
    "bloqueo caseta manifestantes autopista México",
]

RSS_NOTICIAS = [
    ("Milenio", "https://www.milenio.com/rss"),
    ("El Universal", "https://www.eluniversal.com.mx/rss.xml"),
    ("Excélsior", "https://www.excelsior.com.mx/rss.xml"),
    ("Infobae MX", "https://www.infobae.com/feeds/rss/"),
]

CONAGUA_URLS = [
    "https://smn.conagua.gob.mx/tools/RESOURCES/Avisos/AvisoMeteorologico.xml",
    "https://smn.conagua.gob.mx/tools/RESOURCES/avisos/avisos.xml",
    "https://smn.conagua.gob.mx/es/avisos-meteorologicos",
]

FUENTES_OFICIALES = {
    "CAPUFE",
    "CAPUFE_Oficial",
    "@CAPUFE_Oficial",
    "GN_Carreteras",
    "@GN_Carreteras",
    "Guardia Nacional Carreteras",
    "CONAGUA/SMN",
    "CONAGUA",
    "SMN",
    "Protección Civil",
}

FUENTES_MEDIOS = {
    "Google News",
    "Milenio",
    "El Universal",
    "Excélsior",
    "Infobae MX",
    "Reforma",
    "El Financiero",
    "La Jornada",
    "Proceso",
    "Expansión",
}

SOURCE_PROFILE = {
    "oficial": {"base_confidence": 0.88, "priority": 1},
    "medio": {"base_confidence": 0.55, "priority": 2},
    "crowdsource": {"base_confidence": 0.42, "priority": 3},
    "desconocida": {"base_confidence": 0.35, "priority": 4},
}

COORD_MAP = {
    "mexico puebla": (19.35, -98.40),
    "150d": (19.35, -98.40),
    "mexico queretaro": (20.10, -99.50),
    "57d": (20.10, -99.50),
    "mexico guadalajara": (20.40, -103.35),
    "15d": (20.40, -103.35),
    "mexico veracruz": (19.20, -96.80),
    "140d": (19.20, -96.80),
    "mexico acapulco": (17.55, -99.50),
    "95d": (17.55, -99.50),
    "mexico laredo": (24.00, -99.00),
    "85d": (24.00, -99.00),
    "mexico monterrey": (25.40, -100.30),
    "monterrey": (25.67, -100.31),
    "tepic guadalajara": (21.00, -104.00),
    "15": (21.00, -104.00),
    "puebla cordoba": (18.90, -97.00),
    "150": (18.90, -97.00),
    "tinaja isla": (18.10, -95.20),
    "siglo xxi": (19.30, -104.00),
    "jiquilpan manzanillo": (19.30, -104.00),
    "cuernavaca acapulco": (18.20, -99.20),
    "amozoc": (19.10, -98.00),
    "jalisco": (20.66, -103.35),
    "veracruz": (19.18, -96.14),
    "oaxaca": (17.06, -96.72),
    "guerrero": (17.55, -99.50),
    "chiapas": (16.75, -93.12),
    "puebla": (19.04, -98.20),
    "hidalgo": (20.11, -98.73),
    "michoacan": (19.70, -101.19),
    "guanajuato": (21.02, -101.26),
    "cdmx": (19.43, -99.13),
    "edomex": (19.35, -99.70),
    "tamaulipas": (24.26, -98.84),
    "nuevo leon": (25.67, -100.31),
    "sinaloa": (24.80, -107.39),
    "sonora": (29.07, -110.96),
    "chihuahua": (28.64, -106.08),
    "baja california": (30.84, -115.28),
    "coahuila": (27.06, -101.71),
    "durango": (24.02, -104.66),
    "zacatecas": (22.77, -102.58),
    "san luis": (22.15, -100.97),
    "nayarit": (21.75, -104.85),
    "colima": (19.24, -103.72),
    "tlaxcala": (19.32, -98.24),
    "morelos": (18.67, -99.10),
    "queretaro": (20.59, -100.39),
    "aguascalientes": (21.88, -102.29),
    "tabasco": (17.99, -92.93),
    "campeche": (19.83, -90.53),
    "yucatan": (20.97, -89.62),
    "quintana roo": (18.50, -88.30),
}

ESTADOS_ALIASES = {
    "Aguascalientes": ["aguascalientes"],
    "Baja California": ["baja california", "bc"],
    "Baja California Sur": ["baja california sur", "bcs"],
    "Campeche": ["campeche"],
    "Chiapas": ["chiapas"],
    "Chihuahua": ["chihuahua"],
    "CDMX": ["cdmx", "ciudad de mexico", "ciudad de méxico", "df"],
    "Coahuila": ["coahuila"],
    "Colima": ["colima"],
    "Durango": ["durango"],
    "Edomex": ["edomex", "estado de mexico", "estado de méxico"],
    "Guanajuato": ["guanajuato"],
    "Guerrero": ["guerrero"],
    "Hidalgo": ["hidalgo"],
    "Jalisco": ["jalisco"],
    "Michoacán": ["michoacan", "michoacán"],
    "Morelos": ["morelos"],
    "Nayarit": ["nayarit"],
    "Nuevo León": ["nuevo leon", "nuevo león"],
    "Oaxaca": ["oaxaca"],
    "Puebla": ["puebla"],
    "Querétaro": ["queretaro", "querétaro"],
    "Quintana Roo": ["quintana roo"],
    "San Luis Potosí": ["san luis potosi", "san luis potosí", "slp"],
    "Sinaloa": ["sinaloa"],
    "Sonora": ["sonora"],
    "Tabasco": ["tabasco"],
    "Tamaulipas": ["tamaulipas"],
    "Tlaxcala": ["tlaxcala"],
    "Veracruz": ["veracruz"],
    "Yucatán": ["yucatan", "yucatán"],
    "Zacatecas": ["zacatecas"],
}

KEYWORDS = {
    "cierre_total": [
        "cierre total",
        "cerrado totalmente",
        "sin circulación",
        "volcadura",
        "derrumbe",
        "deslave",
        "inundación total",
        "completamente cerrado",
    ],
    "bloqueo": [
        "bloqueo",
        "manifestación",
        "manifestantes",
        "protesta",
        "inconformes",
        "comuneros",
        "pobladores",
        "toma de caseta",
        "huelga",
        "paro",
    ],
    "robo": [
        "robo",
        "asalto",
        "asaltantes",
        "delincuentes carretera",
        "banda",
        "robo a transporte",
        "pipas robadas",
        "robo de combustible",
    ],
    "cierre_parcial": [
        "cierre parcial",
        "un carril",
        "reducción de carril",
        "maniobras",
        "percance",
        "accidente",
        "choque",
        "volcadura parcial",
        "neblina",
        "falla mecánica",
        "tractocamión",
        "carril cerrado",
    ],
    "carga_vehicular": [
        "carga vehicular",
        "tránsito lento",
        "lento avance",
        "saturación",
        "congestionamiento",
        "avance lento",
        "tráfico pesado",
    ],
    "obra": [
        "obra vial",
        "trabajos viales",
        "instalación",
        "rehabilitación",
        "mantenimiento vial",
        "bacheo",
        "pavimentación",
        "wim",
    ],
    "clima": [
        "inundación",
        "lluvia intensa",
        "neblina densa",
        "granizo",
        "tormenta",
        "alerta meteorológica",
        "onda de calor",
        "frente frío",
        "norte",
        "huracán",
        "ciclón",
        "tifón",
    ],
}

TIPO_CONFIG = {
    "cierre_total": dict(
        color="rojo",
        icono="🔴",
        label="CIERRE TOTAL",
        dot="#e74c3c",
        badge="badge-cierre-total",
        badge_txt="CIERRE TOTAL",
        col2=False,
        orden=0,
    ),
    "bloqueo": dict(
        color="rojo",
        icono="⛔",
        label="BLOQUEOS / MANIFESTACIONES",
        dot="#8e44ad",
        badge="badge-bloqueo",
        badge_txt="BLOQUEO",
        col2=False,
        orden=1,
    ),
    "robo": dict(
        color="rojo",
        icono="🚨",
        label="ROBOS EN CARRETERA",
        dot="#c0392b",
        badge="badge-robo",
        badge_txt="ROBO",
        col2=True,
        orden=2,
    ),
    "cierre_parcial": dict(
        color="amarillo",
        icono="🟡",
        label="CIERRE PARCIAL / ACCIDENTES",
        dot="#e67e22",
        badge="badge-cierre-parcial",
        badge_txt="CIERRE PARCIAL",
        col2=True,
        orden=3,
    ),
    "carga_vehicular": dict(
        color="azul",
        icono="🚗",
        label="CARGA VEHICULAR",
        dot="#2980b9",
        badge="badge-carga",
        badge_txt="CARGA VEHICULAR",
        col2=True,
        orden=4,
    ),
    "obra": dict(
        color="verde",
        icono="🚧",
        label="OBRA CONTINUA",
        dot="#27ae60",
        badge="badge-obra",
        badge_txt="OBRA CONTINUA",
        col2=False,
        orden=5,
    ),
    "clima": dict(
        color="azul",
        icono="🌧️",
        label="ALERTA METEOROLÓGICA / INUNDACIÓN",
        dot="#3498db",
        badge="badge-clima",
        badge_txt="ALERTA CLIMA",
        col2=False,
        orden=6,
    ),
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("alertas")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "es-MX,es;q=0.9",
}


def get(url: str, timeout: int = 15) -> Optional[requests.Response]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception as e:
        log.debug(f"GET {url} → {e}")
        return None


def limpiar(html: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(html or "", "html.parser").get_text()).strip()


def normalizar(texto: str) -> str:
    texto = texto or ""
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", texto.lower()).strip()


def make_id(texto: str) -> str:
    return hashlib.md5((texto or "").encode("utf-8")).hexdigest()[:8]


def detectar_tipo_fuente(fuente: str, default: str = "desconocida") -> str:
    f = normalizar(fuente)

    if any(normalizar(k) in f for k in FUENTES_OFICIALES):
        return "oficial"

    if any(normalizar(k) in f for k in FUENTES_MEDIOS):
        return "medio"

    if f.startswith("@") or "nitter" in f or "twitter" in f or "x.com" in f:
        return "crowdsource"

    return default


def fuente_permitida(source_type: str) -> bool:
    if RUN_MODE in {"all", "", "default"}:
        return True
    if RUN_MODE == "official_only":
        return source_type == "oficial"
    if RUN_MODE == "media_only":
        return source_type == "medio"
    return True


def clasificar(texto: str) -> str:
    t = normalizar(texto)
    for tipo, palabras in KEYWORDS.items():
        if any(normalizar(p) in t for p in palabras):
            return tipo
    return "cierre_parcial"


def es_relevante(texto: str) -> bool:
    t = normalizar(texto)

    vial_kw = [
        "carretera",
        "autopista",
        "km ",
        "kilometro",
        "cierre",
        "bloqueo",
        "manifestacion",
        "accidente",
        "volcadura",
        "derrumbe",
        "deslave",
        "inundacion",
        "neblina",
        "robo",
        "asalto",
        "capufe",
        "gn_carreteras",
        "guardia nacional",
        "transporte de carga",
        "tractocamion",
        "manifestantes",
        "protesta",
        "obra vial",
        "transito",
        "vialidad",
        "carril",
        "caseta",
    ]

    return any(k in t for k in vial_kw)


def es_falso_positivo(texto: str) -> bool:
    t = normalizar(texto)

    if not t:
        return True

    positivos_activos = [
        "sin circulacion",
        "cierre total",
        "cerrado",
        "bloqueo activo",
        "continua cerrado",
        "permanece cerrado",
        "carril cerrado",
        "reduccion de carril",
    ]

    if any(p in t for p in positivos_activos):
        return False

    falsos = [
        "reabren",
        "reabrio",
        "reapertura",
        "liberan carretera",
        "liberan autopista",
        "retiran bloqueo",
        "se retiro el bloqueo",
        "queda libre",
        "vialidad libre",
        "circulacion normal",
        "restablecen circulacion",
        "sin afectaciones",
        "sin reporte de incidentes",
        "no hay afectaciones",
        "saldo blanco",
        "simulacro",
        "archivo historico",
        "efemeride",
        "hace un ano",
        "hace una decada",
        "video antiguo",
        "nota antigua",
        "lo que debes saber",
        "asi fue",
        "historia de",
    ]

    if any(p in t for p in falsos):
        return True

    anios = [int(a) for a in re.findall(r"\b(20\d{2})\b", t)]
    anio_actual = datetime.now(CST).year

    if anios and max(anios) < anio_actual - 1:
        return True

    return False


def extraer_coords(texto: str) -> Optional[tuple]:
    t = normalizar(texto)

    for nombre, coords in COORD_MAP.items():
        if normalizar(nombre) in t:
            return coords

    return None


def extraer_ruta(texto: str) -> str:
    patrones = [
        r"(?:Autopista|Carretera|Libramiento|Tramo|Caseta)\s+[\w\s\-–áéíóúÁÉÍÓÚñÑ]+?(?=\s*(?:km\b|kilómetro|kilometro|tramo|,|\.|·|;|:|$))",
        r"(?:México|Mexico)\s*[-–]\s*[A-ZÁÉÍÓÚÑa-záéíóúñ\s]+",
        r"(?:km|kilómetro|kilometro)\s*\.?\s*\d{1,4}(?:\+\d{1,4})?",
        r"\b(?:MEX|Méx|Mex)?\s*\d{1,3}D?\b(?=\s+(?:km|tramo|autopista|carretera))",
    ]

    for p in patrones:
        m = re.search(p, texto or "", re.IGNORECASE)
        if m:
            return re.sub(r"\s+", " ", m.group(0)).strip()[:120]

    palabras = " ".join((texto or "").split()[:12])
    return f"{palabras}…" if palabras else "Ruta no identificada"


def extraer_road_name(ruta: str, texto: str) -> Optional[str]:
    base = ruta or ""

    if base and not base.endswith("…") and not normalizar(base).startswith(("km ", "kilometro ")):
        return base[:120]

    m = re.search(
        r"(?:Autopista|Carretera|Libramiento|Tramo|Caseta)\s+[\w\s\-–áéíóúÁÉÍÓÚñÑ]+?(?=\s*(?:km\b|kilómetro|kilometro|,|\.|;|:|$))",
        texto or "",
        re.IGNORECASE,
    )

    return re.sub(r"\s+", " ", m.group(0)).strip()[:120] if m else None


def extraer_rec(texto: str) -> str:
    m = re.search(
        r"(?:se recomienda|alternativa[:\s]|usar[:\s]|evitar[:\s]|tome precauciones|precaución[:\s]|precaucion[:\s])[^.!?\n]{10,180}",
        texto or "",
        re.IGNORECASE,
    )

    return m.group(0).strip() if m else ""


def fmt_fecha(rss_date: str = "") -> str:
    if not rss_date:
        return _ahora_str()

    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(rss_date).astimezone(CST)
        return f"{dt.day} {MESES[dt.month - 1]} {dt.year} · {dt.strftime('%H:%M')} CST"
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
    dt = parse_fecha_rss(rss_date)

    if dt is None:
        return ACCEPT_UNDATED

    if LOOKBACK_MINUTES <= 0:
        return True

    ahora = datetime.now(CST)
    delta = ahora - dt

    return timedelta(minutes=-60) <= delta <= timedelta(minutes=LOOKBACK_MINUTES)


def es_de_hoy(rss_date: str) -> bool:
    return esta_en_ventana(rss_date)


def _ahora_str() -> str:
    n = datetime.now(CST)
    return f"{n.day} {MESES[n.month - 1]} {n.year} · {n.strftime('%H:%M')} CST"


def iso_or_none(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def extraer_estado(texto: str) -> Optional[str]:
    t = normalizar(texto)

    for estado, aliases in ESTADOS_ALIASES.items():
        for alias in aliases:
            alias_norm = normalizar(alias)
            if re.search(rf"\b{re.escape(alias_norm)}\b", t):
                return estado

    return None


def extraer_km(texto: str) -> Optional[str]:
    m = re.search(
        r"(?:km|kilómetro|kilometro)\s*\.?\s*(\d{1,4}(?:\+\d{1,4})?)",
        texto or "",
        re.IGNORECASE,
    )

    return m.group(1) if m else None


def km_numero(km: Optional[str]) -> Optional[float]:
    if not km:
        return None

    m = re.match(r"(\d{1,4})(?:\+(\d{1,4}))?", km)

    if not m:
        return None

    base = float(m.group(1))
    metros = float(m.group(2) or 0) / 1000

    return base + metros


def extraer_direccion(texto: str) -> Optional[str]:
    patrones = [
        r"(?:sentido|dirección|direccion|rumbo a|hacia)\s+([A-ZÁÉÍÓÚÑa-záéíóúñ\s\-]{3,40})",
        r"cuerpo\s+([AB])\b",
    ]

    for p in patrones:
        m = re.search(p, texto or "", re.IGNORECASE)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip(" .,-").upper()[:40]

    return None


def extraer_status(texto: str) -> str:
    t = normalizar(texto)

    if any(
        p in t
        for p in [
            "reabren",
            "reabrio",
            "restablecen circulacion",
            "queda libre",
            "circulacion normal",
        ]
    ):
        return "reabierto"

    if any(
        p in t
        for p in [
            "cerrado",
            "cierre",
            "bloqueo",
            "sin circulacion",
            "carril cerrado",
            "continua",
            "permanece",
        ]
    ):
        return "activo"

    if any(
        p in t
        for p in [
            "precaucion",
            "precaución",
            "reduccion de carril",
            "reducción de carril",
            "carga vehicular",
            "transito lento",
            "tránsito lento",
        ]
    ):
        return "precaucion"

    return "activo"


def calcular_expiracion(tipo: str, detected_at: datetime) -> str:
    horas = {
        "cierre_total": 4,
        "bloqueo": 4,
        "robo": 8,
        "cierre_parcial": 3,
        "carga_vehicular": 2,
        "obra": 24,
        "clima": 6,
    }.get(tipo, 4)

    return (detected_at + timedelta(hours=horas)).isoformat()


def confidence_label(confidence: float) -> str:
    if confidence >= OFFICIAL_MIN_CONFIDENCE:
        return "confirmado"

    if confidence >= PROBABLE_MIN_CONFIDENCE:
        return "probable"

    return "no_verificado"


def calcular_confianza(
    source_type: str,
    tipo: str,
    texto: str,
    coords: Optional[tuple],
    km: Optional[str],
    direction: Optional[str],
    status: str,
) -> float:
    base = SOURCE_PROFILE.get(source_type, SOURCE_PROFILE["desconocida"])["base_confidence"]

    if km:
        base += 0.05

    if coords:
        base += 0.04

    if direction:
        base += 0.03

    if tipo in {"cierre_total", "bloqueo", "clima"}:
        base += 0.02

    if status == "reabierto":
        base -= 0.25

    if es_falso_positivo(texto):
        base -= 0.30

    return round(max(0.05, min(base, 0.98)), 2)


def hacer_alerta(
    tipo,
    ruta,
    desc,
    rec,
    fecha,
    fuente,
    url,
    extra_texto="",
    rss_date: str = "",
    source_type_hint: Optional[str] = None,
) -> dict:
    texto_completo = f"{desc} {ruta} {extra_texto}".strip()
    tipo = tipo if tipo in TIPO_CONFIG else clasificar(texto_completo)
    c = TIPO_CONFIG[tipo]

    coords = extraer_coords(texto_completo)
    source_type = detectar_tipo_fuente(fuente, source_type_hint or "desconocida")
    estado = extraer_estado(texto_completo)
    km = extraer_km(texto_completo)
    direction = extraer_direccion(texto_completo)
    road_name = extraer_road_name(ruta, texto_completo)
    status = extraer_status(texto_completo)
    published_dt = parse_fecha_rss(rss_date)
    detected_dt = datetime.now(CST)

    confidence = calcular_confianza(
        source_type=source_type,
        tipo=tipo,
        texto=texto_completo,
        coords=coords,
        km=km,
        direction=direction,
        status=status,
    )

    alerta = {
        "id": make_id(f"{desc}|{fuente}|{url}"),
        "tipo": tipo,
        "ruta": ruta,
        "descripcion": desc[:500],
        "recomendacion": rec,
        "fecha": fecha,
        "fuente": fuente,
        "url": url,
        "dot_color": c["dot"],
        "badge": c["badge"],
        "badge_txt": c["badge_txt"],
        "event_type": tipo,
        "status": status,
        "source_name": fuente,
        "source_type": source_type,
        "confidence": confidence,
        "confidence_label": confidence_label(confidence),
        "state": estado,
        "municipality": None,
        "road_name": road_name,
        "km": km,
        "direction": direction,
        "published_at": iso_or_none(published_dt),
        "detected_at": detected_dt.isoformat(),
        "expires_at": calcular_expiracion(tipo, detected_dt),
        "raw_text": texto_completo[:2000],
        "source_url": url,
        "related_sources": [fuente],
        "deduplicated_count": 1,
    }

    if coords:
        alerta["lat"] = coords[0]
        alerta["lon"] = coords[1]

    return alerta


def fetch_google_news() -> list[dict]:
    alertas = []
    vistos = set()

    for query in GNEWS_QUERIES:
        url = GNEWS_BASE.format(query=requests.utils.quote(query))
        resp = get(url, timeout=15)

        if not resp:
            log.warning(f"  GNews sin respuesta: {query}")
            continue

        feed = feedparser.parse(resp.text)
        log.info(f"  GNews '{query}': {len(feed.entries)} entradas")

        for entry in feed.entries[:15]:
            pub_date = entry.get("published", "")

            if not esta_en_ventana(pub_date):
                continue

            titulo = limpiar(entry.get("title", ""))
            resumen = limpiar(entry.get("summary", ""))
            texto = f"{titulo}. {resumen}"

            if not es_relevante(texto) or es_falso_positivo(texto):
                continue

            fuente = entry.get("source", {}).get("title", "Google News")
            source_type = detectar_tipo_fuente(fuente, "medio")

            if not fuente_permitida(source_type):
                continue

            key = make_id(normalizar(titulo[:100]))

            if key in vistos:
                continue

            vistos.add(key)

            tipo = clasificar(texto)
            ruta = extraer_ruta(texto)
            fecha = fmt_fecha(pub_date)
            link = entry.get("link", "")

            alertas.append(
                hacer_alerta(
                    tipo,
                    ruta,
                    texto[:500],
                    extraer_rec(texto),
                    fecha,
                    fuente,
                    link,
                    texto,
                    rss_date=pub_date,
                    source_type_hint="medio",
                )
            )

        time.sleep(0.5)

    log.info(f"  Google News total: {len(alertas)} alertas")
    return alertas


def fetch_rss_periodicos() -> list[dict]:
    alertas = []
    vistos = set()

    for nombre, rss_url in RSS_NOTICIAS:
        source_type = detectar_tipo_fuente(nombre, "medio")

        if not fuente_permitida(source_type):
            continue

        resp = get(rss_url, timeout=15)

        if not resp:
            log.warning(f"  RSS {nombre}: sin respuesta")
            continue

        feed = feedparser.parse(resp.text)
        log.info(f"  RSS {nombre}: {len(feed.entries)} entradas")

        for entry in feed.entries[:30]:
            pub_date = entry.get("published", "")

            if not esta_en_ventana(pub_date):
                continue

            titulo = limpiar(entry.get("title", ""))
            resumen = limpiar(entry.get("summary", ""))
            texto = f"{titulo}. {resumen}"

            if not es_relevante(texto) or es_falso_positivo(texto):
                continue

            key = make_id(f"{nombre}|{normalizar(titulo[:100])}")

            if key in vistos:
                continue

            vistos.add(key)

            tipo = clasificar(texto)
            ruta = extraer_ruta(texto)
            fecha = fmt_fecha(pub_date)
            link = entry.get("link", rss_url)

            alertas.append(
                hacer_alerta(
                    tipo,
                    ruta,
                    texto[:500],
                    extraer_rec(texto),
                    fecha,
                    nombre,
                    link,
                    texto,
                    rss_date=pub_date,
                    source_type_hint="medio",
                )
            )

    log.info(f"  Periódicos total: {len(alertas)} alertas")
    return alertas


def fetch_conagua() -> list[dict]:
    alertas = []

    if not fuente_permitida("oficial"):
        return alertas

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

            for entry in feed.entries[:15]:
                pub_date = entry.get("published", "")

                if pub_date and not esta_en_ventana(pub_date):
                    continue

                titulo = limpiar(entry.get("title", "Aviso meteorológico"))
                resumen = limpiar(entry.get("summary", titulo))
                texto = f"{titulo}. {resumen}"

                if es_falso_positivo(texto):
                    continue

                fecha = fmt_fecha(pub_date)
                link = entry.get("link", url)

                alertas.append(
                    hacer_alerta(
                        "clima",
                        titulo[:90],
                        texto[:500],
                        "Maneja con precaución y verifica condiciones antes de salir.",
                        fecha,
                        "CONAGUA/SMN",
                        link,
                        texto,
                        rss_date=pub_date,
                        source_type_hint="oficial",
                    )
                )

            return alertas

        soup = BeautifulSoup(resp.text, "html.parser")

        for item in soup.select(".aviso, .alerta, .card, article")[:10]:
            texto = item.get_text(separator=" ", strip=True)

            if len(texto) < 20 or es_falso_positivo(texto):
                continue

            alertas.append(
                hacer_alerta(
                    "clima",
                    texto[:80],
                    texto[:500],
                    "Maneja con precaución y verifica condiciones antes de salir.",
                    _ahora_str(),
                    "CONAGUA/SMN",
                    url,
                    texto,
                    source_type_hint="oficial",
                )
            )

        if alertas:
            return alertas

    log.info(f"  CONAGUA: {len(alertas)} avisos")
    return alertas


NITTER_INSTANCES = [
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://xcancel.com",
    "https://nitter.cz",
    "https://nitter.1d4.us",
]

CUENTAS_X = ["CAPUFE_Oficial", "GN_Carreteras"]

VIAL_KW = [
    "cierre",
    "bloqueo",
    "carretera",
    "autopista",
    "km ",
    "accidente",
    "manifestación",
    "obra",
    "tránsito",
    "carga vehicular",
    "carril",
    "volcadura",
    "neblina",
    "percance",
    "tractocamión",
    "robo",
    "inundación",
]


def fetch_nitter() -> list[dict]:
    alertas = []

    if not fuente_permitida("oficial"):
        return alertas

    for cuenta in CUENTAS_X:
        for base in NITTER_INSTANCES:
            url = f"{base}/{cuenta}/rss"
            resp = get(url, timeout=10)

            if not resp:
                continue

            ct = resp.headers.get("content-type", "")

            if "xml" not in ct and "rss" not in ct:
                continue

            feed = feedparser.parse(resp.text)

            if not feed.entries:
                continue

            log.info(f"  Nitter @{cuenta} via {base}: {len(feed.entries)} tweets")

            for entry in feed.entries[:20]:
                pub_date = entry.get("published", "")

                if not esta_en_ventana(pub_date):
                    continue

                texto = limpiar(entry.get("summary", entry.get("title", "")))
                texto_norm = normalizar(texto)

                if len(texto) < 30 or not any(normalizar(k) in texto_norm for k in VIAL_KW):
                    continue

                if es_falso_positivo(texto):
                    continue

                tipo = clasificar(texto)
                fecha = fmt_fecha(pub_date)

                alertas.append(
                    hacer_alerta(
                        tipo,
                        extraer_ruta(texto),
                        texto[:500],
                        extraer_rec(texto),
                        fecha,
                        f"@{cuenta}",
                        entry.get("link", ""),
                        texto,
                        rss_date=pub_date,
                        source_type_hint="oficial",
                    )
                )

            break

    log.info(f"  Nitter (bonus): {len(alertas)} tweets")
    return alertas


def _parse_iso_datetime(valor: Optional[str]) -> Optional[datetime]:
    if not valor:
        return None

    try:
        dt = datetime.fromisoformat(valor)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CST)

        return dt.astimezone(CST)
    except Exception:
        return None


def _time_bucket(alerta: dict) -> int:
    dt = (
        _parse_iso_datetime(alerta.get("published_at"))
        or _parse_iso_datetime(alerta.get("detected_at"))
        or datetime.now(CST)
    )

    window_seconds = max(1, DEDUPE_WINDOW_HOURS) * 3600

    return int(dt.timestamp() // window_seconds)


def _km_bucket(km: Optional[str]) -> str:
    valor = km_numero(km)

    if valor is None:
        return "sin_km"

    inicio = int(valor // DEDUPE_KM_TOLERANCE) * DEDUPE_KM_TOLERANCE

    return f"km_{inicio}_{inicio + DEDUPE_KM_TOLERANCE}"


def _dedupe_key(alerta: dict) -> tuple:
    road = normalizar(alerta.get("road_name") or alerta.get("ruta") or "")
    km = alerta.get("km")

    desc_sig = ""

    if not km:
        desc_sig = make_id(normalizar(alerta.get("descripcion", ""))[:160])

    return (
        alerta.get("event_type") or alerta.get("tipo"),
        normalizar(alerta.get("state") or "sin_estado"),
        road[:90] if road else desc_sig,
        _km_bucket(km),
        normalizar(alerta.get("direction") or "sin_sentido"),
        _time_bucket(alerta),
    )


def _merge_alertas(a: dict, b: dict) -> dict:
    base, extra = (a, b) if a.get("confidence", 0) >= b.get("confidence", 0) else (b, a)
    merged = dict(base)

    related = set(merged.get("related_sources") or [])
    related.update(extra.get("related_sources") or [])
    related.add(merged.get("source_name") or merged.get("fuente") or "desconocida")
    related.add(extra.get("source_name") or extra.get("fuente") or "desconocida")
    related.discard("")

    merged["related_sources"] = sorted(related)
    merged["deduplicated_count"] = int(a.get("deduplicated_count", 1)) + int(
        b.get("deduplicated_count", 1)
    )

    for campo in [
        "state",
        "municipality",
        "road_name",
        "km",
        "direction",
        "lat",
        "lon",
        "published_at",
    ]:
        if not merged.get(campo) and extra.get(campo):
            merged[campo] = extra[campo]

    if extra.get("status") == "activo":
        merged["status"] = "activo"

    boost = min(0.12, 0.05 * max(0, len(related) - 1))

    merged["confidence"] = round(
        min(0.98, max(a.get("confidence", 0), b.get("confidence", 0)) + boost),
        2,
    )
    merged["confidence_label"] = confidence_label(merged["confidence"])
    merged["id"] = make_id(
        "|".join(
            [
                str(merged.get("event_type") or merged.get("tipo") or ""),
                str(merged.get("state") or ""),
                str(merged.get("road_name") or merged.get("ruta") or ""),
                str(merged.get("km") or ""),
                ",".join(merged["related_sources"]),
            ]
        )
    )

    return merged


def dedup(alertas: list[dict]) -> list[dict]:
    grupos: dict[tuple, dict] = {}

    for alerta in alertas:
        key = _dedupe_key(alerta)

        if key not in grupos:
            grupos[key] = alerta
        else:
            grupos[key] = _merge_alertas(grupos[key], alerta)

    out = list(grupos.values())
    out.sort(
        key=lambda a: (
            a.get("confidence", 0),
            a.get("published_at") or a.get("detected_at") or "",
        ),
        reverse=True,
    )

    return out


def agrupar(alertas: list[dict]) -> list[dict]:
    grupos: dict[str, list] = {}

    for a in alertas:
        grupos.setdefault(a["tipo"], []).append(a)

    return [
        {
            "tipo": tipo,
            "color": cfg["color"],
            "icono": cfg["icono"],
            "label": cfg["label"],
            "col2": cfg["col2"],
            "alertas": sorted(
                grupos[tipo],
                key=lambda alerta: alerta.get("confidence", 0),
                reverse=True,
            ),
        }
        for tipo, cfg in sorted(TIPO_CONFIG.items(), key=lambda x: x[1]["orden"])
        if tipo in grupos
    ]


def resumen_confianza(alertas: list[dict]) -> dict:
    resumen = {
        "confirmadas": 0,
        "probables": 0,
        "no_verificadas": 0,
    }

    for a in alertas:
        label = a.get("confidence_label")

        if label == "confirmado":
            resumen["confirmadas"] += 1
        elif label == "probable":
            resumen["probables"] += 1
        else:
            resumen["no_verificadas"] += 1

    return resumen


def fuentes_detectadas(alertas: list[dict]) -> list[str]:
    fuentes = {
        a.get("source_name") or a.get("fuente")
        for a in alertas
        if a.get("source_name") or a.get("fuente")
    }

    if not fuentes:
        fuentes = {
            "Google News",
            "Milenio",
            "El Universal",
            "CONAGUA/SMN",
            "@CAPUFE_Oficial",
            "@GN_Carreteras",
        }

    return sorted(fuentes)


def construir_salida(alertas: list[dict]) -> dict:
    ahora = datetime.now(CST)

    return {
        "ultima_actualizacion": ahora.isoformat(),
        "ultima_actualizacion_legible": (
            f"{ahora.day} de {MESES[ahora.month - 1]} de {ahora.year}"
            f" · {ahora.strftime('%H:%M')} CST"
        ),
        "total": len(alertas),
        "fuentes": fuentes_detectadas(alertas),
        "alertas": alertas,
        "grupos": agrupar(alertas),
        "para_mapa": [
            {
                k: a[k]
                for k in (
                    "id",
                    "tipo",
                    "event_type",
                    "status",
                    "ruta",
                    "road_name",
                    "descripcion",
                    "fecha",
                    "published_at",
                    "detected_at",
                    "expires_at",
                    "fuente",
                    "source_name",
                    "source_type",
                    "confidence",
                    "confidence_label",
                    "url",
                    "source_url",
                    "dot_color",
                    "badge_txt",
                    "state",
                    "km",
                    "direction",
                    "lat",
                    "lon",
                )
                if k in a
            }
            for a in alertas
            if "lat" in a and "lon" in a
        ],
        "resumen_confianza": resumen_confianza(alertas),
        "pipeline": {
            "run_mode": RUN_MODE,
            "lookback_minutes": LOOKBACK_MINUTES,
            "dedupe_window_hours": DEDUPE_WINDOW_HOURS,
            "dedupe_km_tolerance": DEDUPE_KM_TOLERANCE,
            "official_min_confidence": OFFICIAL_MIN_CONFIDENCE,
            "probable_min_confidence": PROBABLE_MIN_CONFIDENCE,
            "accept_undated": ACCEPT_UNDATED,
            "output_file": OUTPUT_FILE,
        },
    }


def main():
    log.info("═══ Scraper Alertas Viales México ═══")
    log.info(
        "Config: run_mode=%s · lookback=%s min · output=%s",
        RUN_MODE,
        LOOKBACK_MINUTES,
        OUTPUT_FILE,
    )

    todas: list[dict] = []

    log.info("► Google News RSS …")
    try:
        todas.extend(fetch_google_news())
    except Exception as e:
        log.error(f"  Google News: {e}")

    log.info("► RSS Periódicos …")
    try:
        todas.extend(fetch_rss_periodicos())
    except Exception as e:
        log.error(f"  Periódicos: {e}")

    log.info("► CONAGUA/SMN …")
    try:
        todas.extend(fetch_conagua())
    except Exception as e:
        log.error(f"  CONAGUA: {e}")

    log.info("► Nitter/X (bonus) …")
    try:
        todas.extend(fetch_nitter())
    except Exception as e:
        log.error(f"  Nitter: {e}")

    todas = dedup(todas)
    log.info(f"Total alertas únicas: {len(todas)}")

    salida = construir_salida(todas)

    if not todas:
        log.warning("⚠ Sin alertas nuevas — se conserva JSON anterior si existe.")

        try:
            with open(OUTPUT_FILE, encoding="utf-8") as f:
                prev = json.load(f)

            prev["ultima_actualizacion"] = salida["ultima_actualizacion"]
            prev["ultima_actualizacion_legible"] = salida["ultima_actualizacion_legible"]
            prev["nota"] = "Sin nuevos datos. Última actualización exitosa conservada."
            prev["pipeline"] = salida["pipeline"]

            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(prev, f, ensure_ascii=False, indent=2)

            log.info(f"✅ {OUTPUT_FILE} conservado con timestamp actualizado")
            return

        except FileNotFoundError:
            log.warning(f"⚠ No existía {OUTPUT_FILE}; se generará archivo vacío.")
        except Exception as e:
            log.warning(f"⚠ No se pudo conservar JSON anterior: {e}")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    log.info(
        "✅ %s generado · %s alertas · %s con coordenadas",
        OUTPUT_FILE,
        len(todas),
        len(salida["para_mapa"]),
    )


if __name__ == "__main__":
    main()