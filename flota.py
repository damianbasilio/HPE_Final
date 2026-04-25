import logging
import threading
import time
import uuid
from typing import Dict, Optional

from config import INTERVALO_ACTUALIZACION
from rutas import generar_ruta
from vehiculo import crear_vehiculo, VehiculoBase

logger = logging.getLogger(__name__)


class FleetManager:
    def __init__(self):
        self._lock = threading.Lock()
        self.vehiculos: Dict[str, VehiculoBase] = {}
        self.asignaciones: Dict[str, str] = {}
        self.incidentes: Dict[str, dict] = {}
        self._crear_flotas_base()

    def _crear_flotas_base(self) -> None:
        for tipo in ["policia", "ambulancia", "bomberos", "dron"]:
            vid = f"{tipo}-{uuid.uuid4().hex[:6]}"
            self.vehiculos[vid] = crear_vehiculo(vid, tipo=tipo, energia="combustion")

    def asignar_vehiculo(self, sesion_id: str, tipo_preferido: Optional[str] = None) -> str:
        with self._lock:
            if sesion_id in self.asignaciones:
                return self.asignaciones[sesion_id]

            disponibles = [v for v in self.vehiculos.values() if v.estado_servicio == "disponible"]
            elegido = None
            if tipo_preferido:
                for veh in disponibles:
                    if veh.tipo == tipo_preferido:
                        elegido = veh
                        break
            if not elegido and disponibles:
                elegido = disponibles[0]
            if not elegido:
                vid = f"{tipo_preferido or 'unidad'}-{uuid.uuid4().hex[:6]}"
                elegido = crear_vehiculo(vid, tipo=tipo_preferido or "policia")
                self.vehiculos[vid] = elegido

            self.asignaciones[sesion_id] = elegido.id
            return elegido.id

    def obtener_vehiculo(self, vid: str) -> Optional[VehiculoBase]:
        with self._lock:
            return self.vehiculos.get(vid)

    def obtener_todos(self) -> Dict[str, VehiculoBase]:
        with self._lock:
            return dict(self.vehiculos)

    def actualizar(self, factor_entorno: float = 1.0) -> None:
        with self._lock:
            for veh in self.vehiculos.values():
                veh.actualizar_factor_entorno(factor_entorno)
                veh.actualizar_simulacion(delta_time=INTERVALO_ACTUALIZACION)
                self._actualizar_incidente(veh)

    def _actualizar_incidente(self, veh: VehiculoBase) -> None:
        if not veh.incidente:
            return

        if veh.gps.progreso_ruta >= 0.98:
            veh.estado_servicio = "en_escena"
            veh.disponibilidad = "busy"
            incidente = veh.incidente
            inicio = incidente.get("started_at")
            duracion = incidente.get("duracion_min", 15)
            if incidente.get("status") != "resolved":
                incidente["status"] = "on_scene"
            incidente["elapsed"] = incidente.get("elapsed", 0) + INTERVALO_ACTUALIZACION
            if incidente["elapsed"] >= duracion * 60:
                incidente["status"] = "resolved"
                veh.cerrar_incidente()
                if inicio:
                    self.incidentes[incidente["incident_id"]] = incidente

    def manejar_evento(self, evento: dict) -> Optional[str]:
        tipo_evento = evento.get("type")
        destino = (evento.get("latitude"), evento.get("longitude"))
        if not destino[0] or not destino[1]:
            return None

        tipo_veh = self._mapear_tipo_evento(tipo_evento)
        incidente_id = evento.get("id") or f"INC-{uuid.uuid4().hex[:6]}"
        incidente = {
            "incident_id": incidente_id,
            "incident_type": tipo_evento,
            "incident_status": "assigned",
            "title": evento.get("title", tipo_evento),
            "severity": evento.get("severity", "medium"),
            "lat": destino[0],
            "lon": destino[1],
            "started_at": evento.get("started_at"),
            "duracion_min": self._duracion_por_severidad(evento.get("severity")),
            "status": "assigned",
            "elapsed": 0.0
        }

        vehiculo = self._buscar_disponible(tipo_veh)
        if not vehiculo:
            self.incidentes[incidente_id] = incidente
            return None

        ruta = generar_ruta((vehiculo.gps.latitud, vehiculo.gps.longitud), destino)
        if ruta and len(ruta) >= 2:
            vehiculo.gps.establecer_ruta(ruta)
            vehiculo.version_ruta += 1
        vehiculo.asignar_incidente(incidente)
        return vehiculo.id

    def _buscar_disponible(self, tipo: str) -> Optional[VehiculoBase]:
        for veh in self.vehiculos.values():
            if veh.tipo == tipo and veh.estado_servicio == "disponible":
                return veh
        return None

    def _mapear_tipo_evento(self, tipo_evento: str) -> str:
        mapa = {
            "fire": "bomberos",
            "hazmat_spill": "bomberos",
            "medical_emergency": "ambulancia",
            "accident": "policia",
            "lane_closure": "policia",
            "construction": "policia",
            "storm": "policia",
            "flood": "bomberos",
            "public_event": "policia",
            "power_outage": "policia"
        }
        return mapa.get(tipo_evento or "", "policia")

    def _duracion_por_severidad(self, severidad: Optional[str]) -> int:
        if severidad == "critical":
            return 45
        if severidad == "high":
            return 30
        if severidad == "medium":
            return 20
        return 10

    def loop_actualizacion(self, factor_entorno_cb=None) -> None:
        while True:
            try:
                factor = factor_entorno_cb() if factor_entorno_cb else 1.0
                self.actualizar(factor_entorno=factor)
                time.sleep(INTERVALO_ACTUALIZACION)
            except Exception as exc:
                logger.warning("Error en loop flota: %s", exc)
                time.sleep(1)
