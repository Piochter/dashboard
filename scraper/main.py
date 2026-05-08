#!/usr/bin/env python3
"""
Scraper Alertas Viales Mexico v2 - AssistCargo
Fuentes: Google News RSS, RSS nacionales/regionales, CAPUFE, CONAGUA, Nitter/X
Ventana: hoy desde medianoche CST — solo fecha actual
Filtros: requiere via+incidente, descarta resueltos, ordena mas reciente primero
"""
import json, re, hashlib, logging, time, os
from datetime import datetime, timezone, timedelta
from typing import Optional
import requests, feedparser
from bs4 import BeautifulSoup

RUN_MODE      = os.getenv("RUN_MODE", "all")
MAX_POR_FEED  = int(os.getenv("MAX_POR_FEED", "30"))
VENTANA_HORAS = int(os.getenv("VENTANA_HORAS", "3"))   # solo últimas N horas

GNEWS_BASE = "https://news.google.com/rss/search?q={query}&hl=es-419&gl=MX&ceid=MX%3Aes-419"
GNEWS_QUERIES = [
    "cierre carretero Mexico autopista hoy",
    "cierre vial autopista federal Mexico",
    "carretera cerrada Mexico accidente hoy",
    "CAPUFE cierre vial alerta",
    "GN_Carreteras cierre bloqueo Mexico",
    "Guardia Nacional carreteras cierre Mexico",
    "SCT SICT cierre carretera federal",
    "volcadura tractocamion autopista Mexico",
    "accidente carretera Mexico trailer camion",
    "derrumbe deslave carretera Mexico",
    "inundacion carretera autopista Mexico",
    "neblina cierre autopista Mexico",
    "incendio vehiculo autopista Mexico",
    "bloqueo manifestacion carretera federal Mexico",
    "manifestantes toma caseta autopista Mexico",
    "comuneros bloqueo carretera Mexico",
    "huelga paro carretera Mexico bloqueo",
    "robo carretera autopista Mexico asalto",
    "robo transporte de carga autopista Mexico",
    "asalto tractocamion carretera Mexico",
    "autopista Mexico Queretaro 57D cierre accidente",
    "autopista Mexico Puebla 150D cierre volcadura",
    "autopista Mexico Acapulco 95D cierre bloqueo",
    "autopista Siglo XXI Manzanillo cierre accidente",
    "autopista Mexico Veracruz 140D cierre",
    "autopista Mexico Laredo 85D cierre accidente",
    "cierre carretera Oaxaca Guerrero Chiapas",
    "bloqueo carretera Tamaulipas Nuevo Leon",
]

RSS_NACIONALES = [
    ("Milenio",         "https://www.milenio.com/rss"),
    ("El Universal",    "https://www.eluniversal.com.mx/rss.xml"),
    ("Excelsior",       "https://www.excelsior.com.mx/rss.xml"),
    ("Infobae MX",      "https://www.infobae.com/feeds/rss/"),
    ("El Heraldo",      "https://heraldodemexico.com.mx/feed/"),
    ("La Silla Rota",   "https://lasillarota.com/feed"),
    ("SDP Noticias",    "https://www.sdpnoticias.com/rss"),
    ("24 Horas",        "https://www.24-horas.mx/feed/"),
    ("Aristegui",       "https://aristeguinoticias.com/feed/"),
    ("El Financiero",   "https://www.elfinanciero.com.mx/rss"),
    ("Proceso",         "https://www.proceso.com.mx/?feed=rss2"),
    ("Animal Politico", "https://www.animalpolitico.com/feed"),
    ("Expansion",       "https://expansion.mx/rss"),
    ("Forbes MX",       "https://www.forbes.com.mx/feed/"),
]

