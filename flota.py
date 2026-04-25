

import logging
import threading
import time
import traceback
import uuid
from collections import deque
from datetime import datetime
from typing import Dict, List, Optional

from config import INTERVALO_ACTUALIZACION
from rutas import generar_ruta, obtener_distancia_total_ruta, snap_a_carretera
from vehiculo_factory import crear_vehiculo
from vehiculo_base import VehiculoBase

logger = logging.getLogger(__name__)

MAPA_EVENTO_A_UNIDAD = {
    "fire": "bomberos",
    "hazmat_spill": "bomberos",
    "medical_emergency": "ambulancia",
    "accident": "policia",
    "lane_closure": "policia",
    "construction": "policia",
    "storm": "proteccion_civil",
    "flood": "proteccion_civil",
    "public_event": "policia",
    "power_outage": "proteccion_civil",
    "earthquake": "proteccion_civil",
    "marine_rescue": "proteccion_civil",
}

EVENTOS_CON_SCOUT = {"fire", "flood", "marine_rescue", "earthquake", "hazmat_spill"}

PLANTILLA_DEFECTO: List[tuple] = [
    ("policia",          "combustion", "Patrulla 01"),
    ("policia",          "electrico",  "Patrulla 02 EV"),
    ("ambulancia",       "combustion", "Ambulancia 01"),
    ("bomberos",         "combustion", "Bomberos 01"),
    ("proteccion_civil", "combustion", "PC 01"),
    ("dron",             "unico",      "Dron 01"),
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

def _extraer_coords(evento: dict):
    lat = _to_float(evento.get('latitude'))
    if lat is None:
        lat = _to_float(evento.get('lat'))

    lon = _to_float(evento.get('longitude'))
    if lon is None:
        lon = _to_float(evento.get('lon'))
    if lon is None:
        lon = _to_float(evento.get('lng'))

    if lat is None or lon is None:
        loc = evento.get('location') or evento.get('coords') or evento.get('coordinates') or evento.get('position')
        if isinstance(loc, dict):
            lat = lat if lat is not None else _to_float(loc.get('lat') or loc.get('latitude'))
            lon = lon if lon is not None else _to_float(loc.get('lon') or loc.get('lng') or loc.get('longitude'))
        elif isinstance(loc, (list, tuple)) and len(loc) >= 2:
            lat = lat if lat is not None else _to_float(loc[1] if abs(_to_float(loc[0]) or 0) > 90 else loc[0])
            lon = lon if lon is not None else _to_float(loc[0] if abs(_to_float(loc[0]) or 0) > 90 else loc[1])

    return lat, lon

class FleetManager:
    def __init__(self, plantilla: Optional[List[tuple]] = None):
        self._lock = threading.RLock()
        self.vehiculos: Dict[str, VehiculoBase] = {}

        self.incidentes: Dict[str, dict] = {}

        self.asignaciones: Dict[str, str] = {}
        self._traza: deque = deque(maxlen=200)
        self._crear_flotas_base(plantilla or PLANTILLA_DEFECTO)

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

    def actualizar(self, factor_entorno: float = 1.0) -> None:
        factor = float(factor_entorno or 1.0)
        with self._lock:
            for veh in self.vehiculos.values():
                veh.factor_entorno = factor
                veh.actualizar_simulacion(delta_time=INTERVALO_ACTUALIZACION)
                self._sincronizar_incidente(veh)
        self._despachar_cola()

    def loop_actualizacion(self, factor_entorno_cb=None) -> None:
        while True:
            try:
                factor = factor_entorno_cb() if factor_entorno_cb else 1.0
                self.actualizar(factor_entorno=factor)
                time.sleep(INTERVALO_ACTUALIZACION)
            except Exception as exc:
                logger.warning("Error en loop flota: %s", exc)
                time.sleep(1)

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
        elif activo == veh.ESTADO_BASE.lower():

            inc['incident_status'] = 'resolved'
            inc['status'] = 'resolved'
            inc['resolved_at'] = datetime.now().isoformat()
            inc['coste_total_eur'] = round(veh.coste_total_eur, 2)
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

        tipo_evento = (evento.get('type') or evento.get('event_type')
                       or evento.get('category') or 'incident')
        lat, lon = _extraer_coords(evento)
        ev_id = (evento.get('id') or evento.get('event_id')
                 or evento.get('incident_id') or f"EV-{uuid.uuid4().hex[:6]}")
        sev = (evento.get('severity') or evento.get('priority') or 'medium')
        resolved_at = evento.get('resolved_at') or evento.get('closed_at')

        logger.info(
            "[Flota] Evento Kafka recibido: id=%s type=%s severity=%s lat=%s lon=%s resolved_at=%s",
            ev_id, tipo_evento, sev, lat, lon, resolved_at,
        )

        if lat is None or lon is None:
            logger.warning(
                "[Flota] Evento %s sin coordenadas validas, descartado. payload=%r",
                ev_id, evento,
            )
            self._registrar_traza(ev_id, 'descartado',
                                  'sin coordenadas',
                                  tipo=tipo_evento, payload=evento)
            return None

        if resolved_at:
            logger.info("[Flota] Evento %s ya resuelto en origen, no se asigna unidad", ev_id)
            self._registrar_traza(ev_id, 'descartado',
                                  f'resolved_at={resolved_at}',
                                  tipo=tipo_evento, payload=evento)
            return None

        evento_norm = dict(evento)
        evento_norm['id'] = ev_id
        evento_norm['type'] = tipo_evento
        evento_norm['latitude'] = float(lat)
        evento_norm['longitude'] = float(lon)
        evento_norm['severity'] = sev

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
            unidad = self._buscar_unidad_para(unidad_objetivo)
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
            self._registrar_traza(
                ev_id, 'asignado',
                f"{unidad_objetivo}={unidad.id} nombre={unidad.metadatos.get('nombre', unidad.id)}",
                tipo=tipo_evento,
            )
            return inc_id

    def _despachar_cola(self) -> None:
        with self._lock:
            pendientes = [i for i in self.incidentes.values() if i.get('status') == 'queued']
            for inc in pendientes:
                tipo = inc.get('tipo_unidad_solicitada', 'policia')
                unidad = self._buscar_unidad_para(tipo)
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

    def _buscar_unidad_para(self, tipo: str) -> Optional[VehiculoBase]:
        candidatos = [v for v in self.vehiculos.values()
                      if v.TIPO == tipo and not self._tiene_escenario_activo(v)]
        return candidatos[0] if candidatos else None

    def _tiene_escenario_activo(self, veh: VehiculoBase) -> bool:
        if veh.id in self.asignaciones:
            return True
        return veh.en_camino or veh.en_escena or veh.reabasteciendo

    def _activar_intervencion(self, unidad: VehiculoBase, incidente: dict) -> str:
        # Vehicle is already on a road (spawned on road network).
        # Snap the incident destination to the nearest road node using the
        # graph (fast, no HTTP) so the routing engine gets a reachable target.
        origen = (unidad.gps.latitud, unidad.gps.longitud)
        destino_raw = (incidente['lat'], incidente['lon'])
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
        velocidad_nominal = max(40, min(unidad.velocidad_max_unidad, 110))
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
            ruta=ruta if ruta and len(ruta) >= 2 else None
        )

        incidente['eta_seg'] = tiempo_viaje_s
        incidente['distancia_km'] = round(distancia_km, 2)
        incidente['unidad_id'] = unidad.id
        incidente['unidad_nombre'] = unidad.metadatos.get('nombre', unidad.id)
        incidente['incident_status'] = 'en_route'
        incidente['status'] = 'en_route'
        incidente['factor_entorno'] = round(factor, 2)

        self.incidentes[incidente['incident_id']] = incidente
        self.asignaciones[unidad.id] = incidente['incident_id']

        logger.info(
            "[Flota] %s/%s -> %s ETA=%ss dist=%.2fkm factor=%.2f",
            unidad.TIPO, unidad.id[:8], incidente['incident_id'],
            tiempo_viaje_s, distancia_km, factor
        )
        return unidad.id

    def _construir_modificadores(self, unidad: VehiculoBase, incidente: dict,
                                  intensidad: float, tiempo_viaje_s: int,
                                  tiempo_escena_s: int) -> dict:
        mods = {
            'tiempo_viaje': tiempo_viaje_s,
            'tiempo_escena': tiempo_escena_s,
            'intensidad': intensidad,
            'comportamiento_escena': 'estacionario',
            'consumo_factor': 1.2 if intensidad >= 0.7 else 1.0,
            'temp_factor': 1.3 if intensidad >= 0.7 else 1.0,
            'desgaste_factor': 1.5 if intensidad >= 0.7 else 1.0,
            'aceleracion_max': 8 if intensidad >= 0.7 else 5,
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
                inc['coste_total_eur'] = round(
                    veh.coste_total_eur + veh.coste_intervencion_eur, 2
                )
            veh.terminar_escenario()
            return True

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
