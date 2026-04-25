import os
from dotenv import load_dotenv

load_dotenv()

CLAVE_FLASK = os.getenv('FLASK_SECRET_KEY', os.urandom(24).hex())
DEPURACION_FLASK = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
HOST_SERVIDOR = os.getenv('HOST_SERVIDOR', '0.0.0.0')
PUERTO_SERVIDOR = int(os.getenv('PUERTO_SERVIDOR', 8080))

API_INVENTARIO_URL = os.getenv('ARUBA_INVENTORY_API', 'http://10.10.48.30:8080')
API_EQUIPO_URL = os.getenv('ARUBA_TEAM_API', 'http://10.10.48.21:8080')

OSRM_URL = os.getenv('OSRM_URL', 'https://router.project-osrm.org')
OSRM_PROFILE = os.getenv('OSRM_PROFILE', 'driving')
OSRM_TIMEOUT = float(os.getenv('OSRM_TIMEOUT', 6))

TEAM_ID = os.getenv('TEAM_ID', '52sec')

LLM_BASE_URL = os.getenv('LLM_BASE_URL', 'http://10.10.48.10:8001/v1')
LLM_API_KEY = os.getenv('LLM_API_KEY', 'dummy')
LLM_MODEL = os.getenv('LLM_MODEL', 'Qwen/Qwen3-235B-A22B')
LLM_TEMPERATURA = float(os.getenv('LLM_TEMPERATURA', 0.6))
LLM_TOP_P = float(os.getenv('LLM_TOP_P', 0.95))
LLM_MAX_TOKENS = int(os.getenv('LLM_MAX_TOKENS', 1024))
LLM_THINKING = os.getenv('LLM_THINKING', 'false').lower() == 'true'

INTERVALO_ACTUALIZACION = float(os.getenv('INTERVALO_SIM', 0.5))

INTERVALO_TELEMETRIA = float(os.getenv('INTERVALO_TELEMETRIA', 7.5))

RANGO_COMBUSTIBLE_INICIAL = (60, 95)
RANGO_KM_INICIAL = (15000, 80000)
RANGO_TEMP_INICIAL = (65, 75)
RANGO_ACEITE_INICIAL = (80, 100)
RANGO_DESGASTE_FRENOS = (20, 60)
RANGO_DESGASTE_NEUMATICOS = (30, 70)

VELOCIDAD_PATRULLA = int(os.getenv('VELOCIDAD_PATRULLA', 35))
TASA_REABASTECIMIENTO = float(os.getenv('TASA_REABASTECIMIENTO', 2.0))
UMBRAL_COMBUSTIBLE = int(os.getenv('UMBRAL_COMBUSTIBLE', 15))
TEMP_AMBIENTE = float(os.getenv('TEMP_AMBIENTE', 25.0))
TEMP_MAXIMA = int(os.getenv('TEMP_MAXIMA', 120))
VELOCIDAD_MAXIMA = int(os.getenv('VELOCIDAD_MAXIMA', 200))

CENTRO_ARUBA = (12.5211, -69.9683)

ARUBA_BOUNDS = (
    float(os.getenv('ARUBA_LAT_MIN', 12.4)),
    float(os.getenv('ARUBA_LAT_MAX', 12.7)),
    float(os.getenv('ARUBA_LON_MIN', -70.1)),
    float(os.getenv('ARUBA_LON_MAX', -69.8))
)

ARUBA_LANDMARKS = [
    ("Oranjestad",          12.5240, -70.0270),
    ("Aeropuerto Reina Beatrix", 12.5014, -70.0152),
    ("Eagle Beach",         12.5538, -70.0518),
    ("Palm Beach",          12.5762, -70.0489),
    ("Noord",               12.5870, -70.0411),
    ("Hadicurari",          12.5818, -70.0469),
    ("California Lighthouse", 12.6164, -70.0488),
    ("Santa Cruz",          12.5363, -69.9628),
    ("Paradera",            12.5197, -69.9851),
    ("Tanki Leendert",      12.5474, -70.0089),
    ("Savaneta",            12.4517, -69.9281),
    ("Pos Chiquito",        12.4839, -69.9519),
    ("San Nicolas",         12.4350, -69.9100),
    ("Seroe Colorado",      12.4283, -69.8836),
    ("Sint Cruz",           12.5120, -69.9750),
    ("Bushiribana",         12.5680, -69.9420),
]

CACHE_ENTORNO = int(os.getenv('CACHE_ENTORNO', 300))

KAFKA_BROKER = os.getenv('KAFKA_BROKER', '10.10.48.30:9092')
KAFKA_USERNAME = os.getenv('KAFKA_USERNAME', '52sec')
KAFKA_PASSWORD = os.getenv('KAFKA_PASSWORD', '')
KAFKA_SECURITY_PROTOCOL = os.getenv('KAFKA_SECURITY_PROTOCOL', 'SASL_PLAINTEXT')
KAFKA_SASL_MECHANISM = os.getenv('KAFKA_SASL_MECHANISM', 'PLAIN')
KAFKA_TOPIC_TELEMETRIA = os.getenv('KAFKA_TOPIC_TELEMETRIA', 'aruba.team.52sec')
KAFKA_TOPIC_CLIMA = os.getenv('KAFKA_TOPIC_CLIMA', 'aruba.weather')
KAFKA_TOPIC_EVENTOS = os.getenv('KAFKA_TOPIC_EVENTOS', 'aruba.events')

TIEMPO_SESION = int(os.getenv('TIEMPO_SESION', 3600))

MAX_PUNTOS_RASTRO = int(os.getenv('MAX_PUNTOS_RASTRO', 100))

DIST_MAX_RASTRO = float(os.getenv('DIST_MAX_RASTRO', 0.5))

CACHE_ESTATICOS = int(os.getenv('CACHE_ESTATICOS', 31536000))

_cors_raw = os.getenv('CORS_ORIGENES', '*')
CORS_ORIGENES = _cors_raw if _cors_raw == '*' else [o.strip() for o in _cors_raw.split(',')]

COOKIE_HTTPONLY = True
COOKIE_SAMESITE = 'Lax'

TIPOS_VEHICULO_VALIDOS = (
    'policia', 'ambulancia', 'bomberos', 'proteccion_civil', 'dron'
)

from costos import obtener_tarifa  
