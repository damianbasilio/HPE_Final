"""Cliente del nodo de salud de la isla para historial clinico en tiempo real.

Flujo:
  1. Cuando una ambulancia es asignada a un incidente medico, se llama a
     `obtener_historial(paciente_id, incidente)`.
  2. El cliente intenta consultar el nodo de salud via la Inventory API
     (mismo host, endpoint /api/v1/health/patients/<id>).
  3. Si el nodo no responde o el paciente no tiene historial registrado,
     se genera un historial simulado realista para no detener la operativa.

El modulo es *stateless* para que pueda ser usado tanto por el sistema en
tiempo real como por los replays (SimulacionReplay tiene su propio contexto).

Extensibilidad:
  Cualquier tipo de vehiculo puede llamar a `obtener_contexto_mision` para
  obtener informacion relevante segun su tipo (historial clinico para
  ambulancias, informe tecnico de instalacion para bomberos, etc.)
"""

from __future__ import annotations

import logging
import random
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from config import API_INVENTARIO_URL, CACHE_ENTORNO

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------

_HEALTH_BASE_URL = API_INVENTARIO_URL.rstrip('/')
_TIMEOUT_S = 4.0          # timeout HTTP por peticion
_CACHE_TTL_S = 120        # segundos que se reutiliza un historial en cache

# Cache thread-safe: {paciente_id -> (timestamp_fetch, historial_dict)}
_cache: dict = {}
_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Nombres, alergenos y farmacos usados en el fallback simulado
# ---------------------------------------------------------------------------

_NOMBRES = [
    "R. Tromp", "M. Koolman", "J. Wever", "A. Croes", "L. Thijsen",
    "S. Maduro", "F. Briezen", "C. Oduber", "D. Lacle", "E. Geerman",
]

_ALERGIAS = [
    "penicilina", "aspirina", "ibuprofeno", "latex", "contraste yodado",
    "sulfamidas", "codeina", "amlodipino", "metamizol", "ninguna conocida",
]

_MEDICACION = [
    "atorvastatina 40 mg/dia",
    "metformina 850 mg/12h",
    "enalapril 10 mg/dia",
    "omeprazol 20 mg/dia",
    "levotiroxina 50 mcg/dia",
    "bisoprolol 5 mg/dia",
    "apixaban 5 mg/12h",
    "salbutamol inhalador PRN",
    "insulina glargina 20 UI/noche",
    "furosemida 40 mg/dia",
]

_ANTECEDENTES = [
    "hipertension arterial",
    "diabetes mellitus tipo 2",
    "epoc moderada",
    "insuficiencia cardiaca",
    "fibrilacion auricular",
    "asma bronquial",
    "enfermedad renal cronica",
    "cardiopatia isquemica",
    "ictus previo",
    "anemia ferropenica",
]

_GRUPO_SANGUINEO = ["A+", "A-", "B+", "B-", "AB+", "AB-", "0+", "0-"]


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _simular_historial(paciente_id: str, incidente: Optional[dict]) -> dict:
    """Genera un historial clinico simulado pero coherente.

    Usa el paciente_id como semilla para que el mismo paciente siempre
    genere el mismo historial dentro de la misma ejecucion.
    """
    rng = random.Random(hash(paciente_id) & 0xFFFFFFFF)

    n_alergias = rng.randint(0, 2)
    n_medicacion = rng.randint(0, 3)
    n_antecedentes = rng.randint(0, 3)

    alergias = rng.sample(_ALERGIAS, min(n_alergias, len(_ALERGIAS)))
    if not alergias:
        alergias = ["ninguna conocida"]

    medicacion = rng.sample(_MEDICACION, min(n_medicacion, len(_MEDICACION)))
    antecedentes = rng.sample(_ANTECEDENTES, min(n_antecedentes, len(_ANTECEDENTES)))

    edad = rng.randint(18, 90)
    sexo = rng.choice(["M", "F"])
    nombre = rng.choice(_NOMBRES)
    grupo = rng.choice(_GRUPO_SANGUINEO)

    # Notas clinicas relevantes segun tipo de incidente
    tipo_inc = (incidente or {}).get('incident_type', '')
    notas = []
    if 'medical' in str(tipo_inc).lower() or 'cardiac' in str(tipo_inc).lower():
        if 'cardiopatia isquemica' in antecedentes or 'fibrilacion auricular' in antecedentes:
            notas.append("Paciente con antecedentes cardiacos: valorar ECG urgente.")
        if 'apixaban 5 mg/12h' in medicacion:
            notas.append("Anticoagulado con apixaban: riesgo hemorragico elevado.")
    if 'epoc moderada' in antecedentes:
        notas.append("EPOC: evitar O2 >28% sin gasometria previa.")
    if 'diabetes mellitus tipo 2' in antecedentes:
        notas.append("Diabetico: descartar hipoglucemia como causa.")

    return {
        "paciente_id": paciente_id,
        "nombre": nombre,
        "edad": edad,
        "sexo": sexo,
        "grupo_sanguineo": grupo,
        "alergias": alergias,
        "medicacion_activa": medicacion,
        "antecedentes": antecedentes,
        "notas_clinicas": notas,
        "fuente": "simulado",
        "obtenido_en": _ahora_iso(),
    }