RSS_REGIONALES = [
    ("El Sol de Mexico",      "https://www.elsoldemexico.com.mx/rss.xml"),
    ("NTR Guadalajara",       "https://ntrgdl.com/feed/"),
    ("El Informador Jalisco", "https://www.informador.mx/rss/ultimas-noticias.xml"),
    ("La Jornada Veracruz",   "https://www.jornadaveracruz.com.mx/feed/"),
    ("E-consulta Puebla",     "https://e-consulta.com/feed/"),
    ("Quadratin Michoacan",   "https://www.quadratin.com.mx/rss"),
    ("El Sol de Sinaloa",     "https://www.elsoldesinaloa.com.mx/rss.xml"),
    ("El Horizonte NL",       "https://www.elhorizonte.mx/rss"),
    ("AM Guanajuato",         "https://www.am.com.mx/rss"),
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
NITTER_INSTANCES = [
    "https://nitter.poast.org", "https://nitter.privacydev.net",
    "https://xcancel.com", "https://nitter.cz", "https://nitter.1d4.us",
    "https://nitter.nicfab.eu", "https://nitter.rawbit.ninja",
]
CUENTAS_X = [
    "CAPUFE_Oficial","GN_Carreteras","SICT_mx","088_GN","SEMAR_mx","conagua_clima",
    "C5_CDMX","OVIALCDMX","SSC_CDMX","LaDeTrafico","CAE_AAM","Circuito_mx",
    "C5Edomex","Vialidad_EDOMEX",
    "PolVial_GobOax","PC_Oaxaca","RED_Michoacan","SICT_Michoacan",
    "SSP_Jalisco","C4iSinaloa","SICT_BC","nSaltillo","RedVialRC",
    "SatelTrack","LaHoraMX","InformaOriente",
]

KEYWORDS = {
    "cierre_total":   ["cierre total","cerrado totalmente","sin circulacion","volcadura",
                       "derrumbe","deslave","inundacion total","completamente cerrado",
                       "ambos carriles cerrados","cierre completo","hundimiento","colapso"],
    "bloqueo":        ["bloqueo","manifestacion","manifestantes","protesta","inconformes",
                       "comuneros","pobladores","toma de caseta","huelga","paro",
                       "encadenados","quema de llantas"],
    "robo":           ["robo","asalto","asaltantes","delincuentes carretera","banda",
                       "robo a transporte","pipas robadas","robo de combustible","asalto a mano armada"],
    "cierre_parcial": ["cierre parcial","un carril","reduccion de carril","maniobras",
                       "percance","accidente","choque","volcadura parcial","neblina",
                       "falla mecanica","carril cerrado","colision","impacto vial"],
    "carga_vehicular":["carga vehicular","transito lento","lento avance","saturacion",
                       "congestionamiento","avance lento","trafico pesado","cola",
                       "fila de vehiculos","retencion vial"],
    "obra":           ["obra vial","trabajos viales","rehabilitacion","mantenimiento vial",
                       "bacheo","pavimentacion","reparacion","senalizacion vial"],
    "clima":          ["inundacion","lluvia intensa","neblina densa","granizo","tormenta",
                       "alerta meteorologica","frente frio","huracan","ciclon","viento fuerte",
                       "niebla","helada","nevada","pavimento resbaladizo"],
}

TIPO_CONFIG = {
    "cierre_total":    dict(color="rojo",    icono="🔴", label="CIERRE TOTAL",
                            dot="#e74c3c", badge="badge-cierre-total",   badge_txt="CIERRE TOTAL",   col2=False, orden=0),
    "bloqueo":         dict(color="rojo",    icono="⛔", label="BLOQUEOS / MANIFESTACIONES",
                            dot="#8e44ad", badge="badge-bloqueo",        badge_txt="BLOQUEO",        col2=False, orden=1),
    "robo":            dict(color="rojo",    icono="🚨", label="ROBOS EN CARRETERA",
                            dot="#c0392b", badge="badge-robo",           badge_txt="ROBO",           col2=True,  orden=2),
    "cierre_parcial":  dict(color="amarillo",icono="🟡", label="CIERRE PARCIAL / ACCIDENTES",
                            dot="#e67e22", badge="badge-cierre-parcial", badge_txt="CIERRE PARCIAL", col2=True,  orden=3),
    "carga_vehicular": dict(color="azul",    icono="🚗", label="CARGA VEHICULAR",
                            dot="#2980b9", badge="badge-carga",          badge_txt="CARGA VEHICULAR",col2=True,  orden=4),
    "obra":            dict(color="verde",   icono="🚧", label="OBRA CONTINUA",
                            dot="#27ae60", badge="badge-obra",           badge_txt="OBRA CONTINUA", col2=False, orden=5),
    "clima":           dict(color="azul",    icono="🌧️", label="ALERTA METEOROLOGICA",
                            dot="#3498db", badge="badge-clima",          badge_txt="ALERTA CLIMA",  col2=False, orden=6),
}

COORD_MAP = {
    "mexico puebla":(19.35,-98.40),"150d":(19.35,-98.40),"mexico queretaro":(20.10,-99.50),"57d":(20.10,-99.50),
    "mexico guadalajara":(20.40,-103.35),"15d":(20.40,-103.35),"mexico veracruz":(19.20,-96.80),"140d":(19.20,-96.80),
    "mexico acapulco":(17.55,-99.50),"95d":(17.55,-99.50),"mexico laredo":(24.00,-99.00),"85d":(24.00,-99.00),
    "mexico tuxpan":(20.50,-97.90),"130d":(20.50,-97.90),"tepic guadalajara":(21.00,-104.00),"15":(21.00,-104.00),
    "puebla cordoba":(18.90,-97.00),"150":(18.90,-97.00),"siglo xxi":(19.30,-104.00),
    "jiquilpan manzanillo":(19.30,-104.00),"cuernavaca acapulco":(18.20,-99.20),
    "amozoc":(19.10,-98.00),"tinaja isla":(18.10,-95.20),"arco norte":(19.80,-99.10),
    "monterrey saltillo":(25.50,-100.90),"40d":(25.50,-100.90),"monterrey laredo":(26.50,-99.50),"85":(26.50,-99.50),
    "guadalajara zapotlanejo":(20.65,-103.00),"colima manzanillo":(19.10,-104.30),"200":(19.10,-104.30),
    "aguascalientes guadalajara":(21.20,-102.50),"45d":(21.20,-102.50),"leon silao":(21.12,-101.68),
    "celaya queretaro":(20.55,-100.60),"queretaro san luis":(21.50,-100.00),"57":(21.50,-100.00),
    "san luis tampico":(22.50,-99.00),"80":(22.50,-99.00),"tampico ciudad mante":(22.80,-98.20),
    "torreon saltillo":(25.20,-102.00),"40":(25.20,-102.00),"culiacan mazatlan":(23.70,-106.70),
    "tijuana ensenada":(31.70,-116.70),"mexicali tijuana":(32.20,-115.50),
    "navojoa hermosillo":(28.00,-109.90),"chihuahua ciudad juarez":(30.40,-106.40),
    "durango mazatlan":(24.50,-106.00),"veracruz coatzacoalcos":(18.20,-94.80),
    "oaxaca istmo":(16.50,-95.00),"cordoba veracruz":(18.80,-96.90),"xalapa veracruz":(19.35,-96.60),
    "villahermosa cardenas":(18.10,-94.00),"merida cancun":(20.50,-87.90),
    "tuxtla gutierrez":(16.75,-93.12),"190":(16.75,-93.12),
    "jalisco":(20.66,-103.35),"veracruz":(19.18,-96.14),"oaxaca":(17.06,-96.72),
    "guerrero":(17.55,-99.50),"chiapas":(16.75,-93.12),"puebla":(19.04,-98.20),
    "hidalgo":(20.11,-98.73),"michoacan":(19.70,-101.19),"guanajuato":(21.02,-101.26),
    "cdmx":(19.43,-99.13),"edomex":(19.35,-99.70),"tamaulipas":(24.26,-98.84),
    "nuevo leon":(25.67,-100.31),"sinaloa":(24.80,-107.39),"sonora":(29.07,-110.96),
    "chihuahua":(28.64,-106.08),"baja california":(30.84,-115.28),"baja sur":(23.70,-110.00),
    "coahuila":(27.06,-101.71),"durango":(24.02,-104.66),"zacatecas":(22.77,-102.58),
    "san luis":(22.15,-100.97),"nayarit":(21.75,-104.85),"colima":(19.24,-103.72),
    "tlaxcala":(19.32,-98.24),"morelos":(18.67,-99.10),"queretaro":(20.59,-100.39),
    "aguascalientes":(21.88,-102.29),"tabasco":(17.99,-92.93),"campeche":(19.83,-90.53),
    "yucatan":(20.97,-89.62),"quintana roo":(18.50,-88.30),
    "monterrey":(25.67,-100.31),"guadalajara":(20.66,-103.35),"hermosillo":(29.07,-110.96),
    "tijuana":(32.52,-117.00),"ciudad juarez":(31.69,-106.42),"culiacan":(24.80,-107.39),
    "mazatlan":(23.24,-106.41),"manzanillo":(19.05,-104.32),"acapulco":(16.86,-99.88),
    "cancun":(21.16,-86.85),"merida":(20.97,-89.62),"cuernavaca":(18.92,-99.23),
    "toluca":(19.29,-99.66),"pachuca":(20.12,-98.73),"xalapa":(19.53,-96.91),
    "villahermosa":(17.99,-92.93),"tuxtla":(16.75,-93.12),
}

FALSOS_POSITIVOS = [
    # ── Eventos ya resueltos / liberados ──────────────────────────
    "reabre","restablece circulacion","circulacion normal","sin novedad","se normaliza",
    "ya liberaron","retiraron bloqueo","levantaron bloqueo","despejada la via","despejaron",
    "fluye con normalidad","via libre","liberaron la carretera","levantaron el cierre",
    "sin restricciones","abierta al trafico","sin afectaciones","ya no hay cierre",
    "fue despejado","retiran manifestantes","reestablecio el trafico",
    # ── Capturas / operativos policiales (no alertas activas) ─────
    "fue detenido","fueron detenidos","capturan","capturaron","detienen banda",
    "cayo banda","aprehendidos","sentenciado","condenado","detenidos por robo",
    "desarticulan banda","cayeron presuntos","aseguran vehiculo",
    # ── Contenido historico / archivo ─────────────────────────────
    "simulacro","en memoria","aniversario","recuerdan","conmemoran","historia de",
    "hace 10 anos","hace un ano","hace dos anos","archivo","reportaje especial",
    "este dia en","efemeride","recordamos cuando",
    # ── Analisis / opinion / columnas ─────────────────────────────
    "analisis de","tendencias de","estadisticas de","ranking de","tips para",
    "guia para","opinion:","columna:","editorial:","infografia","podcast",
    "segun estudio","de acuerdo con expertos","investigadores dicen",
    "expertos recomiendan","datos revelan","radiografia de",
    # ── Politica / presupuesto / proyectos (no incidentes) ────────
    "presupuesto para carreteras","proyecto de ley","plan sexenal","plan nacional",
    "programa de gobierno","inversion en carreteras","aprueba presupuesto",
    "propone construccion","anuncia licitacion","convocatoria para obra",
    "candidato promete","promesa de campana","gobernador anuncia","inaugurara",
    "entregaran","construiran","modernizaran","ampliaran la autopista",
    "elecciones","candidato","diputado","senador","gobernador electo",
    "presupuesto","pib","inflacion","tipo de cambio","bolsa de valores",
    # ── Deportes / entretenimiento / espectaculos ─────────────────
    "partido de futbol","gol","champions","liga mx","concierto","festival",
    "estreno","pelicula","serie de tv","boda de","divorcio de","embarazo de",
    "tiktoker","youtuber","influencer","reality show","alfombra roja",
    # ── Bloqueos NO carreteros (maritimos, economicos, politicos) ──
    "buques","maritimo","naval","bloqueo de puertos","bloqueo economico",
    "bloqueo comercial","bloqueo de combustible","sanciones economicas",
    "bloqueo a cuba","bloqueo a iran","bloqueo al","bloqueo de gaza",
    "hambruna","energetica","petroleo","gas natural","refineria",
    # ── Noticias internacionales (no aplican para Mexico) ─────────
    "en estados unidos","en europa","en china","en rusia","en colombia",
    "en argentina","en venezuela","en espana","a nivel global",
    "cuba enfrenta","iran ataca","israel lanza","rusia invade","ucrania",
    # ── Publicidad / clickbait ────────────────────────────────────
    "descubre como","aprende a","todo lo que debes saber","te explicamos",
    "mira como","te contamos","estas son las razones","numero de victimas del",
]

RESOLUCION_KW = [
    "liberan","libera el paso","reabre","restablece","circulacion normal",
    "retiraron","levantaron","despejaron","via libre","fluye","sin bloqueo",
    "ya no hay cierre","abierta al trafico",
]

VIAL_VIA = [
    "carretera","autopista","libramiento","periferico","viaducto","boulevard",
    "km ","caseta","peaje","vialidad","tramo carretero",
    "57d","95d","150d","15d","85d","140d","130d","siglo xxi","arco norte","carretera federal",
]
VIAL_INCIDENTE = [
    "cierre","bloqueo","manifestacion","manifestantes","accidente","volcadura","volco",
    "derrumbe","deslave","inundacion","neblina","niebla","robo","asalto","percance",
    "choque","choco","colision","tractocamion","trailer","obra vial","trabajos viales",
    "retencion","congestionamiento","trafico pesado","lento avance","carril cerrado",
    "falla mecanica","incendio vehicular","toma de caseta","protesta","paro","huelga",
    "policia vial","alerta vial","capufe","gn_carreteras",
]

CST   = timezone(timedelta(hours=-6))
MESES = ["enero","febrero","marzo","abril","mayo","junio",
         "julio","agosto","septiembre","octubre","noviembre","diciembre"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("alertas")
HEADERS = {"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36","Accept-Language":"es-MX,es;q=0.9"}

# ── Utilidades ────────────────────────────────────────────────────────
def get(url, timeout=15):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout); r.raise_for_status(); return r
    except Exception as e:
        log.debug(f"GET {url} -> {e}"); return None

def limpiar(html):
    return re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text()).strip()

