import heapq
import logging
import math
import random
import threading
from typing import Dict, List, Optional, Tuple

from config import ARUBA_BOUNDS, ARUBA_LANDMARKS, CENTRO_ARUBA
from inventario_aruba import InventarioAruba
from osrm_client import osrm_route

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


def snap_a_carretera(lat: float, lon: float) -> Tuple[float, float]:
    """Snap an arbitrary coordinate to the nearest node on the road network.

    Uses only the local inventory road graph (no HTTP call) so it is safe
    to call from hot paths while locks are held. The OSRM/Valhalla route
    engines will further refine the snap when computing the full route.
    Returns the original coordinate unchanged if the graph is not yet loaded.
    """
    try:
        nodos, _ = _obtener_grafo()
        nodo = _buscar_nodo_cercano((lat, lon), nodos)
        if nodo:
            return nodo
    except Exception as exc:
        logger.debug("[snap] Grafo nearest fallo: %s", exc)
    return (lat, lon)

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
    # Always try OSRM/Valhalla first (osrm_route falls back to Valhalla internally)
    try:
        ruta_api = osrm_route(origen, destino)
        if ruta_api and len(ruta_api) >= 2:
            return ruta_api
    except Exception as exc:
        logger.warning("[rutas] API de enrutamiento fallo: %s", exc)

    # Fallback: Dijkstra over the inventory road graph
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

def punto_landmark_aleatorio() -> Tuple[float, float]:
    """Random landmark without jitter (landmarks are already near roads)."""
    if not ARUBA_LANDMARKS:
        return CENTRO_ARUBA
    nombre, lat, lon = random.choice(ARUBA_LANDMARKS)
    return (lat, lon)


def spawn_en_carretera() -> Tuple[float, float]:
    """Return a position guaranteed to be on the Aruba road network.

    Road graph nodes are intersections/endpoints from the inventory API,
    so they are exact road coordinates. Falls back to landmarks if the
    graph is not yet loaded.
    """
    try:
        nodos, _ = _obtener_grafo()
        if nodos:
            return random.choice(list(nodos.values()))
    except Exception as exc:
        logger.warning("[spawn] No se pudo obtener nodo de carretera: %s", exc)
    return punto_landmark_aleatorio()


def generar_ruta_patrulla(origen: Optional[Tuple[float, float]] = None) -> List[List[float]]:
    """Generate a multi-stop patrol route, always starting on a road node."""
    if not origen:
        origen = spawn_en_carretera()

    nodos, _ = _obtener_grafo()
    cantidad = random.randint(2, 4)

    destinos: List[Tuple[float, float]] = []
    if nodos:
        candidatos = list(nodos.values())
        random.shuffle(candidatos)
        # Exclude the origin itself to avoid zero-length segments
        destinos = [c for c in candidatos if c != origen][:cantidad]
    if not destinos:
        destinos = [punto_landmark_aleatorio() for _ in range(cantidad)]

    ruta_total: List[List[float]] = []
    actual = origen
    for destino in destinos:
        tramo = generar_ruta(actual, destino)
        if not tramo or len(tramo) < 2:
            continue
        if not ruta_total:
            # First segment: include the snapped start point (ruta[0]) as origin
            ruta_total.extend(tramo)
        else:
            # Subsequent segments: skip duplicate connection point
            ruta_total.extend(tramo[1:])
        # Use the actual snapped end of the route as next origin
        actual = (tramo[-1][0], tramo[-1][1])

    if not ruta_total:
        ruta_total = [list(origen)]

    return ruta_total

def obtener_distancia_total_ruta(ruta: List[List[float]]) -> float:
    if not ruta or len(ruta) < 2:
        return 0.0

    total = 0.0
    for i in range(len(ruta) - 1):
        total += _haversine((ruta[i][0], ruta[i][1]), (ruta[i + 1][0], ruta[i + 1][1]))
    return total
