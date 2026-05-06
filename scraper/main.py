#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║         SCRAPER ALERTAS VIALES MÉXICO — AssistCargo             ║
║  Fuentes primarias:                                             ║
║    • Google News RSS  (sin API key, muy confiable)              ║
║    • CONAGUA / SMN    (XML oficial)                             ║
║    • RSS periódicos   (Milenio, El Universal, Excélsior)        ║
║  Salida: alertas.json (coordenadas incluidas para el mapa)      ║
╚══════════════════════════════════════════════════════════════════╝
"""

import json, re, hashlib, logging, time
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests, feedparser
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────────────────────────
# BÚSQUEDAS EN GOOGLE NEWS RSS  (sin API key, gratis)
# ─────────────────────────────────────────────────────────────────
GNEWS_BASE = (
    "https://news.google.com/rss/search"
    "?q={query}&hl=es-419&gl=MX&ceid=MX%3Aes-419"
)

GNEWS_QUERIES = [
    "cierre carretero México autopista",
    "bloqueo manifestación carretera México",
    "CAPUFE cierre vial accidente",
    "accidente autopista México volcadura",
    "inundación carretera México derrumbe",
    "robo carretera México asalto camino",
    "GN_Carreteras cierre bloqueo",
    "manifestantes bloqueo carretera federal México",
    "neblina cierre autopista México",
    "obras vialidad cierre carretera México",
]

# ─────────────────────────────────────────────────────────────────
# RSS PERIÓDICOS NACIONALES
# ─────────────────────────────────────────────────────────────────
RSS_NOTICIAS = [
    ("Milenio",      "https://www.milenio.com/rss"),
    ("El Universal", "https://www.eluniversal.com.mx/rss.xml"),
    ("Excélsior",    "https://www.excelsior.com.mx/rss.xml"),
    ("Infobae MX",   "https://www.infobae.com/feeds/rss/"),
]

# ─────────────────────────────────────────────────────────────────
# CONAGUA / SMN  (XML oficial)
# ─────────────────────────────────────────────────────────────────
CONAGUA_URLS = [
    "https://smn.conagua.gob.mx/tools/RESOURCES/Avisos/AvisoMeteorologico.xml",
    "https://smn.conagua.gob.mx/tools/RESOURCES/avisos/avisos.xml",
    "https://smn.conagua.gob.mx/es/avisos-meteorologicos",
]

# ─────────────────────────────────────────────────────────────────
# COORDENADAS DE CARRETERAS / ESTADOS CLAVE (para el mapa)
# ─────────────────────────────────────────────────────────────────
COORD_MAP = {
    # Autopistas federales
    "mexico puebla":          (19.35, -98.40),  "150d":             (19.35, -98.40),
    "mexico queretaro":       (20.10, -99.50),  "57d":              (20.10, -99.50),
    "mexico guadalajara":     (20.40,-103.35),  "15d":              (20.40,-103.35),
    "mexico veracruz":        (19.20, -96.80),  "140d":             (19.20, -96.80),
    "mexico acapulco":        (17.55, -99.50),  "95d":              (17.55, -99.50),
    "mexico laredo":          (24.00, -99.00),  "85d":              (24.00, -99.00),
    "mexico monterrey":       (25.40,-100.30),  "monterrey":        (25.67,-100.31),
    "tepic guadalajara":      (21.00,-104.00),  "15":               (21.00,-104.00),
    "puebla cordoba":         (18.90, -97.00),  "150":              (18.90, -97.00),
    "tinaja isla":            (18.10, -95.20),
    "siglo xxi":              (19.30,-104.00),  "jiquilpan manzanillo": (19.30,-104.00),
    "cuernavaca acapulco":    (18.20, -99.20),
    "amozoc":                 (19.10, -98.00),
    # Estados
    "jalisco":    (20.66,-103.35), "veracruz":   (19.18, -96.14),
    "oaxaca":     (17.06, -96.72), "guerrero":   (17.55, -99.50),
    "chiapas":    (16.75, -93.12), "puebla":     (19.04, -98.20),
    "hidalgo":    (20.11, -98.73), "michoacan":  (19.70,-101.19),
    "guanajuato": (21.02,-101.26), "cdmx":       (19.43, -99.13),
    "edomex":     (19.35, -99.70), "tamaulipas": (24.26, -98.84),
    "nuevo leon": (25.67,-100.31), "sinaloa":    (24.80,-107.39),
    "sonora":     (29.07,-110.96), "chihuahua":  (28.64,-106.08),
    "baja california": (30.84,-115.28),
    "coahuila":   (27.06,-101.71), "durango":    (24.02,-104.66),
    "zacatecas":  (22.77,-102.58), "san luis":   (22.15, -100.97),
    "nayarit":    (21.75,-104.85), "colima":     (19.24,-103.72),
    "tlaxcala":   (19.32, -98.24), "morelos":    (18.67, -99.10),
    "queretaro":  (20.59, -100.39),"aguascalientes": (21.88,-102.29),
    "tabasco":    (17.99, -92.93), "campeche":   (19.83, -90.53),
    "yucatan":    (20.97, -89.62), "quintana roo": (18.50, -88.30),
}

CST    = timezone(timedelta(hours=-6))
MESES  = ["enero","febrero","marzo","abril","mayo","junio",
          "julio","agosto","septiembre","octubre","noviembre","diciembre"]

KEYWORDS = {
    "cierre_total": [
        "cierre total","cerrado totalmente","sin circulación","volcadura",
        "derrumbe","deslave","inundación total","completamente cerrado",
    ],
    "bloqueo": [
        "bloqueo","manifestación","manifestantes","protesta","inconformes",
        "comuneros","pobladores","toma de caseta","huelga","paro",
    ],
    "robo": [
        "robo","asalto","asaltantes","delincuentes carretera","banda",
        "robo a transporte","pipas robadas","robo de combustible",
    ],
    "cierre_parcial": [
        "cierre parcial","un carril","reducción de carril","maniobras",
        "percance","accidente","choque","volcadura parcial","neblina",
        "falla mecánica","tractocamión","carril cerrado",
    ],
    "carga_vehicular": [
        "carga vehicular","tránsito lento","lento avance","saturación",
        "congestionamiento","avance lento","tráfico pesado",
    ],
    "obra": [
        "obra vial","trabajos viales","instalación","rehabilitación",
        "mantenimiento vial","bacheo","pavimentación","wim",
    ],
    "clima": [
        "inundación","lluvia intensa","neblina densa","granizo","tormenta",
        "alerta meteorológica","onda de calor","frente frío","norte",
        "huracán","ciclón","tifón",
    ],
}

TIPO_CONFIG = {
    "cierre_total":   dict(color="rojo",    icono="🔴", label="CIERRE TOTAL",
                           dot="#e74c3c", badge="badge-cierre-total",
                           badge_txt="CIERRE TOTAL",    col2=False, orden=0),
    "bloqueo":        dict(color="rojo",    icono="⛔", label="BLOQUEOS / MANIFESTACIONES",
                           dot="#8e44ad", badge="badge-bloqueo",
                           badge_txt="BLOQUEO",          col2=False, orden=1),
    "robo":           dict(color="rojo",    icono="🚨", label="ROBOS EN CARRETERA",
                           dot="#c0392b", badge="badge-robo",
                           badge_txt="ROBO",             col2=True,  orden=2),
    "cierre_parcial": dict(color="amarillo",icono="🟡", label="CIERRE PARCIAL / ACCIDENTES",
                           dot="#e67e22", badge="badge-cierre-parcial",
                           badge_txt="CIERRE PARCIAL",   col2=True,  orden=3),
    "carga_vehicular":dict(color="azul",    icono="🚗", label="CARGA VEHICULAR",
                           dot="#2980b9", badge="badge-carga",
                           badge_txt="CARGA VEHICULAR",  col2=True,  orden=4),
    "obra":           dict(color="verde",   icono="🚧", label="OBRA CONTINUA",
                           dot="#27ae60", badge="badge-obra",
                           badge_txt="OBRA CONTINUA",    col2=False, orden=5),
    "clima":          dict(color="azul",    icono="🌧️", label="ALERTA METEOROLÓGICA / INUNDACIÓN",
                           dot="#3498db", badge="badge-clima",
                           badge_txt="ALERTA CLIMA",     col2=False, orden=6),
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("alertas")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "es-MX,es;q=0.9",
}

# ─────────────────────────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────────────────────────

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
    """Filtra solo noticias viales/de seguridad en carreteras."""
    t = texto.lower()
    vial_kw = [
        "carretera","autopista","km ","cierre","bloqueo","manifestación",
        "accidente","volcadura","derrumbe","inundación","neblina",
        "robo","asalto","capufe","gn_carreteras","guardia nacional",
        "transporte de carga","tractocamión","manifestantes","protesta",
        "obra vial","tránsito","vialidad","carril",
    ]
    return any(k in t for k in vial_kw)


def extraer_coords(texto: str) -> Optional[tuple]:
    """Busca coordenadas conocidas a partir de nombres de carreteras/estados."""
    t = texto.lower()
    # Buscar km específico en carreteras conocidas
    for nombre, coords in COORD_MAP.items():
        if nombre in t:
            return coords
    return None


def extraer_ruta(texto: str) -> str:
    patrones = [
        r"(?:Autopista|Carretera)\s+[\w\s\-–áéíóúÁÉÍÓÚñÑ]+?(?=\s*(?:km\b|tramo|,|\.|·|$))",
        r"(?:km|kilómetro)\s+\d+[\+\d]*",
        r"\b\d{1,3}D?\b(?=\s+(?:km|tramo))",
    ]
    for p in patrones:
        m = re.search(p, texto, re.IGNORECASE)
        if m:
            return m.group(0).strip()[:90]
    return " ".join(texto.split()[:12]) + "…"


def extraer_rec(texto: str) -> str:
    m = re.search(
        r"(?:se recomienda|alternativa[:\s]|usar[:\s]|evitar[:\s])[^.!?\n]{10,150}",
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


def _ahora_str() -> str:
    n = datetime.now(CST)
    return f"{n.day} {MESES[n.month-1]} {n.year} · {n.strftime('%H:%M')} CST"


def hacer_alerta(tipo, ruta, desc, rec, fecha, fuente, url, extra_texto="") -> dict:
    c = TIPO_CONFIG[tipo]
    coords = extraer_coords(desc + " " + ruta + " " + extra_texto)
    a = {
        "id":          make_id(desc),
        "tipo":        tipo,
        "ruta":        ruta,
        "descripcion": desc[:500],
        "recomendacion": rec,
        "fecha":       fecha,
        "fuente":      fuente,
        "url":         url,
        "dot_color":   c["dot"],
        "badge":       c["badge"],
        "badge_txt":   c["badge_txt"],
    }
    if coords:
        a["lat"] = coords[0]
        a["lon"] = coords[1]
    return a


# ─────────────────────────────────────────────────────────────────
# FUENTE 1 — GOOGLE NEWS RSS  (sin API key)
# ─────────────────────────────────────────────────────────────────

def fetch_google_news() -> list[dict]:
    alertas = []
    vistos  = set()

    for query in GNEWS_QUERIES:
        url  = GNEWS_BASE.format(query=requests.utils.quote(query))
        resp = get(url, timeout=15)
        if not resp:
            log.warning(f"  GNews sin respuesta: {query}")
            continue

        feed = feedparser.parse(resp.text)
        log.info(f"  GNews '{query}': {len(feed.entries)} entradas")

        for entry in feed.entries[:12]:
            titulo = limpiar(entry.get("title", ""))
            resumen = limpiar(entry.get("summary", ""))
            texto   = f"{titulo}. {resumen}"

            if not es_relevante(texto):
                continue

            # Deduplicar por título
            key = make_id(titulo[:60].lower())
            if key in vistos:
                continue
            vistos.add(key)

            tipo  = clasificar(texto)
            ruta  = extraer_ruta(texto)
            fecha = fmt_fecha(entry.get("published", ""))
            fuente = entry.get("source", {}).get("title", "Google News")
            link   = entry.get("link", "")

            alertas.append(hacer_alerta(tipo, ruta, texto[:500],
                                        extraer_rec(texto), fecha, fuente, link, texto))

        time.sleep(0.5)  # cortesía con Google

    log.info(f"  Google News total: {len(alertas)} alertas")
    return alertas


# ─────────────────────────────────────────────────────────────────
# FUENTE 2 — RSS PERIÓDICOS NACIONALES
# ─────────────────────────────────────────────────────────────────

def fetch_rss_periodicos() -> list[dict]:
    alertas = []
    vistos  = set()

    for nombre, rss_url in RSS_NOTICIAS:
        resp = get(rss_url, timeout=15)
        if not resp:
            log.warning(f"  RSS {nombre}: sin respuesta")
            continue

        feed = feedparser.parse(resp.text)
        log.info(f"  RSS {nombre}: {len(feed.entries)} entradas")

        for entry in feed.entries[:30]:
            titulo  = limpiar(entry.get("title", ""))
            resumen = limpiar(entry.get("summary", ""))
            texto   = f"{titulo}. {resumen}"

            if not es_relevante(texto):
                continue

            key = make_id(titulo[:60].lower())
            if key in vistos:
                continue
            vistos.add(key)

            tipo  = clasificar(texto)
            ruta  = extraer_ruta(texto)
            fecha = fmt_fecha(entry.get("published", ""))
            link  = entry.get("link", rss_url)

            alertas.append(hacer_alerta(tipo, ruta, texto[:500],
                                        extraer_rec(texto), fecha, nombre, link, texto))

    log.info(f"  Periódicos total: {len(alertas)} alertas")
    return alertas


# ─────────────────────────────────────────────────────────────────
# FUENTE 3 — CONAGUA / SMN  (XML oficial)
# ─────────────────────────────────────────────────────────────────

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
            for entry in feed.entries[:15]:
                titulo  = entry.get("title", "Aviso meteorológico")
                resumen = limpiar(entry.get("summary", titulo))
                texto   = f"{titulo}. {resumen}"
                fecha   = fmt_fecha(entry.get("published", ""))
                link    = entry.get("link", url)
                alertas.append(hacer_alerta("clima", titulo[:90], texto[:500],
                                            "Maneja con precaución.", fecha,
                                            "CONAGUA/SMN", link, texto))
            return alertas

        # HTML fallback
        soup = BeautifulSoup(resp.text, "html.parser")
        for item in soup.select(".aviso, .alerta, .card, article")[:10]:
            texto = item.get_text(separator=" ", strip=True)
            if len(texto) < 20:
                continue
            alertas.append(hacer_alerta("clima", texto[:80], texto[:500],
                                        "Maneja con precaución.", _ahora_str(),
                                        "CONAGUA/SMN", url, texto))
        if alertas:
            return alertas

    log.info(f"  CONAGUA: {len(alertas)} avisos")
    return alertas


# ─────────────────────────────────────────────────────────────────
# FUENTE 4 — NITTER RSS  (si está disponible, como bonus)
# ─────────────────────────────────────────────────────────────────

NITTER_INSTANCES = [
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://xcancel.com",
    "https://nitter.cz",
    "https://nitter.1d4.us",
]
CUENTAS_X = ["CAPUFE_Oficial", "GN_Carreteras"]

VIAL_KW = ["cierre","bloqueo","carretera","autopista","km ","accidente",
           "manifestación","obra","tránsito","carga vehicular","carril",
           "volcadura","neblina","percance","tractocamión","robo","inundación"]

def fetch_nitter() -> list[dict]:
    alertas = []
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
            log.info(f"  Nitter @{cuenta} via {base}: {len(feed.entries)} tweets")
            for entry in feed.entries[:20]:
                texto = limpiar(entry.get("summary", entry.get("title", "")))
                if len(texto) < 30 or not any(k in texto.lower() for k in VIAL_KW):
                    continue
                tipo  = clasificar(texto)
                c     = TIPO_CONFIG[tipo]
                fecha = fmt_fecha(entry.get("published", ""))
                alertas.append(hacer_alerta(tipo, extraer_ruta(texto), texto[:500],
                                            extraer_rec(texto), fecha,
                                            f"@{cuenta}", entry.get("link",""), texto))
            break  # un nitter funcional es suficiente para esta cuenta
    log.info(f"  Nitter (bonus): {len(alertas)} tweets")
    return alertas


# ─────────────────────────────────────────────────────────────────
# DEDUP + AGRUPAR
# ─────────────────────────────────────────────────────────────────

def dedup(alertas: list[dict]) -> list[dict]:
    seen, out = set(), []
    for a in alertas:
        key = make_id(a["descripcion"][:80].lower())
        if key not in seen:
            seen.add(key)
            out.append(a)
    return out


def agrupar(alertas: list[dict]) -> list[dict]:
    grupos: dict[str, list] = {}
    for a in alertas:
        grupos.setdefault(a["tipo"], []).append(a)
    return [
        {"tipo":    tipo,
         "color":   cfg["color"],
         "icono":   cfg["icono"],
         "label":   cfg["label"],
         "col2":    cfg["col2"],
         "alertas": grupos[tipo]}
        for tipo, cfg in sorted(TIPO_CONFIG.items(), key=lambda x: x[1]["orden"])
        if tipo in grupos
    ]


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────

def main():
    log.info("═══  Scraper Alertas Viales México  ═══")
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

    ahora  = datetime.now(CST)
    salida = {
        "ultima_actualizacion":         ahora.isoformat(),
        "ultima_actualizacion_legible": (
            f"{ahora.day} de {MESES[ahora.month-1]} de {ahora.year}"
            f" · {ahora.strftime('%H:%M')} CST"
        ),
        "total":   len(todas),
        "fuentes": ["Google News", "Milenio", "El Universal",
                    "CONAGUA/SMN", "@CAPUFE_Oficial", "@GN_Carreteras"],
        "grupos":  agrupar(todas),
        # Lista plana con coords para el mapa
        "para_mapa": [
            {k: a[k] for k in ("id","tipo","ruta","descripcion","fecha",
                                "fuente","url","dot_color","badge_txt","lat","lon")
             if k in a}
            for a in todas if "lat" in a and "lon" in a
        ],
    }

    # Si no hay nada, conservar el JSON anterior
    if not todas:
        log.warning("⚠  Sin alertas — se conserva JSON anterior.")
        try:
            with open("alertas.json") as f:
                prev = json.load(f)
            prev["ultima_actualizacion"] = ahora.isoformat()
            prev["ultima_actualizacion_legible"] = salida["ultima_actualizacion_legible"]
            prev["nota"] = "Sin nuevos datos. Última actualización exitosa conservada."
            with open("alertas.json", "w", encoding="utf-8") as f:
                json.dump(prev, f, ensure_ascii=False, indent=2)
        except FileNotFoundError:
            with open("alertas.json", "w", encoding="utf-8") as f:
                json.dump(salida, f, ensure_ascii=False, indent=2)
        return

    with open("alertas.json", "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    log.info(f"✅  alertas.json generado · {len(todas)} alertas · "
             f"{len(salida['para_mapa'])} con coordenadas")


if __name__ == "__main__":
    main()
