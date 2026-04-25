import logging
import threading
import time
from typing import Dict, List, Optional

import requests

from config import API_INVENTARIO_URL, CACHE_ENTORNO

logger = logging.getLogger(__name__)


class InventarioAruba:
    def __init__(self, base_url: str = API_INVENTARIO_URL, cache_ttl: int = CACHE_ENTORNO):
        self.base_url = base_url.rstrip("/")
        self.cache_ttl = cache_ttl
        self._lock = threading.Lock()
        self._cache: Dict[str, List[dict]] = {
            "stations": [],
            "pois": [],
            "roads": []
        }
        self._cache_time: Dict[str, float] = {
            "stations": 0.0,
            "pois": 0.0,
            "roads": 0.0
        }

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{path}"
        resp = requests.get(url, params=params or {}, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _fetch_paginated(self, path: str, limit: int) -> List[dict]:
        items: List[dict] = []
        offset = 0

        while True:
            payload = self._get(path, params={"limit": limit, "offset": offset})
            page_items = payload.get("items", [])
            items.extend(page_items)

            if not payload.get("has_more"):
                break

            offset += payload.get("limit", limit)

        return items

    def _get_cached(self, key: str, fetch_fn) -> List[dict]:
        ahora = time.time()
        with self._lock:
            if ahora - self._cache_time.get(key, 0) < self.cache_ttl and self._cache.get(key):
                return list(self._cache[key])

        try:
            datos = fetch_fn()
            with self._lock:
                self._cache[key] = list(datos)
                self._cache_time[key] = ahora
            return list(datos)
        except Exception as exc:
            logger.warning("Inventario Aruba fallo en %s: %s", key, exc)
            with self._lock:
                return list(self._cache.get(key, []))

    def obtener_estaciones(self) -> List[dict]:
        return self._get_cached("stations", lambda: self._fetch_paginated("/api/v1/stations", limit=20))

    def obtener_pois(self) -> List[dict]:
        return self._get_cached("pois", lambda: self._fetch_paginated("/api/v1/pois", limit=50))

    def obtener_carreteras(self) -> List[dict]:
        return self._get_cached("roads", lambda: self._fetch_paginated("/api/v1/roads", limit=20))

    def obtener_estacion(self, station_id: str) -> Optional[dict]:
        try:
            return self._get(f"/api/v1/stations/{station_id}")
        except Exception as exc:
            logger.warning("No se pudo obtener estacion %s: %s", station_id, exc)
            return None

    def obtener_poi(self, poi_id: str) -> Optional[dict]:
        try:
            return self._get(f"/api/v1/pois/{poi_id}")
        except Exception as exc:
            logger.warning("No se pudo obtener poi %s: %s", poi_id, exc)
            return None

    def obtener_carretera(self, road_id: str) -> Optional[dict]:
        try:
            return self._get(f"/api/v1/roads/{road_id}")
        except Exception as exc:
            logger.warning("No se pudo obtener carretera %s: %s", road_id, exc)
            return None

    def refrescar_todo(self) -> None:
        self.obtener_estaciones()
        self.obtener_pois()
        self.obtener_carreteras()