def make_id(texto):
    return hashlib.md5(texto.encode()).hexdigest()[:8]

def clasificar(texto):
    t = texto.lower()
    for tipo, palabras in KEYWORDS.items():
        if any(p in t for p in palabras):
            return tipo
    return "cierre_parcial"

def es_relevante(texto):
    t = texto.lower()
    return any(k in t for k in VIAL_VIA) and any(k in t for k in VIAL_INCIDENTE)

def es_falso_positivo(texto):
    t = texto.lower()
    return any(fp in t for fp in FALSOS_POSITIVOS)

def es_resuelto(texto):
    t = texto.lower()
    return any(kw in t for kw in RESOLUCION_KW)

def extraer_coords(texto):
    t = texto.lower()
    km_m = re.search(r"km\s*(\d+)", t)
    for nombre, coords in COORD_MAP.items():
        if nombre in t:
            if km_m:
                km = int(km_m.group(1))
                return (coords[0]+(km%10)*0.01, coords[1]+(km%10)*0.01)
            return coords
    return None

def extraer_ruta(texto):
    for p in [
        r"(?:Autopista|Carretera|Libramiento|Periferico|Viaducto|Boulevard)\s+[\w\s\-]+?(?=\s*(?:km\b|tramo|,|\.|$))",
        r"(?:km|kilometro)\s+\d+[\+\.\d]*",
        r"\b\d{1,3}D?\b(?=\s+(?:km|tramo|libre|cuota))",
    ]:
        m = re.search(p, texto, re.IGNORECASE)
        if m: return m.group(0).strip()[:90]
    return " ".join(texto.split()[:10]) + "..."