def _fetch_nodo_salud(paciente_id: str) -> Optional[dict]:
    """Intenta obtener el historial clinico real del nodo de salud de la isla.

    El nodo de salud expone sus datos a traves del mismo servidor de la
    Inventory API (mismo host, puerto 8080) bajo el prefijo /api/v1/health.
    Si el endpoint no existe o no responde, devuelve None para que el
    llamador caiga al fallback simulado.
    """
    url = f"{_HEALTH_BASE_URL}/api/v1/health/patients/{paciente_id}"
    try:
        resp = requests.get(url, timeout=_TIMEOUT_S)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                data.setdefault('fuente', 'nodo_salud')
                data.setdefault('obtenido_en', _ahora_iso())
                return data
        elif resp.status_code == 404:
            logger.debug("[HistorialClin] Paciente %s no encontrado en nodo salud", paciente_id)
        else:
            logger.warning(
                "[HistorialClin] Nodo salud devolvio HTTP %s para paciente %s",
                resp.status_code, paciente_id,
            )
    except requests.exceptions.Timeout:
        logger.warning("[HistorialClin] Timeout al consultar nodo salud (paciente %s)", paciente_id)
    except requests.exceptions.ConnectionError:
        logger.debug("[HistorialClin] Nodo salud no accesible (ConnectionError)")
    except Exception as exc:
        logger.warning("[HistorialClin] Error consultando nodo salud: %s", exc)
    return None


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------

def obtener_historial(paciente_id: str,
                      incidente: Optional[dict] = None,
                      forzar_real: bool = False) -> dict:
    """Devuelve el historial clinico del paciente.

    1. Busca en cache local (TTL = CACHE_TTL_S).
    2. Intenta el nodo de salud de la isla (HTTP).
    3. Si no hay respuesta, genera un historial simulado.

    Args:
        paciente_id: identificador del paciente (puede ser el incident_id
                     cuando el paciente aun no esta identificado).
        incidente:   dict del incidente para contextualizar las notas clinicas.
        forzar_real: si True, omite la cache y solo intenta el nodo real
                     (sin fallback simulado). Util para depuracion.

    Returns:
        dict con keys: paciente_id, nombre, edad, sexo, grupo_sanguineo,
        alergias, medicacion_activa, antecedentes, notas_clinicas,
        fuente ('nodo_salud' | 'simulado'), obtenido_en.
    """
    pid = str(paciente_id or 'desconocido').strip()

    # Cache
    ahora = time.monotonic()
    with _cache_lock:
        entrada = _cache.get(pid)
        if entrada and not forzar_real:
            ts, historial = entrada
            if ahora - ts < _CACHE_TTL_S:
                return dict(historial)

    # Intento real
    historial = _fetch_nodo_salud(pid)

    if historial is None:
        if forzar_real:
            return {}
        historial = _simular_historial(pid, incidente)

    with _cache_lock:
        _cache[pid] = (ahora, historial)

    return dict(historial)


def obtener_contexto_mision(tipo_vehiculo: str,
                            incidente: Optional[dict] = None,
                            vehiculo_id: str = '') -> dict:
    """Devuelve el contexto de mision relevante segun el tipo de vehiculo.

    Pensado para ser extensible a todos los tipos de vehiculo:
      - ambulancia  -> historial clinico del paciente
      - bomberos    -> informe de la instalacion (POI del inventario)
      - policia     -> historial de incidentes en la zona
      - proteccion_civil -> datos meteo/infraestructura de la zona
      - dron        -> datos del area de vuelo

    Por ahora implementa completamente 'ambulancia' y devuelve una
    estructura basica para el resto.
    """
    if not isinstance(incidente, dict):
        incidente = {}

    tipo = str(tipo_vehiculo or '').lower()

    if tipo == 'ambulancia':
        pid = (
            incidente.get('paciente_id')
            or incidente.get('patient_id')
            or incidente.get('incident_id')
            or f"PAC-{incidente.get('id', 'desconocido')}"
        )
        historial = obtener_historial(pid, incidente=incidente)
        return {
            "tipo_contexto": "historial_clinico",
            "vehiculo_id": vehiculo_id,
            "incidente_id": incidente.get('incident_id'),
            "historial": historial,
        }

    if tipo == 'bomberos':
        poi_id = incidente.get('poi_id') or incidente.get('location_id')
        return {
            "tipo_contexto": "informe_instalacion",
            "vehiculo_id": vehiculo_id,
            "incidente_id": incidente.get('incident_id'),
            "poi_id": poi_id,
            "nota": "Consulta el inventario /api/v1/pois/{id} para datos de la instalacion.",
        }

    if tipo == 'policia':
        return {
            "tipo_contexto": "informe_zona",
            "vehiculo_id": vehiculo_id,
            "incidente_id": incidente.get('incident_id'),
            "nota": "Consulta el inventario /api/v1/roads para estado de la red vial.",
        }

    return {
        "tipo_contexto": "generico",
        "vehiculo_id": vehiculo_id,
        "incidente_id": incidente.get('incident_id'),
    }


def invalidar_cache(paciente_id: str) -> None:
    """Elimina la entrada del paciente de la cache local."""
    with _cache_lock:
        _cache.pop(str(paciente_id), None)
