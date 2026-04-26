

import logging
import math
import threading
import time
import traceback
import uuid
from collections import deque
from datetime import datetime
from typing import Dict, List, Optional

from config import INTERVALO_ACTUALIZACION
from sabotaje import guardian as _guardian_sabotaje
from costos import (
    SLA_RESPUESTA_SEG,
    clasificar_coste,
    desglose_coste_minuto,
    obtener_tarifa,
    prima_tiempo_respuesta,
    tarifas_completas,
)
from rutas import generar_ruta, obtener_distancia_total_ruta, snap_a_carretera
from vehiculo_factory import crear_vehiculo
from vehiculo_base import VehiculoBase

logger = logging.getLogger(__name__)

MAPA_EVENTO_A_UNIDAD = {
    "fire": "bomberos",
    "hazmat_spill": "bomberos",
    "medical_emergency": "ambulancia",
    "medical_alert": "ambulancia",
    "accident": "policia",
    "traffic_accident": "policia",
    "lane_closure": "policia",
    "road_block": "policia",
    "construction": "policia",
    "storm": "proteccion_civil",
    "flood": "proteccion_civil",
    "public_event": "policia",
    "power_outage": "proteccion_civil",
    "blackout": "proteccion_civil",
    "earthquake": "proteccion_civil",
    "marine_rescue": "proteccion_civil",
}

EVENTOS_CON_SCOUT = {"fire", "flood", "marine_rescue", "earthquake", "hazmat_spill"}

ALIAS_TIPO_EVENTO = {
    "medicalalert": "medical_emergency",
    "medical_alert": "medical_emergency",
    "medical": "medical_emergency",
    "traffic_accident": "accident",
    "collision": "accident",
    "road_block": "lane_closure",
    "roadblock": "lane_closure",
    "blackout": "power_outage",
}

# Plantilla ampliada y mas realista: 6 patrullas + 4 ambulancias + 3 bomberos
# + 3 proteccion civil + 2 drones = 18 unidades. Mezcla combustion / electrico
# en patrullas y ambulancias para que la diferencia de tarifa y consumo se
# aprecie en la simulacion.
PLANTILLA_DEFECTO: List[tuple] = [
    ("policia",          "combustion", "Patrulla 01"),
    ("policia",          "combustion", "Patrulla 02"),
    ("policia",          "combustion", "Patrulla 03"),
    ("policia",          "combustion", "Patrulla 04"),
    ("policia",          "electrico",  "Patrulla 05 EV"),
    ("policia",          "electrico",  "Patrulla 06 EV"),
    ("ambulancia",       "combustion", "Ambulancia 01"),
    ("ambulancia",       "combustion", "Ambulancia 02"),
    ("ambulancia",       "combustion", "Ambulancia 03"),
    ("ambulancia",       "electrico",  "Ambulancia 04 EV"),
    ("bomberos",         "combustion", "Bomberos 01"),
    ("bomberos",         "combustion", "Bomberos 02"),
    ("bomberos",         "electrico",  "Bomberos 03 EV"),
    ("proteccion_civil", "combustion", "PC 01"),
    ("proteccion_civil", "combustion", "PC 02"),
    ("proteccion_civil", "electrico",  "PC 03 EV"),
    ("dron",             "unico",      "Dron 01"),
    ("dron",             "unico",      "Dron 02"),
]

def _duracion_por_severidad(severidad: Optional[str]) -> int:
    if severidad == "critical":
        return 45
    if severidad == "high":
        return 30
    if severidad == "medium":
        return 20
    return 10

def _intensidad_por_severidad(severidad: Optional[str]) -> float:
    if severidad == "critical":
        return 0.9
    if severidad == "high":
        return 0.7
    if severidad == "medium":
        return 0.5
    return 0.3

def _to_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _pair_a_lat_lon(a, b):
    x = _to_float(a)
    y = _to_float(b)
    if x is None or y is None:
        return None, None

    # Detecta pares lon/lat frecuentes y los invierte.
    if abs(x) > 90 and abs(y) <= 90:
        return y, x
    if x < -20 and -90 <= y <= 90:
        return y, x
    return x, y


def _normalizar_tipo_evento(raw_tipo) -> str:
    if raw_tipo is None:
        return "incident"

    tipo = str(raw_tipo).strip().lower().replace("-", "_").replace(" ", "_")
    if not tipo:
        return "incident"
    return ALIAS_TIPO_EVENTO.get(tipo, tipo)


def _normalizar_severidad(raw_sev) -> str:
    if raw_sev is None:
        return "medium"

    if isinstance(raw_sev, (int, float)):
        val = float(raw_sev)
        if val >= 0.9:
            return "critical"
        if val >= 0.7:
            return "high"
        if val >= 0.4:
            return "medium"
        return "low"

    sev = str(raw_sev).strip().lower()
    mapa = {
        "p1": "critical",
        "p2": "high",
        "p3": "medium",
        "p4": "low",
        "critica": "critical",
        "critical": "critical",
        "alta": "high",
        "high": "high",
        "media": "medium",
        "medium": "medium",
        "baja": "low",
        "low": "low",
    }
    return mapa.get(sev, "medium")


def _desenvolver_evento(evento: dict) -> dict:
    """Desempaqueta estructuras anidadas comunes en eventos Kafka de Aruba.

    Itera por hasta 5 niveles buscando la capa que contiene los campos
    semanticos del evento (tipo, coordenadas, etc.). Cubre los patrones mas
    habituales: {payload:{...}}, {data:{...}}, {event:{...}}, {message:{...}},
    {value:{...}} y {body:{...}}.
    """
    if not isinstance(evento, dict):
        return evento

    # Campos que indican que hemos encontrado la capa de datos del evento
    _CAMPOS_SEMANTICOS = frozenset((
        "type", "event_type", "incident_type", "category", "eventType",
        "latitude", "lat", "location", "coordinates", "geometry", "position",
        "geo", "details", "properties",
    ))

    actual = evento
    for _ in range(5):
        siguiente = None
        for key in ("payload", "event", "data", "message", "value", "body"):
            nested = actual.get(key)
            if isinstance(nested, dict) and any(c in nested for c in _CAMPOS_SEMANTICOS):
                siguiente = nested
                break
        if not siguiente:
            break
        actual = siguiente

    return actual


