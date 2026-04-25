import heapq
import logging
import math
import random
import threading
from typing import Dict, List, Optional, Tuple

from config import ARUBA_BOUNDS, CENTRO_ARUBA
from inventario_aruba import InventarioAruba

logger = logging.getLogger(__name__)

inventario = InventarioAruba()

_grafo_lock = threading.Lock()
_grafo_cache = {
    "nodos": {},
    "adyacencia": {},
    "timestamp": 0.0
}

def _haversine(punto1: Tuple[float, float], punto2: Tuple[float, float]) -> float:

    lat1, lon1 = math.radians(punto1[0]), math.radians(punto1[1])
    lat2, lon2 = math.radians(punto2[0]), math.radians(punto2[1])

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return 6371 * c

def _coord_key(lat: float, lon: float) -> Tuple[float, float]:
    return (round(lat, 6), round(lon, 6))

def _construir_grafo(roads: List[dict]) -> Tuple[Dict[Tuple[float, float], Tuple[float, float]], Dict]:
    nodos: Dict[Tuple[float, float], Tuple[float, float]] = {}
    adyacencia: Dict[Tuple[float, float], List[Tuple[Tuple[float, float], float]]] = {}

    for road in roads:
        try:
            start = _coord_key(road.get("start_lat"), road.get("start_lon"))
            end = _coord_key(road.get("end_lat"), road.get("end_lon"))

            if start not in nodos:
                nodos[start] = start
            if end not in nodos:
                nodos[end] = end

            longitud_km = (road.get("length_m") or 0) / 1000.0
            if longitud_km <= 0:
                longitud_km = _haversine(start, end)

            adyacencia.setdefault(start, []).append((end, longitud_km))
            adyacencia.setdefault(end, []).append((start, longitud_km))
        except Exception:
            continue

    return nodos, adyacencia

def _obtener_grafo() -> Tuple[Dict, Dict]:
    with _grafo_lock:
        if _grafo_cache["nodos"] and _grafo_cache["adyacencia"]:
            return _grafo_cache["nodos"], _grafo_cache["adyacencia"]

    roads = inventario.obtener_carreteras()
    nodos, adyacencia = _construir_grafo(roads)

    with _grafo_lock:
        _grafo_cache["nodos"] = nodos
        _grafo_cache["adyacencia"] = adyacencia

    return nodos, adyacencia

def _buscar_nodo_cercano(punto: Tuple[float, float], nodos: Dict) -> Optional[Tuple[float, float]]:
    if not nodos:
        return None

    mejor = None
    mejor_dist = float("inf")

    for nodo in nodos.values():
        dist = _haversine(punto, nodo)
        if dist < mejor_dist:
            mejor_dist = dist
            mejor = nodo

    return mejor

def _dijkstra(adyacencia: Dict, origen: Tuple[float, float], destino: Tuple[float, float]) -> List[Tuple[float, float]]:
    distancias = {origen: 0.0}
    anteriores = {}
    heap = [(0.0, origen)]

    while heap:
        distancia_actual, nodo_actual = heapq.heappop(heap)
        if nodo_actual == destino:
            break
        if distancia_actual > distancias.get(nodo_actual, float("inf")):
            continue

        for vecino, costo in adyacencia.get(nodo_actual, []):
            nueva = distancia_actual + costo
            if nueva < distancias.get(vecino, float("inf")):
                distancias[vecino] = nueva
                anteriores[vecino] = nodo_actual
                heapq.heappush(heap, (nueva, vecino))

    if destino not in distancias:
        return []

    ruta = [destino]
    while ruta[-1] != origen:
        ruta.append(anteriores[ruta[-1]])
    ruta.reverse()
    return ruta

def generar_ruta(origen: Tuple[float, float], destino: Tuple[float, float]) -> List[List[float]]:
    nodos, adyacencia = _obtener_grafo()
    if not nodos:
        return [list(origen), list(destino)]

    nodo_inicio = _buscar_nodo_cercano(origen, nodos)
    nodo_fin = _buscar_nodo_cercano(destino, nodos)
    if not nodo_inicio or not nodo_fin:
        return [list(origen), list(destino)]

    ruta = _dijkstra(adyacencia, nodo_inicio, nodo_fin)
    if not ruta:
        return [list(origen), list(destino)]

    puntos = [list(origen)]
    puntos.extend([[p[0], p[1]] for p in ruta])
    puntos.append(list(destino))
    return puntos

def generar_ruta_patrulla(origen: Optional[Tuple[float, float]] = None) -> List[List[float]]:
    lat_min, lat_max, lon_min, lon_max = ARUBA_BOUNDS
    if not origen:
        origen = (
            random.uniform(lat_min, lat_max),
            random.uniform(lon_min, lon_max)
        )

    destinos = []
    for _ in range(random.randint(2, 4)):
        destinos.append((
            random.uniform(lat_min, lat_max),
            random.uniform(lon_min, lon_max)
        ))

    ruta_total: List[List[float]] = [list(origen)]
    actual = origen
    for destino in destinos:
        tramo = generar_ruta(actual, destino)
        if tramo:
            ruta_total.extend(tramo[1:])
            actual = destino

    return ruta_total

def obtener_distancia_total_ruta(ruta: List[List[float]]) -> float:
    if not ruta or len(ruta) < 2:
        return 0.0

    total = 0.0
    for i in range(len(ruta) - 1):
        total += _haversine((ruta[i][0], ruta[i][1]), (ruta[i + 1][0], ruta[i + 1][1]))
    return total