def extraer_rec(texto):
    m = re.search(r"(?:se recomienda|alternativa[:\s]|usar[:\s]|evitar[:\s]|desvio[:\s])[^.!?\n]{10,160}", texto, re.IGNORECASE)
    return m.group(0).strip() if m else ""

def parse_fecha_rss(rss_date):
    if not rss_date: return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(rss_date).astimezone(CST)
    except Exception: return None

def fmt_fecha(rss_date=""):
    if not rss_date: return _ahora_str()
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(rss_date).astimezone(CST)
        return f"{dt.day} {MESES[dt.month-1]} {dt.year} · {dt.strftime('%H:%M')} CST"
    except Exception: return rss_date[:16]

def _ahora_str():
    n = datetime.now(CST)
    return f"{n.day} {MESES[n.month-1]} {n.year} · {n.strftime('%H:%M')} CST"

def esta_en_ventana(rss_date):
    """Acepta solo artículos de las últimas VENTANA_HORAS horas (ventana rodante).
    Sin fecha → rechazado para evitar basura sin timestamp."""
    dt = parse_fecha_rss(rss_date)
    if dt is None:
        return False
    return (datetime.now(CST) - dt).total_seconds() <= VENTANA_HORAS * 3600

def hacer_alerta(tipo, ruta, desc, rec, fecha, fuente, url, extra="", pub=""):
    c  = TIPO_CONFIG[tipo]
    dt = parse_fecha_rss(pub)
    ts = dt.timestamp() if dt else datetime.now(CST).timestamp()
    coords = extraer_coords(desc + " " + ruta + " " + extra)
    a = {"id":make_id(desc),"tipo":tipo,"ruta":ruta,"descripcion":desc[:500],
         "recomendacion":rec,"fecha":fecha,"fuente":fuente,"url":url,
         "dot_color":c["dot"],"badge":c["badge"],"badge_txt":c["badge_txt"],"_ts":ts}
    if coords:
        a["lat"] = round(coords[0], 5); a["lon"] = round(coords[1], 5)
    return a

