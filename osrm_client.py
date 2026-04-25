import logging
import threading
from collections import OrderedDict
from typing import List, Optional, Tuple

import requests

from config import OSRM_PROFILE, OSRM_TIMEOUT, OSRM_URL

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_cache: "OrderedDict[tuple, list]" = OrderedDict()
_MAX_CACHE = 1000

_disabled = False

def osrm_disponible() -> bool:
    return bool(OSRM_URL) and not _disabled

def _cache_key(origen: Tuple[float, float], destino: Tuple[float, float]) -> tuple:
    return (
        round(float(origen[0]), 4),
        round(float(origen[1]), 4),
        round(float(destino[0]), 4),
        round(float(destino[1]), 4),
    )

def _set_cache(k: tuple, ruta: List[List[float]]) -> None:
    with _lock:
        if len(_cache) >= _MAX_CACHE:
            _cache.popitem(last=False)
        _cache[k] = list(ruta)

def _get_cache(k: tuple) -> Optional[List[List[float]]]:
    with _lock:
        valor = _cache.get(k)
        if valor is None:
            return None
        _cache.move_to_end(k)
        return list(valor)

def osrm_route(origen: Tuple[float, float], destino: Tuple[float, float]) -> Optional[List[List[float]]]:
    global _disabled

    if not osrm_disponible():
        return None

    k = _cache_key(origen, destino)
    cached = _get_cache(k)
    if cached is not None:
        return cached

    coords = f"{origen[1]:.6f},{origen[0]:.6f};{destino[1]:.6f},{destino[0]:.6f}"
    url = f"{OSRM_URL.rstrip('/')}/route/v1/{OSRM_PROFILE}/{coords}"
    params = {
        "overview": "full",
        "geometries": "geojson",
        "steps": "false",
        "alternatives": "false",
    }

    try:
        resp = requests.get(url, params=params, timeout=OSRM_TIMEOUT)
    except Exception as exc:
        logger.warning("OSRM fallo (%s): %s", url, exc)
        _disabled = True
        return None

    if resp.status_code != 200:
        logger.warning("OSRM HTTP %s para %s -> %s", resp.status_code, origen, destino)
        return None

    try:
        data = resp.json()
    except Exception as exc:
        logger.warning("OSRM respuesta no JSON: %s", exc)
        return None

    if data.get("code") != "Ok" or not data.get("routes"):
        return None

    coordinates = (data["routes"][0].get("geometry") or {}).get("coordinates") or []
    if len(coordinates) < 2:
        return None

    ruta = [[float(c[1]), float(c[0])] for c in coordinates]
    _set_cache(k, ruta)
    return ruta
