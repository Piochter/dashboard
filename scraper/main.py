#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║         SCRAPER ALERTAS VIALES MÉXICO — AssistCargo             ║
║  Fuentes: @CAPUFE_Oficial · @GN_Carreteras · CONAGUA · PC       ║
║  Salida : alertas.json   (se sube via GitHub Actions)           ║
╚══════════════════════════════════════════════════════════════════╝
Ejecutar manualmente:
    pip install -r requirements.txt
    python main.py
"""

import json, re, hashlib, logging, sys
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests, feedparser
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────

CUENTAS_X = ["CAPUFE_Oficial", "GN_Carreteras"]

# Instancias Nitter públicas (sin API key) – se prueban en orden
NITTER_INSTANCES = [
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://xcancel.com",
    "https://nitter.cz",
    "https://twiiit.com",
    "https://nitter.1d4.us",
]

CAPUFE_WEB_URL = "https://www.capufe.gob.mx/site/estado-carretero.html"
SMN_AVISOS_URL = "https://smn.conagua.gob.mx/es/avisos-meteorologicos"
SMN_XML_URL    = "https://smn.conagua.gob.mx/tools/RESOURCES/Avisos/AvisoMeteorologico.xml"
CNPC_URL       = "https://www.gob.mx/cnpc/es/articulos"

CST = timezone(timedelta(hours=-6))

MESES = ["enero","febrero","marzo","abril","mayo","junio",
         "julio","agosto","septiembre","octubre","noviembre","diciembre"]

# ── Palabras clave para clasificar cada evento ──────────────────
KEYWORDS = {
    "cierre_total": [
        "cierre total","cerrado totalmente","sin circulación",
        "vía bloqueada","volcadura","derrumbe","deslave","inundación total",
    ],
    "bloqueo": [
        "bloqueo","manifestación","manifestantes","protesta","protestantes",
        "grupos","comuneros","pobladores","toma de caseta","cierre por",
        "inconformes","huelga",
    ],
    "cierre_parcial": [
        "cierre parcial","un carril","reducción de carril","carril cerrado",
        "maniobras","percance","accidente","choque","volcadura parcial",
        "neblina","falla mecánica","auxilio vial","tractocamión",
    ],
    "carga_vehicular": [
        "carga vehicular","tránsito lento","lento avance","saturación",
        "congestionamiento","flujo pesado","avance lento",
    ],
    "obra": [
        "obra","trabajos","instalación","rehabilitación","mantenimiento",
        "señalización","bacheo","pavimentación","wim","báscula dinámica",
    ],
    "clima": [
        "lluvia","neblina","granizo","viento fuerte","huracán","tormenta",
        "alerta meteorológica","onda de calor","norte","frente frío",
    ],
}

# ── Configuración visual por tipo (espeja tu CSS) ────────────────
TIPO_CONFIG = {
    "cierre_total":   dict(color="rojo",    icono="🔴", label="CIERRE TOTAL",
                           dot="#e74c3c", badge="badge-cierre-total",
                           badge_txt="CIERRE TOTAL",   col2=False, orden=0),
    "bloqueo":        dict(color="rojo",    icono="⛔", label="BLOQUEOS / MANIFESTACIONES",
                           dot="#8e44ad", badge="badge-bloqueo",
                           badge_txt="BLOQUEO",         col2=False, orden=1),
    "cierre_parcial": dict(color="amarillo",icono="🟡", label="CIERRE PARCIAL / ACCIDENTES",
                           dot="#e67e22", badge="badge-cierre-parcial",
                           badge_txt="CIERRE PARCIAL",  col2=True,  orden=2),
    "carga_vehicular":dict(color="azul",    icono="🚗", label="CARGA VEHICULAR",
                           dot="#2980b9", badge="badge-carga",
                           badge_txt="CARGA VEHICULAR", col2=True,  orden=3),
    "obra":           dict(color="verde",   icono="🚧", label="OBRA CONTINUA",
                           dot="#27ae60", badge="badge-obra",
                           badge_txt="OBRA CONTINUA",   col2=False, orden=4),
    "clima":          dict(color="azul",    icono="🌧️", label="ALERTA METEOROLÓGICA",
                           dot="#3498db", badge="badge-clima",
                           badge_txt="ALERTA CLIMA",    col2=False, orden=5),
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


def extraer_ruta(texto: str) -> str:
    patrones = [
        r"(Autopista\s+[\w\s\-–áéíóúÁÉÍÓÚñÑ]+?)(?:\s*[·|,·]|\s+km\b|\s*\btramo\b)",
        r"(Carretera\s+[\w\s\-–áéíóúÁÉÍÓÚñÑ]+?)(?:\s*[·|,·]|\s+km\b|\s*\btramo\b)",
        r"([\w\s]+\d{1,3}D?\b)(?=\s+km)",
        r"km\s+\d+[\+\d]*",
    ]
    for p in patrones:
        m = re.search(p, texto, re.IGNORECASE)
        if m:
            return m.group(0).strip()[:90]
    return " ".join(texto.split()[:12]) + "…"


def extraer_rec(texto: str) -> str:
    m = re.search(
        r"(se recomienda[^.!?\n]*|alternativa[:\s][^.!?\n]*|usar[:\s][^.!?\n]*)",
        texto, re.IGNORECASE
    )
    return m.group(0).strip()[:200] if m else ""


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


# ─────────────────────────────────────────────────────────────────
# FUENTE 1 — X / TWITTER  via  Nitter RSS  (sin API key)
# ─────────────────────────────────────────────────────────────────

VIAL_KW = [
    "cierre", "bloqueo", "carretera", "autopista", "km ",
    "accidente", "manifestación", "obra", "tránsito",
    "carga vehicular", "carril", "volcadura", "neblina",
    "percance", "auxilio", "tractocamión",
]


def fetch_nitter(cuenta: str) -> list[dict]:
    for base in NITTER_INSTANCES:
        url = f"{base}/{cuenta}/rss"
        resp = get(url, timeout=12)
        if not resp or "xml" not in resp.headers.get("content-type", ""):
            continue
        feed = feedparser.parse(resp.text)
        if not feed.entries:
            continue
        log.info(f"  Nitter OK  {base}  →  @{cuenta}  ({len(feed.entries)} tweets)")
        alertas = []
        for entry in feed.entries[:25]:
            texto = limpiar(entry.get("summary", entry.get("title", "")))
            if len(texto) < 30:
                continue
            if not any(k in texto.lower() for k in VIAL_KW):
                continue
            tipo = clasificar(texto)
            c    = TIPO_CONFIG[tipo]
            alertas.append({
                "id":          make_id(texto),
                "tipo":        tipo,
                "ruta":        extraer_ruta(texto),
                "descripcion": texto[:450],
                "recomendacion": extraer_rec(texto),
                "fecha":       fmt_fecha(entry.get("published", "")),
                "fuente":      f"@{cuenta}",
                "url":         entry.get("link", ""),
                "dot_color":   c["dot"],
                "badge":       c["badge"],
                "badge_txt":   c["badge_txt"],
            })
        return alertas          # éxito: no necesitamos otro nitter
    log.warning(f"  Sin nitter funcional para @{cuenta}")
    return []


# ─────────────────────────────────────────────────────────────────
# FUENTE 2 — CAPUFE sitio oficial
# ─────────────────────────────────────────────────────────────────

def fetch_capufe_web() -> list[dict]:
    resp = get(CAPUFE_WEB_URL) or get("https://www.capufe.gob.mx")
    if not resp:
        log.warning("  CAPUFE web inaccesible")
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    alertas = []
    selectores = ["tr", ".evento", ".incidencia", ".alerta", "article", ".notice", "li"]
    for sel in selectores:
        for row in soup.select(sel):
            texto = row.get_text(separator=" ", strip=True)
            if len(texto) < 40:
                continue
            if not any(k in texto.lower() for k in ["cierre", "bloqueo", "km ", "carretera"]):
                continue
            tipo = clasificar(texto)
            c    = TIPO_CONFIG[tipo]
            alertas.append({
                "id":          make_id(texto),
                "tipo":        tipo,
                "ruta":        extraer_ruta(texto),
                "descripcion": texto[:450],
                "recomendacion": "",
                "fecha":       _ahora_str(),
                "fuente":      "CAPUFE",
                "url":         CAPUFE_WEB_URL,
                "dot_color":   c["dot"],
                "badge":       c["badge"],
                "badge_txt":   c["badge_txt"],
            })
        if alertas:
            break
    log.info(f"  CAPUFE web: {len(alertas)} alertas")
    return alertas


# ─────────────────────────────────────────────────────────────────
# FUENTE 3 — CONAGUA / SMN
# ─────────────────────────────────────────────────────────────────

def fetch_conagua() -> list[dict]:
    alertas = []

    # Intentar XML primero
    resp = get(SMN_XML_URL)
    if resp and "xml" in resp.headers.get("content-type", ""):
        feed = feedparser.parse(resp.text)
        for entry in feed.entries[:15]:
            titulo = entry.get("title", "Aviso meteorológico")
            texto  = limpiar(entry.get("summary", titulo))
            c      = TIPO_CONFIG["clima"]
            alertas.append({
                "id":          make_id(texto),
                "tipo":        "clima",
                "ruta":        titulo[:90],
                "descripcion": texto[:450],
                "recomendacion": "Maneja con precaución en zonas afectadas.",
                "fecha":       fmt_fecha(entry.get("published", "")),
                "fuente":      "CONAGUA / SMN",
                "url":         entry.get("link", SMN_AVISOS_URL),
                "dot_color":   c["dot"],
                "badge":       c["badge"],
                "badge_txt":   c["badge_txt"],
            })
        if alertas:
            log.info(f"  CONAGUA XML: {len(alertas)} avisos")
            return alertas

    # Fallback: scraping HTML
    resp = get(SMN_AVISOS_URL)
    if resp:
        soup = BeautifulSoup(resp.text, "html.parser")
        for item in soup.select(".aviso, .alerta, .card, article, .notice")[:12]:
            texto = item.get_text(separator=" ", strip=True)
            if len(texto) < 20:
                continue
            c = TIPO_CONFIG["clima"]
            alertas.append({
                "id":          make_id(texto),
                "tipo":        "clima",
                "ruta":        texto[:80],
                "descripcion": texto[:450],
                "recomendacion": "Maneja con precaución en zonas afectadas.",
                "fecha":       _ahora_str(),
                "fuente":      "CONAGUA / SMN",
                "url":         SMN_AVISOS_URL,
                "dot_color":   c["dot"],
                "badge":       c["badge"],
                "badge_txt":   c["badge_txt"],
            })
    log.info(f"  CONAGUA HTML: {len(alertas)} avisos")
    return alertas


# ─────────────────────────────────────────────────────────────────
# FUENTE 4 — Protección Civil (CNPC)
# ─────────────────────────────────────────────────────────────────

def fetch_proteccion_civil() -> list[dict]:
    resp = get(CNPC_URL)
    if not resp:
        log.warning("  CNPC inaccesible")
        return []
    soup    = BeautifulSoup(resp.text, "html.parser")
    alertas = []
    pc_kw   = ["carretera","cierre","alerta","derrumbe","inundación","sismo","desastre","tormenta"]
    for art in soup.select("article, .article, .news-item, .card")[:10]:
        h = art.select_one("h2,h3,.title")
        texto = h.get_text(strip=True) if h else art.get_text(separator=" ", strip=True)[:200]
        if len(texto) < 10 or not any(k in texto.lower() for k in pc_kw):
            continue
        tipo = clasificar(texto)
        c    = TIPO_CONFIG[tipo]
        link = art.select_one("a[href]")
        href = link["href"] if link else ""
        url  = ("https://www.gob.mx" + href) if href.startswith("/") else href
        alertas.append({
            "id":          make_id(texto),
            "tipo":        tipo,
            "ruta":        texto[:90],
            "descripcion": texto[:450],
            "recomendacion": "",
            "fecha":       _ahora_str(),
            "fuente":      "Protección Civil",
            "url":         url,
            "dot_color":   c["dot"],
            "badge":       c["badge"],
            "badge_txt":   c["badge_txt"],
        })
    log.info(f"  Protección Civil: {len(alertas)} alertas")
    return alertas


# ─────────────────────────────────────────────────────────────────
# AGRUPAR & DEDUPLICAR
# ─────────────────────────────────────────────────────────────────

def dedup(alertas: list[dict]) -> list[dict]:
    seen, out = set(), []
    for a in alertas:
        key = make_id(a["descripcion"][:100].lower())
        if key not in seen:
            seen.add(key)
            out.append(a)
    return out


def agrupar(alertas: list[dict]) -> list[dict]:
    grupos: dict[str, list] = {}
    for a in alertas:
        grupos.setdefault(a["tipo"], []).append(a)
    resultado = []
    for tipo, cfg in sorted(TIPO_CONFIG.items(), key=lambda x: x[1]["orden"]):
        if tipo in grupos:
            resultado.append({
                "tipo":    tipo,
                "color":   cfg["color"],
                "icono":   cfg["icono"],
                "label":   cfg["label"],
                "col2":    cfg["col2"],
                "alertas": grupos[tipo],
            })
    return resultado


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────

def main():
    log.info("═══  Scraper Alertas Viales  ═══")
    todas: list[dict] = []

    # ── X / Nitter ──────────────────────────────────────────────
    for cuenta in CUENTAS_X:
        log.info(f"Scraping @{cuenta} …")
        try:
            todas.extend(fetch_nitter(cuenta))
        except Exception as e:
            log.error(f"  @{cuenta}: {e}")

    # ── CAPUFE web ───────────────────────────────────────────────
    log.info("Scraping CAPUFE web …")
    try:
        todas.extend(fetch_capufe_web())
    except Exception as e:
        log.error(f"  CAPUFE web: {e}")

    # ── CONAGUA ──────────────────────────────────────────────────
    log.info("Scraping CONAGUA …")
    try:
        todas.extend(fetch_conagua())
    except Exception as e:
        log.error(f"  CONAGUA: {e}")

    # ── Protección Civil ─────────────────────────────────────────
    log.info("Scraping Protección Civil …")
    try:
        todas.extend(fetch_proteccion_civil())
    except Exception as e:
        log.error(f"  PC: {e}")

    todas = dedup(todas)
    log.info(f"Total alertas únicas: {len(todas)}")

    if not todas:
        log.warning("⚠  Sin alertas nuevas — se conserva el JSON anterior si existe.")
        try:
            with open("alertas.json") as f:
                prev = json.load(f)
            prev["nota"] = "Sin nuevos datos. Última actualización exitosa conservada."
            prev["ultima_actualizacion"] = datetime.now(CST).isoformat()
            with open("alertas.json", "w", encoding="utf-8") as f:
                json.dump(prev, f, ensure_ascii=False, indent=2)
        except FileNotFoundError:
            pass
        return

    ahora = datetime.now(CST)
    salida = {
        "ultima_actualizacion":         ahora.isoformat(),
        "ultima_actualizacion_legible": (
            f"{ahora.day} de {MESES[ahora.month-1]} de {ahora.year}"
            f" · {ahora.strftime('%H:%M')} CST"
        ),
        "total":   len(todas),
        "fuentes": ["@CAPUFE_Oficial", "@GN_Carreteras", "CONAGUA/SMN", "Protección Civil"],
        "grupos":  agrupar(todas),
    }

    with open("alertas.json", "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    log.info("✅  alertas.json generado correctamente")


if __name__ == "__main__":
    main()