# ── Fuentes ───────────────────────────────────────────────────────────
def fetch_google_news():
    alertas, vistos = [], set()
    for query in GNEWS_QUERIES:
        resp = get(GNEWS_BASE.format(query=requests.utils.quote(query)))
        if not resp: continue
        feed = feedparser.parse(resp.text); n = 0
        for e in feed.entries[:MAX_POR_FEED]:
            pub = e.get("published","")
            if not esta_en_ventana(pub): continue
            titulo = limpiar(e.get("title","")); resumen = limpiar(e.get("summary",""))
            texto = f"{titulo}. {resumen}"
            if not es_relevante(texto) or es_falso_positivo(texto) or es_resuelto(texto): continue
            key = make_id(titulo[:60].lower())
            if key in vistos: continue
            vistos.add(key)
            alertas.append(hacer_alerta(clasificar(texto), extraer_ruta(titulo), texto[:500],
                extraer_rec(texto), fmt_fecha(pub),
                e.get("source",{}).get("title","Google News"), e.get("link",""), texto, pub))
            n += 1
        log.info(f"  GNews '{query[:40]}': {len(feed.entries)} -> {n}")
        time.sleep(0.4)
    log.info(f"  Google News total: {len(alertas)}"); return alertas

def _procesar_rss(feeds):
    alertas, vistos = [], set()
    for nombre, rss_url in feeds:
        resp = get(rss_url)
        if not resp: continue
        feed = feedparser.parse(resp.text); n = 0
        for e in feed.entries[:MAX_POR_FEED]:
            pub = e.get("published","")
            if not esta_en_ventana(pub): continue
            titulo = limpiar(e.get("title","")); resumen = limpiar(e.get("summary",""))
            texto = f"{titulo}. {resumen}"
            if not es_relevante(texto) or es_falso_positivo(texto) or es_resuelto(texto): continue
            key = make_id(titulo[:60].lower())
            if key in vistos: continue
            vistos.add(key)
            alertas.append(hacer_alerta(clasificar(texto), extraer_ruta(titulo), texto[:500],
                extraer_rec(texto), fmt_fecha(pub), nombre, e.get("link", rss_url), texto, pub))
            n += 1
        log.info(f"  RSS {nombre}: {len(feed.entries)} -> {n}")
    return alertas