def _extraer_coords(evento: dict):
    """Extrae (lat, lon) de un evento normalizado intentando todos los campos
    que el topic aruba.events puede utilizar.

    Orden de prioridad:
    1. Campos planos: latitude/longitude, lat/lon, y/x
    2. Objeto anidado: location, coords, coordinates, position, point, geo,
       details, properties (buscando lat/lon dentro)
    3. GeoJSON geometry
    4. Lista top-level de dos numeros como [lon, lat] o [lat, lon]
    """
    def _from_dict(d: dict):
        """Extrae lat/lon de un dict con cualquier campo conocido."""
        la = _to_float(d.get('lat') or d.get('latitude') or d.get('y'))
        lo = _to_float(d.get('lon') or d.get('lng') or d.get('longitude') or d.get('x'))
        return la, lo

    # 1. Campos planos en el propio evento
    lat = _to_float(evento.get('latitude'))
    if lat is None:
        lat = _to_float(evento.get('lat'))
    if lat is None:
        lat = _to_float(evento.get('y'))

    lon = _to_float(evento.get('longitude'))
    if lon is None:
        lon = _to_float(evento.get('lon'))
    if lon is None:
        lon = _to_float(evento.get('lng'))
    if lon is None:
        lon = _to_float(evento.get('x'))

    # 2. Objetos anidados habituales (incluyendo 'geo', 'details', 'properties')
    if lat is None or lon is None:
        for key in ('location', 'coords', 'coordinates', 'position', 'point',
                    'geo', 'details', 'properties'):
            loc = evento.get(key)
            if isinstance(loc, dict):
                la, lo = _from_dict(loc)
                if lat is None:
                    lat = la
                if lon is None:
                    lon = lo
                if lat is not None and lon is not None:
                    break
            elif isinstance(loc, (list, tuple)) and len(loc) >= 2:
                p_lat, p_lon = _pair_a_lat_lon(loc[0], loc[1])
                if lat is None:
                    lat = p_lat
                if lon is None:
                    lon = p_lon
                if lat is not None and lon is not None:
                    break

    # 3. GeoJSON geometry (Point / LineString / Polygon)
    if lat is None or lon is None:
        geom = evento.get('geometry')
        if isinstance(geom, dict):
            g_type = str(geom.get('type') or '').lower()
            coords = geom.get('coordinates')
            if isinstance(coords, (list, tuple)) and coords:
                if g_type == 'point' and len(coords) >= 2:
                    # GeoJSON Point: [lon, lat]
                    p_lat, p_lon = _pair_a_lat_lon(coords[1], coords[0])
                    if lat is None:
                        lat = p_lat
                    if lon is None:
                        lon = p_lon
                elif isinstance(coords[0], (list, tuple)) and len(coords[0]) >= 2:
                    # LineString / Polygon: primer punto [lon, lat]
                    p_lat, p_lon = _pair_a_lat_lon(coords[0][1], coords[0][0])
                    if lat is None:
                        lat = p_lat
                    if lon is None:
                        lon = p_lon
        elif isinstance(geom, (list, tuple)) and len(geom) >= 2:
            p_lat, p_lon = _pair_a_lat_lon(geom[0], geom[1])
            if lat is None:
                lat = p_lat
            if lon is None:
                lon = p_lon

    # 4. Lista plana de dos numeros en el propio evento [lon,lat] o [lat,lon]
    if lat is None or lon is None:
        for key in ('latlng', 'latlon', 'lonlat', 'lnglat'):
            raw = evento.get(key)
            if isinstance(raw, (list, tuple)) and len(raw) >= 2:
                p_lat, p_lon = _pair_a_lat_lon(raw[0], raw[1])
                if lat is None:
                    lat = p_lat
                if lon is None:
                    lon = p_lon
                if lat is not None and lon is not None:
                    break

    return lat, lon

