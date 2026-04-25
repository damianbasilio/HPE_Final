import logging
import threading
import time
from collections import OrderedDict
from typing import List, Optional, Tuple

import requests

from config import OSRM_PROFILE, OSRM_TIMEOUT, OSRM_URL

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_cache: "OrderedDict[tuple, list]" = OrderedDict()
_MAX_CACHE = 1000

# OSRM state with time-based cooldown (never permanently disabled)
_osrm_disabled_until: float = 0.0
_OSRM_COOLDOWN = 90.0  # seconds before retrying after failure

# Valhalla public instance (OpenStreetMap routing)
_VALHALLA_URL = "https://valhalla1.openstreetmap.de"
_valhalla_disabled_until: float = 0.0
_VALHALLA_COOLDOWN = 120.0


def osrm_disponible() -> bool:
    return bool(OSRM_URL) and time.time() >= _osrm_disabled_until


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


def _decode_polyline6(encoded: str) -> List[List[float]]:
    """Decode Valhalla polyline6 encoding to list of [lat, lon] pairs."""
    coords: List[List[float]] = []
    index = 0
    lat = 0
    lng = 0
    length = len(encoded)
    while index < length:
        result, shift = 0, 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        lat += (~(result >> 1) if result & 1 else result >> 1)

        result, shift = 0, 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        lng += (~(result >> 1) if result & 1 else result >> 1)
        coords.append([lat / 1e6, lng / 1e6])
    return coords


def _valhalla_route(
    origen: Tuple[float, float], destino: Tuple[float, float]
) -> Optional[List[List[float]]]:
    global _valhalla_disabled_until

    if time.time() < _valhalla_disabled_until:
        return None

    body = {
        "locations": [
            {"lon": float(origen[1]), "lat": float(origen[0])},
            {"lon": float(destino[1]), "lat": float(destino[0])},
        ],
        "costing": "auto",
        "directions_options": {"units": "km"},
    }

    try:
        resp = requests.post(
            f"{_VALHALLA_URL}/route",
            json=body,
            timeout=OSRM_TIMEOUT + 3,
        )
    except Exception as exc:
        logger.warning("[Valhalla] fallo de conexion: %s", exc)
        _valhalla_disabled_until = time.time() + _VALHALLA_COOLDOWN
        return None

    if resp.status_code != 200:
        logger.warning("[Valhalla] HTTP %s: %s", resp.status_code, resp.text[:200])
        return None

    try:
        data = resp.json()
        shape = data["trip"]["legs"][0]["shape"]
        coords = _decode_polyline6(shape)
        if len(coords) >= 2:
            logger.debug("[Valhalla] ruta OK: %d puntos", len(coords))
            return coords
    except (KeyError, IndexError, TypeError, Exception) as exc:
        logger.warning("[Valhalla] error parseando respuesta: %s", exc)

    return None


def osrm_route(
    origen: Tuple[float, float], destino: Tuple[float, float]
) -> Optional[List[List[float]]]:
    global _osrm_disabled_until

    k = _cache_key(origen, destino)
    cached = _get_cache(k)
    if cached is not None:
        return cached

    # Try OSRM first
    if osrm_disponible():
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
            logger.warning("[OSRM] fallo de conexion (%s): %s — intentando Valhalla", url, exc)
            _osrm_disabled_until = time.time() + _OSRM_COOLDOWN
            return _valhalla_route(origen, destino)

        if resp.status_code == 200:
            try:
                data = resp.json()
                if data.get("code") == "Ok" and data.get("routes"):
                    coordinates = (
                        (data["routes"][0].get("geometry") or {}).get("coordinates") or []
                    )
                    if len(coordinates) >= 2:
                        ruta = [[float(c[1]), float(c[0])] for c in coordinates]
                        _set_cache(k, ruta)
                        return ruta
            except Exception as exc:
                logger.warning("[OSRM] error parseando respuesta: %s", exc)

        logger.warning("[OSRM] sin ruta valida (HTTP %s) — intentando Valhalla", resp.status_code)

    # Fallback to Valhalla
    ruta = _valhalla_route(origen, destino)
    if ruta and len(ruta) >= 2:
        _set_cache(k, ruta)
    return ruta


def osrm_estado() -> dict:
    return {
        "url": OSRM_URL,
        "profile": OSRM_PROFILE,
        "disponible": osrm_disponible(),
        "osrm_disabled_until": _osrm_disabled_until,
        "valhalla_url": _VALHALLA_URL,
        "valhalla_disponible": time.time() >= _valhalla_disabled_until,
    }


def warmup() -> None:
    """Pre-warm OSRM / Valhalla route cache between two known Aruba landmarks."""
    from config import ARUBA_LANDMARKS

    if len(ARUBA_LANDMARKS) < 2:
        logger.warning("[Warmup] No hay suficientes landmarks para calentar la cache")
        return

    a = (ARUBA_LANDMARKS[0][1], ARUBA_LANDMARKS[0][2])
    b = (ARUBA_LANDMARKS[1][1], ARUBA_LANDMARKS[1][2])

    try:
        ruta = osrm_route(a, b)
        if ruta and len(ruta) >= 2:
            logger.info("[Warmup] Ruta OK: %d puntos entre %s y %s",
                        len(ruta), ARUBA_LANDMARKS[0][0], ARUBA_LANDMARKS[1][0])
        else:
            logger.warning("[Warmup] Sin ruta disponible entre landmarks")
    except Exception as exc:
        logger.warning("[Warmup] Error: %s", exc)