def fetch_rss_nacionales():
    r = _procesar_rss(RSS_NACIONALES); log.info(f"  Nacionales: {len(r)}"); return r

def fetch_rss_regionales():
    r = _procesar_rss(RSS_REGIONALES); log.info(f"  Regionales: {len(r)}"); return r

def fetch_capufe():
    alertas = []
    for url in CAPUFE_URLS:
        resp = get(url)
        if not resp: continue
        ct = resp.headers.get("content-type","")
        if "xml" in ct or url.endswith(".xml"):
            feed = feedparser.parse(resp.text)
            if feed.entries:
                for e in feed.entries[:30]:
                    pub = e.get("published","")
                    if not esta_en_ventana(pub): continue
                    titulo = limpiar(e.get("title","Alerta CAPUFE"))
                    resumen = limpiar(e.get("summary", titulo))
                    texto = f"{titulo}. {resumen}"
                    alertas.append(hacer_alerta(clasificar(texto), extraer_ruta(titulo),
                        texto[:500], extraer_rec(texto), fmt_fecha(pub),
                        "CAPUFE", e.get("link", url), texto, pub))
                log.info(f"  CAPUFE XML: {len(alertas)}"); return alertas
        soup = BeautifulSoup(resp.text, "html.parser")
        for fila in soup.select("tr, .reporte, article")[:40]:
            texto = fila.get_text(separator=" ", strip=True)
            if len(texto) < 30 or not es_relevante(texto) or es_falso_positivo(texto): continue
            alertas.append(hacer_alerta(clasificar(texto), extraer_ruta(texto), texto[:500],
                extraer_rec(texto), _ahora_str(), "CAPUFE", url, texto))
        if alertas: log.info(f"  CAPUFE HTML: {len(alertas)}"); return alertas
    log.info(f"  CAPUFE: {len(alertas)}"); return alertas

