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
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────
LOOKBACK_HORAS = int(os.getenv("SCRAPER_LOOKBACK_MINUTES", str(24*60))) // 60
MAX_POR_FEED   = int(os.getenv("MAX_POR_FEED", "30"))
RUN_MODE       = os.getenv("SCRAPER_RUN_MODE", "all")
ACCEPT_UNDATED = os.getenv("SCRAPER_ACCEPT_UNDATED", "false").lower() == "true"

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
# RSS MEDIOS REGIONALES
# ─────────────────────────────────────────────────────────────────────
RSS_REGIONALES = [
    ("El Sol de México",      "https://www.elsoldemexico.com.mx/rss.xml"),
    ("NTR Guadalajara",       "https://ntrgdl.com/feed/"),
    ("El Informador Jalisco", "https://www.informador.mx/rss/ultimas-noticias.xml"),
    ("Milenio Jalisco",       "https://www.milenio.com/rss/jalisco"),
    ("La Jornada Veracruz",   "https://www.jornadaveracruz.com.mx/feed/"),
    ("E-consulta Puebla",     "https://e-consulta.com/feed/"),
    ("Quadratín Michoacán",   "https://www.quadratin.com.mx/rss"),
    ("El Sol de Sinaloa",     "https://www.elsoldesinaloa.com.mx/rss.xml"),
    ("El Horizonte NL",       "https://www.elhorizonte.mx/rss"),
    ("AM Guanajuato",         "https://www.am.com.mx/rss"),
]

# ─────────────────────────────────────────────────────────────────────
# CONAGUA / SMN
# ─────────────────────────────────────────────────────────────────────
CONAGUA_URLS = [
    "https://smn.conagua.gob.mx/tools/RESOURCES/Avisos/AvisoMeteorologico.xml",
    "https://smn.conagua.gob.mx/tools/RESOURCES/avisos/avisos.xml",
]

# ─────────────────────────────────────────────────────────────────────
# CAPUFE
# ─────────────────────────────────────────────────────────────────────
CAPUFE_URLS = [
    "https://www.capufe.gob.mx/site/xml/ReporteVialidad.xml",
    "https://www.capufe.gob.mx/site/webSCT/comunicados.xml",
    "https://www.capufe.gob.mx/norteMonitor/",
]

# ─────────────────────────────────────────────────────────────────────
# NITTER
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
    "CAPUFE_Oficial", "GN_Carreteras", "SICT_mx", "088_GN",
    "SEMAR_mx", "conagua_clima", "C5_CDMX", "OVIALCDMX",
    "SSC_CDMX", "LaDeTrafico", "CAE_AAM", "Circuito_mx",
    "C5Edomex", "Vialidad_EDOMEX", "PolVial_GobOax", "PC_Oaxaca",
    "RED_Michoacan", "SICT_Michoacan", "SSP_Jalisco", "C4iSinaloa",
    "SICT_BC", "nSaltillo", "RedVialRC", "SatelTrack",
    "LaHoraMX", "InformaOriente",
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
# COORDENADAS
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
# FALSOS POSITIVOS — ampliado con contextos no viales
# ─────────────────────────────────────────────────────────────────────
FALSOS_POSITIVOS = [
    # Resolución de incidentes
    "reabre","restablece circulación","circulación normal","sin novedad",
    "se normaliza","ya liberaron","retiraron bloqueo","fue detenido",
    "fueron detenidos","capturan","capturaron","detienen banda",
    # Contenido histórico/archivo
    "simulacro","en memoria","aniversario","recuerdan","conmemoran",
    "historia","hace 10 años","hace un año","archivo","reportaje especial",
    "análisis de","tendencias de","estadísticas de","ranking de",
    # Deportes
    "liga mx","premier league","champions league","copa mx","nfl","nba",
    "fútbol","futbol","derrota","victoria","gol","partido","temporada",
    "jugador","equipo","técnico","entrenador","torneo","atleta",
    "rayadas","chivas","américa fc","cruz azul","pumas","tigres",
    # Política / economía
    "morena","pan ","pri ","senado","diputados","congreso","elecciones",
    "presidente","secretaría de salud","imss","issste","pensión",
    "bolsa de valores","tipo de cambio","dólar","inflación","pib",
    "banco de méxico","banxico","reforma fiscal","presupuesto",
    # Entretenimiento
    "serie de televisión","película","estreno","temporada final",
    "the boys","netflix","disney","amazon prime","spotify",
    "concierto","festival","cantante","actor","actriz",
    # Salud
    "síndrome","enfermedad","padecimiento","tratamiento médico",
    "hospital","clínica","diagnóstico","síntoma","vacuna",
    "ovario poliquístico","diabetes","cáncer","obesidad",
    # Tecnología / negocios
    "inteligencia artificial","startup","app ","software","hardware",
    "criptomoneda","bitcoin","nft","metaverso",
    # Cierre en contexto no vial
    "cierre de campaña","cierre de año","cierre fiscal","cierre comercial",
    "cierre de empresa","cierre de negocio","cierre de fábrica",
    "accidente aéreo","accidente marítimo","accidente minero",
    # AIFA / aeropuerto
    "aifa","aeropuerto","vuelo","aerolínea","terminal aérea",
    "pasajeros aéreos","pista de aterrizaje",
]

# ─────────────────────────────────────────────────────────────────────
# KEYWORDS VIALES — primarios + contexto (ambos requeridos)
# ─────────────────────────────────────────────────────────────────────
VIAL_KW_PRIMARIOS = [
    "carretera","autopista","cierre vial","bloqueo carretero",
    "manifestación","accidente vial","volcadura","derrumbe",
    "inundación vial","neblina","robo carretero","asalto carretera",
    "capufe","gn_carreteras","guardia nacional carretera",
    "tractocamión","tráiler","caseta","peaje","carril cerrado",
    "cierre de carretera","bloqueo de carretera","percance vial",
    "colisión","km ","kilómetro ","57d","95d","150d","15d","85d",
    "140d","siglo xxi","arco norte","transporte de carga",
    "congestionamiento vial","retención vial","carga vehicular",
    "obra vial","trabajos viales","mantenimiento carretero",
]

VIAL_KW_CONTEXTO = [
    "méxico","federal","autopista","carretera","estado de",
    "cdmx","edomex","jalisco","veracruz","oaxaca","puebla",
    "guerrero","michoacán","chiapas","tamaulipas","nuevo león",
    "sinaloa","sonora","chihuahua","coahuila","hidalgo",
    "guanajuato","querétaro","morelos","tlaxcala","tabasco",
    "campeche","yucatán","quintana roo","baja california",
    "nayarit","colima","zacatecas","san luis potosí",
    "aguas