class FleetManager:
    def __init__(self, plantilla: Optional[List[tuple]] = None):
        self._lock = threading.RLock()
        self.vehiculos: Dict[str, VehiculoBase] = {}

        self.incidentes: Dict[str, dict] = {}

        self.asignaciones: Dict[str, str] = {}
        self._traza: deque = deque(maxlen=200)

        self.historial_costes: deque = deque(maxlen=200)
        self._generador_demo = None
        self._crear_flotas_base(plantilla or PLANTILLA_DEFECTO)

    def activar_auto_demo(self, bus=None,
                          intervalo_min_s: float = 60.0,
                          intervalo_max_s: float = 120.0,
                          silencio_kafka_s: float = 90.0,
                          max_activos: int = 6):
        """Arranca el generador de incidentes ficticios para mantener la demo viva.

        Devuelve el generador (con metodo `notificar_evento_real`) para que el
        wrapper del bus Kafka pueda avisar cuando llegan eventos reales y el
        generador se mantenga en silencio mientras los haya.
        """
        if self._generador_demo is not None:
            return self._generador_demo
        try:
            from generador_incidentes import GeneradorIncidentesDemo
        except Exception as exc:
            logger.warning("[Flota] No se pudo importar el generador demo: %s", exc)
            return None
        self._generador_demo = GeneradorIncidentesDemo(
            self, bus,
            intervalo_min_s=intervalo_min_s,
            intervalo_max_s=intervalo_max_s,
            silencio_kafka_s=silencio_kafka_s,
            max_activos=max_activos,
        )
        self._generador_demo.iniciar()
        return self._generador_demo

    def generador_demo(self):
        return self._generador_demo

    def agregar_unidad(self, tipo: str, propulsion: str = 'combustion',
                        nombre: Optional[str] = None) -> Optional[dict]:
        """Anade una nueva unidad operativa al gemelo digital.

        Devuelve un dict con `id` y `nombre` cuando tiene exito, o `None`
        si el tipo/propulsion no se puede instanciar.
        """
        with self._lock:
            tipo_norm = str(tipo or '').strip().lower()
            prop_norm = str(propulsion or '').strip().lower() or 'combustion'
            if not tipo_norm:
                return None
            if not nombre:
                ya = sum(1 for v in self.vehiculos.values() if v.TIPO == tipo_norm) + 1
                nombre = f"{tipo_norm.capitalize()} {ya:02d}"
            veh = self._instanciar_unidad(tipo_norm, prop_norm, nombre)
            if not veh:
                return None
            return {
                'id': veh.id,
                'tipo': veh.TIPO,
                'propulsion': veh.propulsion,
                'nombre': veh.metadatos.get('nombre', veh.id),
            }

    def eliminar_unidad(self, vehiculo_id: str) -> bool:
        """Retira una unidad de la flota. Si esta en intervencion la cierra primero."""
        with self._lock:
            veh = self.vehiculos.pop(vehiculo_id, None)
            if not veh:
                return False
            inc_id = self.asignaciones.pop(vehiculo_id, None)
            if inc_id and inc_id in self.incidentes:
                inc = self.incidentes[inc_id]
                inc['status'] = 'cancelled'
                inc['incident_status'] = 'cancelled'
                inc['resolved_at'] = datetime.now().isoformat()
            try:
                veh.terminar_escenario()
            except Exception:
                pass
            return True

    def _registrar_traza(self, evento_id: str, decision: str, motivo: str = "",
                         tipo: Optional[str] = None, payload: Optional[dict] = None) -> None:
        try:
            self._traza.append({
                "ts": datetime.now().isoformat(),
                "evento_id": evento_id,
                "tipo": tipo,
                "decision": decision,
                "motivo": motivo[:300] if motivo else "",
                "payload": payload,
            })
        except Exception:
            pass

    def traza_eventos(self, limite: int = 50) -> List[dict]:
        return list(self._traza)[-limite:]

    def _crear_flotas_base(self, plantilla: List[tuple]) -> None:
        for tipo, energia, nombre in plantilla:
            self._instanciar_unidad(tipo, energia, nombre)

    def _instanciar_unidad(self, tipo: str, energia: str, nombre: str) -> Optional[VehiculoBase]:
        try:
            vid = f"{tipo}-{uuid.uuid4().hex[:6]}"
            metadatos = {"nombre": nombre, "creado_en": datetime.now().isoformat()}
            veh = crear_vehiculo(tipo, vid, propulsion=energia, metadatos=metadatos)
            self.vehiculos[vid] = veh
            return veh
        except Exception as exc:
            logger.warning("No se pudo crear unidad %s/%s: %s", tipo, energia, exc)
            return None

    def obtener_vehiculo(self, vid: str) -> Optional[VehiculoBase]:
        with self._lock:
            return self.vehiculos.get(vid)

    def obtener_todos(self) -> Dict[str, VehiculoBase]:
        with self._lock:
            return dict(self.vehiculos)

    def estado_resumen(self) -> List[dict]:
        with self._lock:
            return [self._estado_completo(v) for v in self.vehiculos.values()]

    def estado_broadcast(self) -> List[dict]:
        with self._lock:
            return [self._estado_broadcast(v) for v in self.vehiculos.values()]

    def listado_incidentes(self) -> List[dict]:
        with self._lock:
            return list(self.incidentes.values())

    def _estado_completo(self, veh: VehiculoBase) -> dict:
        st = veh.obtener_estado()
        st['nombre'] = veh.metadatos.get('nombre', veh.id)
        st['incidente'] = self._incidente_actual(veh.id)
        return st

    def _estado_broadcast(self, veh: VehiculoBase) -> dict:
        st = veh.obtener_estado_broadcast()
        st['id'] = veh.id
        st['nombre'] = veh.metadatos.get('nombre', veh.id)
        st['incidente'] = self._incidente_actual(veh.id)
        st['rastro'] = list(veh.rastro)
        return st

    def _incidente_actual(self, vehiculo_id: str) -> Optional[dict]:
        inc_id = self.asignaciones.get(vehiculo_id)
        if not inc_id:
            return None
        return self.incidentes.get(inc_id)

    def actualizar(self, factor_entorno: float = 1.0,
                   delta_time: Optional[float] = None) -> None:
        """Avanza la simulacion de la flota.

        El parametro `delta_time` permite que el gestor de simulaciones replay
        avance la flota en pasos virtuales (acelerado / ralentizado) sin
        depender del intervalo real `INTERVALO_ACTUALIZACION` del bucle live.
        """
        dt = float(delta_time if delta_time is not None else INTERVALO_ACTUALIZACION)
        if dt <= 0:
            return
        factor = float(factor_entorno or 1.0)
        with self._lock:
            for veh in self.vehiculos.values():
                veh.factor_entorno = factor
                veh.actualizar_simulacion(delta_time=dt)
                self._sincronizar_incidente(veh)
        self._despachar_cola()

        # Analisis de sabotaje post-tick (fuera del lock para no bloquear).
        # Se ejecuta en el mismo hilo del bucle; cada regla es O(1) por vehiculo.
        try:
            with self._lock:
                vehiculos_snap = list(self.vehiculos.values())
            for veh in vehiculos_snap:
                _guardian_sabotaje.analizar(veh)
        except Exception as _exc:
            logger.debug("[Sabotaje] Error en analisis post-tick: %s", _exc)

    def loop_actualizacion(self, factor_entorno_cb=None) -> None:
        while True:
            try:
                factor = factor_entorno_cb() if factor_entorno_cb else 1.0
                self.actualizar(factor_entorno=factor)
                time.sleep(INTERVALO_ACTUALIZACION)
            except Exception as exc:
                logger.warning("Error en loop flota: %s", exc)
                time.sleep(1)

    def snapshot(self, decisiones_limit: int = 50) -> dict:
        """Devuelve un snapshot completo del estado de la flota.

        Pensado para que los gestores de simulacion (live o replay) expongan
        toda la informacion relevante en una sola llamada: vehiculos,
        incidentes, asignaciones y la traza reciente de decisiones.
        """
        with self._lock:
            return {
                "vehiculos": [self._estado_completo(v) for v in self.vehiculos.values()],
                "incidentes": list(self.incidentes.values()),
                "asignaciones": dict(self.asignaciones),
                "decisiones": list(self._traza)[-int(max(0, decisiones_limit)):],
                "historial_costes": list(self.historial_costes)[-50:],
            }

    def _sincronizar_incidente(self, veh: VehiculoBase) -> None:
        inc_id = self.asignaciones.get(veh.id)
        if not inc_id:
            return
        inc = self.incidentes.get(inc_id)
        if not inc:
            return

        activo = str(veh.escenario_activo or '').lower()

        if veh.en_camino:
            inc['incident_status'] = 'en_route'
            inc['status'] = 'en_route'
        elif veh.en_escena:
            inc['incident_status'] = 'on_scene'
            inc['status'] = 'on_scene'
            if inc.get('tiempo_respuesta_seg') is None and veh.tiempo_respuesta_seg is not None:
                inc['tiempo_respuesta_seg'] = int(veh.tiempo_respuesta_seg)
                inc['sla_cumplido'] = veh.tiempo_respuesta_seg <= SLA_RESPUESTA_SEG
        elif activo == veh.ESTADO_BASE.lower():
            inc['incident_status'] = 'resolved'
            inc['status'] = 'resolved'
            inc['resolved_at'] = datetime.now().isoformat()
            self._cerrar_costes_incidente(veh, inc)
            self.asignaciones.pop(veh.id, None)

    def manejar_evento(self, evento: dict) -> Optional[str]:
        try:
            return self._manejar_evento_inner(evento)
        except Exception as exc:
            tb = traceback.format_exc()
            logger.error("[Flota] manejar_evento explotó: %s\n%s", exc, tb)
            ev_id = (evento.get('id') if isinstance(evento, dict) else None) or '?'
            self._registrar_traza(
                ev_id, decision='error',
                motivo=f"{type(exc).__name__}: {exc}",
                payload=evento if isinstance(evento, dict) else {"raw": str(evento)},
            )
            return None

    def _manejar_evento_inner(self, evento: dict) -> Optional[str]:
        if not isinstance(evento, dict):
            logger.warning("[Flota] Evento descartado, no es dict: %r", evento)
            self._registrar_traza('?', 'descartado', 'no es dict',
                                  payload={"raw": str(evento)})
            return None

        evento = _desenvolver_evento(evento)

        tipo_evento = _normalizar_tipo_evento(
            evento.get('type')
            or evento.get('event_type')
            or evento.get('eventType')
            or evento.get('incident_type')
            or evento.get('category')
            or evento.get('event')
            or 'incident'
        )
        lat, lon = _extraer_coords(evento)
        ev_id = (
            evento.get('id')
            or evento.get('event_id')
            or evento.get('incident_id')
            or evento.get('uuid')
            or f"EV-{uuid.uuid4().hex[:6]}"
        )
        sev = _normalizar_severidad(evento.get('severity') or evento.get('priority'))

        resolved_at = (
            evento.get('resolved_at')
            or evento.get('closed_at')
            or evento.get('ended_at')
            or evento.get('finished_at')
        )
        estado_evento = str(
            evento.get('status')
            or evento.get('event_status')
            or evento.get('incident_status')
            or ''
        ).strip().lower()
        if not resolved_at and estado_evento in ('resolved', 'closed', 'cancelled'):
            resolved_at = evento.get('updated_at') or datetime.now().isoformat()

        logger.info(
            "[Flota] Evento Kafka recibido: id=%s type=%s severity=%s lat=%s lon=%s resolved_at=%s",
            ev_id, tipo_evento, sev, lat, lon, resolved_at,
        )

        if resolved_at:
            with self._lock:
                existente = self.incidentes.get(ev_id)
                unidad_id = None
                if existente:
                    for vid, inc_id in self.asignaciones.items():
                        if inc_id == ev_id:
                            unidad_id = vid
                            break

                if existente and existente.get('status') != 'resolved':
                    existente['incident_status'] = 'resolved'
                    existente['status'] = 'resolved'
                    existente['resolved_at'] = resolved_at

                    veh = self.vehiculos.get(unidad_id) if unidad_id else None
                    if veh:
                        self._cerrar_costes_incidente(veh, existente)
                        self.asignaciones.pop(unidad_id, None)
                        try:
                            veh.terminar_escenario()
                        except Exception as exc:
                            logger.warning(
                                "[Flota] No se pudo terminar escenario de %s al cerrar %s: %s",
                                unidad_id, ev_id, exc,
                            )

                    logger.info(
                        "[Flota] Evento %s marcado como resuelto en origen; incidente local cerrado",
                        ev_id,
                    )
                    self._registrar_traza(
                        ev_id,
                        'cerrado_por_origen',
                        f"resolved_at={resolved_at}",
                        tipo=tipo_evento,
                        payload=evento,
                    )
                    return ev_id

        if lat is None or lon is None:
            logger.warning(
                "[Flota] Evento %s sin coordenadas validas, descartado. "
                "type=%s keys_disponibles=%s payload_breve=%r",
                ev_id, tipo_evento,
                list(evento.keys()),
                {k: v for k, v in list(evento.items())[:8]},
            )
            self._registrar_traza(ev_id, 'descartado',
                                  'sin coordenadas',
                                  tipo=tipo_evento, payload=evento)
            return None

        if resolved_at:
            logger.info(
                "[Flota] Evento %s ya resuelto en origen, sin incidente activo local",
                ev_id,
            )
            self._registrar_traza(ev_id, 'descartado',
                                  f'resolved_at={resolved_at}',
                                  tipo=tipo_evento, payload=evento)
            return ev_id

        evento_norm = dict(evento)
        evento_norm['id'] = ev_id
        evento_norm['type'] = tipo_evento
        evento_norm['latitude'] = float(lat)
        evento_norm['longitude'] = float(lon)
        evento_norm['severity'] = sev
        if not evento_norm.get('started_at'):
            evento_norm['started_at'] = evento.get('timestamp') or datetime.now().isoformat()

        with self._lock:
            existente = self.incidentes.get(ev_id)
            if existente and existente.get('status') not in ('queued',):
                logger.info(
                    "[Flota] Evento %s ya tratado (status=%s), ignorado",
                    ev_id, existente.get('status'),
                )
                self._registrar_traza(ev_id, 'duplicado',
                                      f"status={existente.get('status')}",
                                      tipo=tipo_evento)
                return ev_id

        unidad_objetivo = MAPA_EVENTO_A_UNIDAD.get(tipo_evento, 'policia')
        incidente = self._construir_incidente(evento_norm, unidad_objetivo)

        if tipo_evento in EVENTOS_CON_SCOUT:
            self._desplegar_scout_si_disponible(incidente)

        with self._lock:
            unidad = self._buscar_unidad_para(
                unidad_objetivo,
                lat=float(lat),
                lon=float(lon),
            )
            if not unidad:
                incidente['incident_status'] = 'queued'
                incidente['status'] = 'queued'
                self.incidentes[incidente['incident_id']] = incidente
                logger.warning(
                    "[Flota] Sin unidad %s libre para %s, en cola",
                    unidad_objetivo, incidente['incident_id'],
                )
                self._registrar_traza(ev_id, 'cola',
                                      f"sin {unidad_objetivo} libre",
                                      tipo=tipo_evento)
                return incidente['incident_id']

            try:
                inc_id = self._activar_intervencion(unidad, incidente)
            except Exception as exc:
                tb = traceback.format_exc()
                logger.error(
                    "[Flota] _activar_intervencion fallo para %s: %s\n%s",
                    incidente.get('incident_id'), exc, tb,
                )
                self._registrar_traza(ev_id, 'error',
                                      f"_activar_intervencion: {type(exc).__name__}: {exc}",
                                      tipo=tipo_evento, payload=evento)
                return None

            logger.info(
                "[Flota] Asignado %s a %s (%s) para %s",
                unidad.id, unidad_objetivo, unidad.metadatos.get('nombre', unidad.id), inc_id,
            )
            # Calcular distancia aproximada al incidente para incluirla en la traza
            try:
                cos_lat = math.cos(math.radians(float(lat)))
                dlat = (unidad.gps.latitud - float(lat)) * 111.32
                dlon = (unidad.gps.longitud - float(lon)) * 111.32 * cos_lat
                dist_km = round((dlat * dlat + dlon * dlon) ** 0.5, 2)
            except Exception:
                dist_km = None

            self._registrar_traza(
                ev_id, 'asignado',
                f"{unidad_objetivo}={unidad.id} "
                f"nombre={unidad.metadatos.get('nombre', unidad.id)} "
                f"distancia={dist_km}km",
                tipo=tipo_evento,
            )
            return inc_id

    def _despachar_cola(self) -> None:
        with self._lock:
            pendientes = [i for i in self.incidentes.values() if i.get('status') == 'queued']
            for inc in pendientes:
                tipo = inc.get('tipo_unidad_solicitada', 'policia')
                lat_inc = inc.get('lat')
                lon_inc = inc.get('lon')
                try:
                    lat_f = float(lat_inc) if lat_inc is not None else None
                    lon_f = float(lon_inc) if lon_inc is not None else None
                except (TypeError, ValueError):
                    lat_f = lon_f = None
                unidad = self._buscar_unidad_para(tipo, lat=lat_f, lon=lon_f)
                if unidad:
                    nuevo_inc = dict(inc)
                    self._activar_intervencion(unidad, nuevo_inc)
                    self.incidentes[nuevo_inc['incident_id']] = nuevo_inc
                    logger.info(
                        "[Flota] Cola: asignado %s a %s (%s)",
                        unidad.id, tipo, nuevo_inc['incident_id'],
                    )

    def _construir_incidente(self, evento: dict, unidad_objetivo: str) -> dict:
        sev = evento.get('severity', 'medium')
        inc_id = evento.get('id') or f"INC-{uuid.uuid4().hex[:6]}"
        return {
            'incident_id': inc_id,
            'incident_type': evento.get('type'),
            'incident_status': 'assigned',
            'title': evento.get('title') or evento.get('type', 'Incidente'),
            'description': evento.get('description', ''),
            'severity': sev,
            'lat': evento.get('latitude'),
            'lon': evento.get('longitude'),
            'started_at': evento.get('started_at') or datetime.now().isoformat(),
            'duracion_min': _duracion_por_severidad(sev),
            'status': 'assigned',
            'tipo_unidad_solicitada': unidad_objetivo,
            'origen': evento.get('origen', 'kafka')
        }

    def _buscar_unidad_para(self, tipo: str,
                             lat: Optional[float] = None,
                             lon: Optional[float] = None) -> Optional[VehiculoBase]:
        """Devuelve la unidad libre mas cercana al incidente del tipo solicitado.

        Si no se dispone de coordenadas del incidente, devuelve la primera
        unidad libre (orden de insercion). La distancia se calcula con una
        aproximacion rapida en km (sin haversine completo) suficientemente
        precisa para las dimensiones de la isla de Aruba.
        """
        candidatos = [v for v in self.vehiculos.values()
                      if v.TIPO == tipo and not self._tiene_escenario_activo(v)]
        if not candidatos:
            return None
        if lat is None or lon is None:
            return candidatos[0]

        # cos(12.5 deg) ~= 0.976 — Aruba esta cerca del ecuador, buena aproximacion
        cos_lat = math.cos(math.radians(lat))

        def dist_sq(v: VehiculoBase) -> float:
            dlat = (v.gps.latitud - lat) * 111.32
            dlon = (v.gps.longitud - lon) * 111.32 * cos_lat
            return dlat * dlat + dlon * dlon

        return min(candidatos, key=dist_sq)

    def _tiene_escenario_activo(self, veh: VehiculoBase) -> bool:
        if veh.id in self.asignaciones:
            return True
        return veh.en_camino or veh.en_escena or veh.reabasteciendo

    def _activar_intervencion(self, unidad: VehiculoBase, incidente: dict) -> str:
        origen = (unidad.gps.latitud, unidad.gps.longitud)
        destino_raw = (incidente['lat'], incidente['lon'])

        # Los drones vuelan en linea recta: no necesitan routing por carretera.
        es_dron = (getattr(unidad, 'TIPO', '') == 'dron')

        if es_dron:
            # Ruta directa [origen, destino] — linea aerea.
            ruta = [list(origen), list(destino_raw)]
            distancia_km = sum(
                math.hypot(
                    (ruta[i+1][0] - ruta[i][0]) * 111.32,
                    (ruta[i+1][1] - ruta[i][1]) * 111.32 * math.cos(math.radians(ruta[i][0]))
                )
                for i in range(len(ruta) - 1)
            )
        else:
            try:
                destino = snap_a_carretera(destino_raw[0], destino_raw[1])
            except Exception:
                destino = destino_raw
            ruta = []
            try:
                ruta = generar_ruta(origen, destino) or []
            except Exception as exc:
                logger.warning("Error generando ruta para %s: %s", incidente['incident_id'], exc)
            distancia_km = obtener_distancia_total_ruta(ruta) if ruta else 0.0

        # Velocidad nominal de emergencia: se usa solo para calcular ETA.
        # Se respeta el maximo real del vehiculo (no se capea artificialmente
        # a 110 km/h como antes; las unidades de policia pueden hacer 180).
        # Factor de seguridad 0.75 = conduccion agresiva pero no irreal.
        velocidad_nominal = max(60, int(unidad.velocidad_max_unidad * 0.75))
        tiempo_viaje_s = int((distancia_km / velocidad_nominal) * 3600) if distancia_km > 0 else 60

        factor = max(0.2, min(1.5, float(getattr(unidad, 'factor_entorno', 1.0) or 1.0)))
        if factor < 1.0:
            tiempo_viaje_s = int(tiempo_viaje_s / factor)

        intensidad = _intensidad_por_severidad(incidente.get('severity'))
        duracion_min = incidente.get('duracion_min') or _duracion_por_severidad(incidente.get('severity'))
        tiempo_total_s = duracion_min * 60
        tiempo_escena_s = max(60, tiempo_total_s - tiempo_viaje_s)

        modificadores = self._construir_modificadores(
            unidad, incidente, intensidad, tiempo_viaje_s, tiempo_escena_s
        )
        nombre_escenario = incidente.get('title') or incidente.get('incident_type') or 'Intervencion'

        unidad.aplicar_escenario(
            tipo_escenario=incidente.get('incident_type') or 'incidente',
            duracion_minutos=duracion_min,
            intensidad=intensidad,
            nombre_personalizado=nombre_escenario,
            modificadores=modificadores,
            ruta=ruta if ruta and len(ruta) >= 2 else None,
            incident_id=incidente.get('incident_id'),
        )

        incidente['eta_seg'] = tiempo_viaje_s
        incidente['distancia_km'] = round(distancia_km, 2)
        incidente['unidad_id'] = unidad.id
        incidente['unidad_nombre'] = unidad.metadatos.get('nombre', unidad.id)
        incidente['incident_status'] = 'en_route'
        incidente['status'] = 'en_route'
        incidente['factor_entorno'] = round(factor, 2)
        incidente['tarifa'] = {
            'coste_min_eur': round(float(unidad.coste_min), 2),
            'coste_activacion_eur': round(float(unidad.coste_activacion), 2),
            'dotacion': int(unidad.dotacion),
            'propulsion': unidad.propulsion,
        }

        self.incidentes[incidente['incident_id']] = incidente
        self.asignaciones[unidad.id] = incidente['incident_id']

        # Notificar a la unidad de su nueva mision (hook extensible).
        # Se llama FUERA del lock para que las subclases que hagan
        # operaciones de red (e.g. consulta historial clinico) no bloqueen
        # el bucle de despacho. El hook lanza su propio hilo si lo necesita.
        try:
            unidad.on_asignacion_incidente(dict(incidente))
        except Exception as exc:
            logger.warning("[Flota] on_asignacion_incidente fallo para %s: %s",
                           unidad.id, exc)

        logger.info(
            "[Flota] %s/%s -> %s ETA=%ss dist=%.2fkm factor=%.2f",
            unidad.TIPO, unidad.id[:8], incidente['incident_id'],
            tiempo_viaje_s, distancia_km, factor
        )
        logger.info(
            "[Coste] Activacion %s: tarifa %.2f EUR/min + %.2f EUR activacion (dotacion %d, %s)",
            incidente['incident_id'], unidad.coste_min, unidad.coste_activacion,
            unidad.dotacion, unidad.propulsion,
        )
        return unidad.id

    def _construir_modificadores(self, unidad: VehiculoBase, incidente: dict,
                                  intensidad: float, tiempo_viaje_s: int,
                                  tiempo_escena_s: int) -> dict:
        # aceleracion_max en km/h por segundo de simulacion.
        # Un vehiculo de emergencia real hace 0-100 en ~8-10s → ~11 km/h/s.
        # Drones: mas agiles en arranque (~15 km/h/s).
        tipo_u = getattr(unidad, 'TIPO', '')
        if tipo_u == 'dron':
            acel = 18
        elif intensidad >= 0.7:
            acel = 12
        else:
            acel = 7

        mods = {
            'tiempo_viaje': tiempo_viaje_s,
            'tiempo_escena': tiempo_escena_s,
            'intensidad': intensidad,
            'comportamiento_escena': 'estacionario',
            'consumo_factor': 1.2 if intensidad >= 0.7 else 1.0,
            'temp_factor': 1.3 if intensidad >= 0.7 else 1.0,
            'desgaste_factor': 1.5 if intensidad >= 0.7 else 1.0,
            'aceleracion_max': acel,
        }
        tipo_inc = (incidente.get('incident_type') or '').lower()

        if unidad.TIPO == 'ambulancia':
            triage = 'rojo' if intensidad >= 0.8 else ('amarillo' if intensidad >= 0.5 else 'verde')
            prioridad = 'P1' if triage == 'rojo' else 'P2'
            evolucion = 'deterioro_grave' if triage == 'rojo' else 'estable'
            mods['estado_clinico'] = {
                'triage': triage,
                'prioridad_traslado': prioridad,
                'evolucion_esperada': evolucion,
                'soporte_clinico_requerido': ['oxigeno'] + (['ventilacion'] if triage == 'rojo' else [])
            }
            mods['paciente'] = {
                'urgencia': triage,
                'condiciones': incidente.get('description') or tipo_inc
            }
        elif unidad.TIPO == 'bomberos':
            mapa = {'fire': 'estructural', 'hazmat_spill': 'derrame', 'flood': 'derrame'}
            mods['tipo_incendio'] = mapa.get(tipo_inc, 'otro')
            mods['requiere_escala'] = (mods['tipo_incendio'] == 'estructural' and intensidad >= 0.6)
        elif unidad.TIPO == 'policia':
            if intensidad >= 0.8:
                mods['protocolo_contencion'] = 'armado'
            elif intensidad >= 0.5:
                mods['protocolo_contencion'] = 'perimetro'
        elif unidad.TIPO == 'proteccion_civil':
            mapa = {
                'flood': 'evacuacion',
                'storm': 'balizamiento',
                'power_outage': 'apoyo',
                'earthquake': 'evacuacion',
                'marine_rescue': 'logistica',
            }
            mods['mision_pc'] = mapa.get(tipo_inc, 'apoyo')
            mods['kits_a_repartir'] = max(1, int(intensidad * 10))
        elif unidad.TIPO == 'dron':
            mods['modo_dron'] = 'scout'
            mods['altitud_m'] = 80 + intensidad * 100

        return mods

    def asignar_manual(self, vehiculo_id: str, incidente_data: dict) -> Optional[str]:
        with self._lock:
            veh = self.vehiculos.get(vehiculo_id)
            if not veh:
                return None
            if self._tiene_escenario_activo(veh):
                logger.info("[Flota] %s ya esta ocupado, ignorando asignacion manual", vehiculo_id)
                return None

            inc_id = incidente_data.get('incident_id') or f"INC-{uuid.uuid4().hex[:6]}"
            incidente = {
                'incident_id': inc_id,
                'incident_type': incidente_data.get('incident_type', 'manual'),
                'incident_status': 'assigned',
                'title': incidente_data.get('title') or 'Asignacion manual',
                'description': incidente_data.get('description', ''),
                'severity': incidente_data.get('severity', 'medium'),
                'lat': incidente_data.get('lat'),
                'lon': incidente_data.get('lon'),
                'started_at': datetime.now().isoformat(),
                'duracion_min': incidente_data.get('duracion_min') or _duracion_por_severidad(
                    incidente_data.get('severity')),
                'status': 'assigned',
                'tipo_unidad_solicitada': veh.TIPO,
                'origen': 'manual'
            }
            return self._activar_intervencion(veh, incidente)

    def cerrar_incidente_manual(self, vehiculo_id: str) -> bool:
        with self._lock:
            veh = self.vehiculos.get(vehiculo_id)
            if not veh:
                return False
            inc_id = self.asignaciones.pop(vehiculo_id, None)
            if inc_id and inc_id in self.incidentes:
                inc = self.incidentes[inc_id]
                inc['status'] = 'resolved'
                inc['incident_status'] = 'resolved'
                inc['resolved_at'] = datetime.now().isoformat()
                self._cerrar_costes_incidente(veh, inc)
            veh.terminar_escenario()
            return True

    def _cerrar_costes_incidente(self, veh: VehiculoBase, inc: dict) -> None:
        """Persiste el desglose de costes en el incidente y en el historial."""
        try:
            minutos = round(veh._segundos_facturados / 60.0, 2)
            tiempo_resp = veh.tiempo_respuesta_seg
            registro_inc = {
                'incident_id': inc.get('incident_id'),
                'incident_type': inc.get('incident_type'),
                'severity': inc.get('severity'),
                'unidad_id': veh.id,
                'unidad_nombre': veh.metadatos.get('nombre', veh.id),
                'tipo_unidad': veh.TIPO,
                'propulsion': veh.propulsion,
                'started_at': inc.get('started_at'),
                'resolved_at': inc.get('resolved_at'),
                'tiempo_respuesta_seg': int(tiempo_resp) if tiempo_resp is not None else None,
                'sla_cumplido': (tiempo_resp is None or tiempo_resp <= SLA_RESPUESTA_SEG),
                'minutos_facturados': minutos,
                'coste_activacion_eur': round(veh.coste_activacion_aplicado_eur, 2),
                'coste_personal_eur': round(veh.coste_personal_eur, 2),
                'coste_energia_eur': round(veh.coste_energia_eur, 2),
                'coste_desgaste_eur': round(veh.coste_desgaste_eur, 2),
                'coste_tiempo_eur': round(veh.coste_tiempo_eur, 2),
                'prima_respuesta_eur': round(veh.prima_respuesta_eur, 2),
                'coste_total_eur': round(veh.coste_intervencion_eur, 2),
                'clase_coste': clasificar_coste(veh.coste_intervencion_eur),
            }

            inc['tiempo_respuesta_seg'] = registro_inc['tiempo_respuesta_seg']
            inc['sla_cumplido'] = registro_inc['sla_cumplido']
            inc['coste_total_eur'] = registro_inc['coste_total_eur']
            inc['coste_breakdown'] = {
                'coste_activacion_eur': registro_inc['coste_activacion_eur'],
                'coste_personal_eur': registro_inc['coste_personal_eur'],
                'coste_energia_eur': registro_inc['coste_energia_eur'],
                'coste_desgaste_eur': registro_inc['coste_desgaste_eur'],
                'coste_tiempo_eur': registro_inc['coste_tiempo_eur'],
                'prima_respuesta_eur': registro_inc['prima_respuesta_eur'],
            }

            self.historial_costes.append(registro_inc)
            logger.info(
                "[Coste] Cierre %s (%s/%s): total=%.2f EUR (act=%.2f + tiempo=%.2f + prima=%.2f)"
                " · respuesta=%s s · SLA=%s",
                registro_inc['incident_id'], registro_inc['tipo_unidad'], registro_inc['propulsion'],
                registro_inc['coste_total_eur'],
                registro_inc['coste_activacion_eur'],
                registro_inc['coste_tiempo_eur'],
                registro_inc['prima_respuesta_eur'],
                registro_inc['tiempo_respuesta_seg'],
                'OK' if registro_inc['sla_cumplido'] else 'SUPERADO',
            )
        except Exception as exc:
            logger.warning("[Flota] No se pudo cerrar desglose de costes: %s", exc)

    def resumen_costes(self) -> dict:
        """Vista agregada para el panel de analisis de costes."""
        with self._lock:
            vehiculos = list(self.vehiculos.values())
            incidentes = list(self.incidentes.values())
            historico = list(self.historial_costes)

        en_curso = 0
        coste_actual = 0.0
        coste_total_acum = 0.0
        coste_personal = 0.0
        coste_energia = 0.0
        coste_desgaste = 0.0
        coste_activacion = 0.0
        coste_prima = 0.0
        intervenciones = 0

        por_tipo: Dict[str, dict] = {}
        por_propulsion: Dict[str, dict] = {}

        for veh in vehiculos:
            en_intervencion = veh._esta_en_intervencion()
            if en_intervencion:
                en_curso += 1
                coste_actual += float(veh.coste_intervencion_eur or 0.0)

            coste_total_acum += float(veh.coste_total_eur or 0.0) + (
                float(veh.coste_intervencion_eur or 0.0) if en_intervencion else 0.0)
            coste_personal += float(veh.coste_acumulado_personal_eur or 0.0) + (
                float(veh.coste_personal_eur or 0.0) if en_intervencion else 0.0)
            coste_energia += float(veh.coste_acumulado_energia_eur or 0.0) + (
                float(veh.coste_energia_eur or 0.0) if en_intervencion else 0.0)
            coste_desgaste += float(veh.coste_acumulado_desgaste_eur or 0.0) + (
                float(veh.coste_desgaste_eur or 0.0) if en_intervencion else 0.0)
            coste_activacion += float(veh.coste_acumulado_activacion_eur or 0.0) + (
                float(veh.coste_activacion_aplicado_eur or 0.0) if en_intervencion else 0.0)
            coste_prima += float(veh.coste_acumulado_prima_eur or 0.0) + (
                float(veh.prima_respuesta_eur or 0.0) if en_intervencion else 0.0)
            intervenciones += int(veh.intervenciones_realizadas or 0)

            agg_t = por_tipo.setdefault(veh.TIPO, {
                'tipo': veh.TIPO, 'unidades': 0, 'intervenciones': 0,
                'coste_total_eur': 0.0, 'coste_actual_eur': 0.0, 'en_curso': 0,
            })
            agg_t['unidades'] += 1
            agg_t['intervenciones'] += int(veh.intervenciones_realizadas or 0)
            agg_t['coste_total_eur'] += float(veh.coste_total_eur or 0.0) + (
                float(veh.coste_intervencion_eur or 0.0) if en_intervencion else 0.0)
            if en_intervencion:
                agg_t['coste_actual_eur'] += float(veh.coste_intervencion_eur or 0.0)
                agg_t['en_curso'] += 1

            agg_p = por_propulsion.setdefault(veh.propulsion, {
                'propulsion': veh.propulsion, 'unidades': 0, 'coste_total_eur': 0.0,
            })
            agg_p['unidades'] += 1
            agg_p['coste_total_eur'] += float(veh.coste_total_eur or 0.0) + (
                float(veh.coste_intervencion_eur or 0.0) if en_intervencion else 0.0)

        cumplidos = [h for h in historico if h.get('sla_cumplido')]
        sla_pct = (100.0 * len(cumplidos) / len(historico)) if historico else None

        tiempos = [h.get('tiempo_respuesta_seg') for h in historico
                   if h.get('tiempo_respuesta_seg') is not None]
        tiempo_resp_medio = (sum(tiempos) / len(tiempos)) if tiempos else None

        coste_medio = (coste_total_acum / intervenciones) if intervenciones else 0.0

        incidentes_activos = [i for i in incidentes
                              if i.get('status') in ('assigned', 'en_route', 'on_scene', 'queued')]

        return {
            "generado_en": datetime.now().isoformat(),
            "currency": "EUR",
            "totales": {
                "coste_total_eur": round(coste_total_acum, 2),
                "coste_intervenciones_en_curso_eur": round(coste_actual, 2),
                "coste_medio_intervencion_eur": round(coste_medio, 2),
                "intervenciones_realizadas": intervenciones,
                "intervenciones_en_curso": en_curso,
                "incidentes_activos": len(incidentes_activos),
                "clase": clasificar_coste(coste_total_acum),
            },
            "desglose_acumulado": {
                "coste_personal_eur": round(coste_personal, 2),
                "coste_energia_eur": round(coste_energia, 2),
                "coste_desgaste_eur": round(coste_desgaste, 2),
                "coste_activacion_eur": round(coste_activacion, 2),
                "prima_respuesta_eur": round(coste_prima, 2),
            },
            "por_tipo": [
                {
                    **v,
                    "coste_total_eur": round(v["coste_total_eur"], 2),
                    "coste_actual_eur": round(v["coste_actual_eur"], 2),
                }
                for v in sorted(por_tipo.values(),
                                key=lambda x: x["coste_total_eur"], reverse=True)
            ],
            "por_propulsion": [
                {**v, "coste_total_eur": round(v["coste_total_eur"], 2)}
                for v in sorted(por_propulsion.values(),
                                key=lambda x: x["coste_total_eur"], reverse=True)
            ],
            "sla_respuesta": {
                "sla_seg": SLA_RESPUESTA_SEG,
                "porcentaje_cumplido": (round(sla_pct, 1) if sla_pct is not None else None),
                "tiempo_respuesta_medio_seg": (round(tiempo_resp_medio, 1)
                    if tiempo_resp_medio is not None else None),
                "intervenciones_evaluadas": len(historico),
            },
            "ultimas_intervenciones": list(reversed(historico[-15:])),
            "tarifas": tarifas_completas(),
        }

    def listado_intervenciones_costes(self, limite: int = 50) -> list:
        with self._lock:
            base = list(self.historial_costes)
        return list(reversed(base))[:max(0, int(limite))]

    def coste_vehiculo(self, vehiculo_id: str) -> Optional[dict]:
        with self._lock:
            veh = self.vehiculos.get(vehiculo_id)
            if not veh:
                return None
            estado = veh.obtener_estado()
            historial = veh.historial_costes()

        return {
            "vehicle_id": veh.id,
            "tipo": veh.TIPO,
            "propulsion": veh.propulsion,
            "nombre": veh.metadatos.get('nombre', veh.id),
            "tarifa": obtener_tarifa(veh.TIPO, veh.propulsion),
            "costes": estado.get('costes', {}),
            "historial": list(reversed(historial)),
        }

    def estimar_coste_intervencion(self, tipo: str, propulsion: str,
                                   minutos: float,
                                   tiempo_respuesta_seg: Optional[float] = None) -> dict:
        """Util para previsualizar el coste teorico (antes de despachar)."""
        tarifa = obtener_tarifa(tipo, propulsion)
        if not tarifa:
            return {}
        minutos = max(0.0, float(minutos or 0.0))
        desglose = desglose_coste_minuto(tipo, propulsion, minutos)
        prima = prima_tiempo_respuesta(tiempo_respuesta_seg) if tiempo_respuesta_seg is not None else 0.0
        total = float(tarifa['coste_activacion']) + desglose['total'] + prima
        return {
            "tipo": tipo,
            "propulsion": propulsion,
            "tarifa": tarifa,
            "minutos": round(minutos, 2),
            "coste_activacion_eur": round(float(tarifa['coste_activacion']), 2),
            "coste_personal_eur": round(desglose['personal'], 2),
            "coste_energia_eur": round(desglose['energia'], 2),
            "coste_desgaste_eur": round(desglose['desgaste'], 2),
            "coste_tiempo_eur": round(desglose['total'], 2),
            "prima_respuesta_eur": round(prima, 2),
            "coste_total_eur": round(total, 2),
            "clase": clasificar_coste(total),
        }

    def _desplegar_scout_si_disponible(self, incidente_principal: dict) -> Optional[str]:
        with self._lock:
            dron = next(
                (v for v in self.vehiculos.values()
                 if v.TIPO == 'dron' and not self._tiene_escenario_activo(v)),
                None
            )
            if not dron:
                return None

            scout_id = f"SCOUT-{incidente_principal['incident_id']}"
            scout_inc = {
                'incident_id': scout_id,
                'incident_type': 'aerial_scout',
                'incident_status': 'en_route',
                'title': f"Scout {incidente_principal.get('title', '')}".strip(),
                'description': f"Reconocimiento aereo previo de {incidente_principal['incident_id']}",
                'severity': 'medium',
                'lat': incidente_principal.get('lat'),
                'lon': incidente_principal.get('lon'),
                'started_at': datetime.now().isoformat(),
                'duracion_min': 12,
                'status': 'en_route',
                'tipo_unidad_solicitada': 'dron',
                'origen': 'auto-scout',
                'incidente_padre': incidente_principal['incident_id']
            }
            logger.info("[Flota] Desplegando dron scout para %s",
                        incidente_principal['incident_id'])
            return self._activar_intervencion(dron, scout_inc)