def fetch_conagua():
    alertas = []
    for url in CONAGUA_URLS:
        resp = get(url)
        if not resp: continue
        ct = resp.headers.get("content-type","")
        if "xml" in ct or url.endswith(".xml"):
            feed = feedparser.parse(resp.text)
            if feed.entries:
                for e in feed.entries[:20]:
                    pub = e.get("published","")
                    if not esta_en_ventana(pub): continue
                    titulo = e.get("title","Aviso meteorologico")
                    resumen = limpiar(e.get("summary", titulo))
                    texto = f"{titulo}. {resumen}"
                    alertas.append(hacer_alerta("clima", titulo[:90], texto[:500],
                        "Maneja con precaucion.", fmt_fecha(pub),
                        "CONAGUA/SMN", e.get("link", url), texto, pub))
                log.info(f"  CONAGUA: {len(alertas)}"); return alertas
    log.info(f"  CONAGUA: {len(alertas)}"); return alertas

def fetch_nitter():
    alertas = []
    for cuenta in CUENTAS_X:
        for base in NITTER_INSTANCES:
            url = f"{base}/{cuenta}/rss"
            resp = get(url, timeout=10)
            if not resp: continue
            ct = resp.headers.get("content-type","")
            if "xml" not in ct and "rss" not in ct: continue
            feed = feedparser.parse(resp.text)
            if not feed.entries: continue
            n = 0
            for e in feed.entries[:30]:
                pub = e.get("published","")
                if not esta_en_ventana(pub): continue
                texto = limpiar(e.get("summary", e.get("title","")))
                if len(texto) < 20 or not es_relevante(texto): continue
                if es_falso_positivo(texto) or es_resuelto(texto): continue
                alertas.append(hacer_alerta(clasificar(texto), extraer_ruta(texto), texto[:500],
                    extraer_rec(texto), fmt_fecha(pub),
                    f"@{cuenta}", e.get("link",""), texto, pub))
                n += 1
            log.info(f"  Nitter @{cuenta} via {base}: {n}"); break
    log.info(f"  Nitter total: {len(alertas)}"); return alertas

# ── Dedup ─────────────────────────────────────────────────────────────
def _tok(texto):
    return set(re.findall(r"\b[a-z\xe1\xe9\xed\xf3\xfa\xf1]{4,}\b", texto.lower()))

def dedup(alertas):
    seen_ids, seen_tok, out = set(), [], []
    for a in alertas:
        uid = make_id(a["descripcion"][:80].lower())
        if uid in seen_ids: continue
        tok = _tok(a["descripcion"][:120])
        if any(len(tok) > 3 and len(tok & st)/max(len(tok),1) >= 0.80 for st in seen_tok): continue
        seen_ids.add(uid); seen_tok.append(tok); out.append(a)
    return out

# ── Filtro de resueltos ───────────────────────────────────────────────
def _llave(texto):
    t = texto.lower()
    for nombre in sorted(COORD_MAP.keys(), key=len, reverse=True):
        if nombre in t:
            km = re.search(r"km\s*(\d+)", t)
            return nombre + (f"_km{km.group(1)}" if km else "")
    return None

def filtrar_resueltos(alertas):
    resueltos = set()
    for a in alertas:
        if es_resuelto(a["descripcion"]):
            llave = _llave(a["descripcion"] + " " + a["ruta"])
            if llave: resueltos.add(llave); log.info(f"  Tramo liberado: {llave}")
    if not resueltos: return alertas
    out = []
    for a in alertas:
        if es_resuelto(a["descripcion"]): continue
        llave = _llave(a["descripcion"] + " " + a["ruta"])
        if llave and llave in resueltos: log.info(f"  Removida (liberado): {a['ruta'][:50]}"); continue
        out.append(a)
    return out

# ── Agrupar — mas reciente primero ────────────────────────────────────
def agrupar(alertas):
    grupos = {}
    for a in alertas:
        grupos.setdefault(a["tipo"], []).append(a)
    resultado = []
    for tipo, cfg in sorted(TIPO_CONFIG.items(), key=lambda x: x[1]["orden"]):
        if tipo not in grupos: continue
        ordenados = sorted(grupos[tipo], key=lambda x: x.get("_ts",0), reverse=True)
        for a in ordenados: a.pop("_ts", None)
        resultado.append({"tipo":tipo,"color":cfg["color"],"icono":cfg["icono"],
                          "label":cfg["label"],"col2":cfg["col2"],"alertas":ordenados})
    return resultado

# ── Main ──────────────────────────────────────────────────────────────
def main():
    log.info(f"=== Scraper Alertas Viales Mexico v2 | modo={RUN_MODE} ===")
    todas, fuentes = [], []

    log.info(">> Google News...")
    try:
        r = fetch_google_news(); todas.extend(r)
        if r: fuentes.append("Google News")
    except Exception as e: log.error(f"  GNews: {e}")

    if RUN_MODE in ("all","media_only"):
        log.info(">> RSS nacionales...")
        try:
            r = fetch_rss_nacionales(); todas.extend(r)
            if r: fuentes.append("Medios nacionales")
        except Exception as e: log.error(f"  Nacionales: {e}")
        log.info(">> RSS regionales...")
        try:
            r = fetch_rss_regionales(); todas.extend(r)
            if r: fuentes.append("Medios regionales")
        except Exception as e: log.error(f"  Regionales: {e}")

    if RUN_MODE in ("all","official_only"):
        log.info(">> CAPUFE...")
        try:
            r = fetch_capufe(); todas.extend(r)
            if r: fuentes.append("CAPUFE")
        except Exception as e: log.error(f"  CAPUFE: {e}")
        log.info(">> CONAGUA...")
        try:
            r = fetch_conagua(); todas.extend(r)
            if r: fuentes.append("CONAGUA/SMN")
           except Exception as e: log.error(f"  CONAGUA: {e}")

    if RUN_MODE in ("all",):
        log.info(">> Nitter/X...")
        try:
            r = fetch_nitter(); todas.extend(r)
            if r: fuentes.append("Twitter/X")
        except Exception as e: log.error(f"  Nitter: {e}")

    log.info(f">> Total bruto: {len(todas)}")
    todas = dedup(todas)
    log.info(f">> Tras dedup: {len(todas)}")
    todas = filtrar_resueltos(todas)
    log.info(f">> Tras filtrar resueltos: {len(todas)}")

    grupos = agrupar(todas)
    para_mapa = [a for g in grupos for a in g["alertas"] if "lat" in a and "lon" in a]

    ahora = datetime.now(CST)
    resultado = {
        "generado": ahora.isoformat(),
        "ventana": f"últimas {VENTANA_HORAS} horas",
        "total": len(todas),
        "fuentes": sorted(set(fuentes)),
        "para_mapa": para_mapa,
        "grupos": grupos,
    }

    with open("alertas.json", "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    log.info(f"=== Listo: {len(todas)} alertas | {len(para_mapa)} con coordenadas ===")

if __name__ == "__main__":
    main()
