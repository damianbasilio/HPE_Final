import logging
import math
import random
from datetime import datetime
from typing import Optional

from config import (
    RANGO_COMBUSTIBLE_INICIAL,
    RANGO_KM_INICIAL,
    RANGO_TEMP_INICIAL,
    RANGO_ACEITE_INICIAL,
    RANGO_DESGASTE_FRENOS,
    RANGO_DESGASTE_NEUMATICOS,
    TEMP_AMBIENTE,
    TEMP_MAXIMA,
    VELOCIDAD_MAXIMA
)
from costos import obtener_tarifa
from gps import SimuladorGPS
from rutas import generar_ruta_patrulla

logger = logging.getLogger(__name__)


VELOCIDAD_BASE = {
    "policia": 60,
    "ambulancia": 65,
    "bomberos": 55,
    "dron": 80
}


class VehiculoBase:
    def __init__(self, vid: str, tipo: str, energia: str = "combustion", nombre: Optional[str] = None):
        self.id = vid
        self.tipo = tipo
        self.energia = energia
        self.nombre = nombre or f"{tipo}-{vid[:6]}"
        self.gps = SimuladorGPS()

        self._init_telemetria()
        self._init_operativo()
        self._init_especialidad()

        self.rastro = []
        self.version_ruta = 0
        self._iniciar_patrulla()

    def _init_telemetria(self) -> None:
        self.energia_nivel = random.uniform(*RANGO_COMBUSTIBLE_INICIAL)
        self.km_totales = random.randint(*RANGO_KM_INICIAL)
        self.temperatura_motor = random.uniform(*RANGO_TEMP_INICIAL)
        self.nivel_aceite = random.uniform(*RANGO_ACEITE_INICIAL)
        self.desgaste_frenos = random.uniform(*RANGO_DESGASTE_FRENOS)
        self.desgaste_neumaticos = random.uniform(*RANGO_DESGASTE_NEUMATICOS)

        self.velocidad = 0.0
        self.velocidad_objetivo = VELOCIDAD_BASE.get(self.tipo, 50)
        self.aceleracion_max = 6
        self.consumo_factor = 1.0
        self.temp_factor = 1.0
        self.desgaste_factor = 1.0

    def _init_operativo(self) -> None:
        self.estado_servicio = "disponible"
        self.disponibilidad = "idle"
        self.prioridad = "normal"
        self.incidente = None
        self.escenario_activo = "patrulla"
        self.en_movimiento = True
        self.en_escena = False
        self.en_camino = False
        self.tiempo_incidente = 0.0
        self.costos = {"activacion": 0.0, "acumulado": 0.0, "minuto": 0.0}
        self.factor_entorno = 1.0

    def _init_especialidad(self) -> None:
        self.especialidad = {}
        if self.tipo == "ambulancia":
            self.especialidad = {
                "patient_heart_rate": random.randint(80, 110),
                "patient_spo2": random.randint(92, 99),
                "patient_status": "stable"
            }
        elif self.tipo == "policia":
            self.especialidad = {
                "risk_level": random.choice(["low", "medium", "high"]),
                "protocol": random.choice(["containment", "surveillance", "intercept"])
            }
        elif self.tipo == "bomberos":
            self.especialidad = {
                "water_pressure_bar": random.randint(4, 8),
                "foam_level_pct": random.randint(60, 100)
            }
        elif self.tipo == "dron":
            self.especialidad = {
                "altitude_m": random.randint(40, 120),
                "battery_health_pct": random.randint(80, 100)
            }

    def _iniciar_patrulla(self) -> None:
        try:
            ruta = generar_ruta_patrulla()
            if ruta and len(ruta) >= 2:
                self.gps.establecer_ruta(ruta)
                self.version_ruta += 1
        except Exception as exc:
            logger.warning("Error generando ruta patrulla: %s", exc)

    def asignar_incidente(self, incidente: dict) -> None:
        self.incidente = incidente
        self.escenario_activo = incidente.get("title", "incidente")
        self.estado_servicio = "en_ruta"
        self.disponibilidad = "busy"
        self.prioridad = incidente.get("severity", "medium")
        self.en_camino = True
        self.en_escena = False
        self.tiempo_incidente = 0.0
        self._activar_costos()

    def cerrar_incidente(self) -> None:
        self.incidente = None
        self.escenario_activo = "patrulla"
        self.estado_servicio = "disponible"
        self.disponibilidad = "idle"
        self.prioridad = "normal"
        self.en_camino = False
        self.en_escena = False
        self.tiempo_incidente = 0.0
        self.costos["acumulado"] = 0.0
        self.costos["activacion"] = 0.0
        self._iniciar_patrulla()

    def _activar_costos(self) -> None:
        tarifa = obtener_tarifa(self.tipo, self.energia)
        self.costos["minuto"] = tarifa.get("minuto", 0.0)
        self.costos["activacion"] = tarifa.get("activacion", 0.0)
        self.costos["acumulado"] = tarifa.get("activacion", 0.0)

    def actualizar_factor_entorno(self, factor: float) -> None:
        self.factor_entorno = max(0.3, min(1.0, factor))

    def actualizar_simulacion(self, delta_time: float) -> None:
        self._actualizar_tiempo(delta_time)
        objetivo = self.velocidad_objetivo * self.factor_entorno
        self._actualizar_velocidad(objetivo, delta_time)
        self._actualizar_motor(delta_time)
        self._actualizar_consumo(delta_time)
        self._actualizar_desgaste(delta_time)
        self._actualizar_kilometraje(delta_time)

        if self.velocidad > 0:
            self.gps.actualizar(self.velocidad, delta_time)

        self._acumular_rastro()
        self._verificar_ruta_completada()
        self._actualizar_costos(delta_time)

    def _actualizar_tiempo(self, delta_time: float) -> None:
        if self.incidente:
            self.tiempo_incidente += delta_time

    def _actualizar_velocidad(self, objetivo: float, delta_time: float) -> None:
        objetivo = min(VELOCIDAD_MAXIMA, max(0, objetivo))
        diff = objetivo - self.velocidad
        if abs(diff) > self.aceleracion_max * delta_time:
            self.velocidad += math.copysign(self.aceleracion_max * delta_time, diff)
        else:
            self.velocidad = objetivo

        if self.velocidad > 0:
            self.velocidad = max(0, self.velocidad + random.uniform(-0.4, 0.4))

    def _actualizar_motor(self, delta_time: float) -> None:
        if self.velocidad > 0:
            incremento = (self.velocidad / 100.0) * 0.2 * self.temp_factor * delta_time
            self.temperatura_motor += incremento

        enfriamiento = 1.0 * delta_time
        if self.temperatura_motor > TEMP_AMBIENTE:
            tasa = 2.0 if self.velocidad == 0 else 0.2
            self.temperatura_motor = max(TEMP_AMBIENTE, self.temperatura_motor - enfriamiento * tasa)

        self.temperatura_motor = max(TEMP_AMBIENTE, min(self.temperatura_motor, TEMP_MAXIMA))

    def _actualizar_consumo(self, delta_time: float) -> None:
        if self.velocidad > 0:
            consumo = (self.velocidad / 100.0) * 0.01 * self.consumo_factor * delta_time
            self.energia_nivel = max(0, self.energia_nivel - consumo)

    def _actualizar_desgaste(self, delta_time: float) -> None:
        if self.velocidad > 0:
            factor = (self.velocidad / 200.0) * 0.05 * self.desgaste_factor * delta_time
            self.desgaste_frenos = min(100, self.desgaste_frenos + factor * 0.3)
            self.desgaste_neumaticos = min(100, self.desgaste_neumaticos + factor * 0.5)

    def _actualizar_kilometraje(self, delta_time: float) -> None:
        metros = (self.velocidad / 3.6) * delta_time
        km = metros / 1000.0
        self.km_totales += km

    def _actualizar_costos(self, delta_time: float) -> None:
        if not self.incidente:
            return
        minutos = delta_time / 60.0
        self.costos["acumulado"] += self.costos.get("minuto", 0.0) * minutos

    def _acumular_rastro(self) -> None:
        if self.velocidad <= 0:
            return
        pos = [round(self.gps.latitud, 6), round(self.gps.longitud, 6)]
        if self.rastro and self.rastro[-1] == pos:
            return
        self.rastro.append(pos)
        if len(self.rastro) > 200:
            self.rastro = self.rastro[-200:]

    def _verificar_ruta_completada(self) -> None:
        if self.gps.progreso_ruta >= 0.99 and not self.incidente:
            self._iniciar_patrulla()

    def obtener_estado(self) -> dict:
        return {
            "timestamp": datetime.now().isoformat(),
            "id": self.id,
            "tipo": self.tipo,
            "energia": self.energia,
            "nombre": self.nombre,
            "combustible": round(self.energia_nivel, 1),
            "temperatura_motor": round(self.temperatura_motor, 1),
            "km_totales": round(self.km_totales, 1),
            "nivel_aceite": round(self.nivel_aceite, 1),
            "desgaste_frenos": round(self.desgaste_frenos, 1),
            "desgaste_neumaticos": round(self.desgaste_neumaticos, 1),
            "velocidad": round(self.velocidad, 1),
            "velocidad_objetivo": round(self.velocidad_objetivo, 1),
            "estado_servicio": self.estado_servicio,
            "disponibilidad": self.disponibilidad,
            "prioridad": self.prioridad,
            "gps": self.gps.obtener_coordenadas(),
            "incidente": self.incidente,
            "costos": {
                "activacion": round(self.costos.get("activacion", 0.0), 2),
                "acumulado": round(self.costos.get("acumulado", 0.0), 2),
                "minuto": round(self.costos.get("minuto", 0.0), 2)
            },
            "especialidad": self.especialidad
        }

    def obtener_estado_broadcast(self) -> dict:
        return {
            "id": self.id,
            "tipo": self.tipo,
            "energia": self.energia,
            "nombre": self.nombre,
            "combustible": round(self.energia_nivel, 1),
            "temperatura_motor": round(self.temperatura_motor, 1),
            "km_totales": round(self.km_totales, 1),
            "nivel_aceite": round(self.nivel_aceite, 1),
            "desgaste_frenos": round(self.desgaste_frenos, 1),
            "desgaste_neumaticos": round(self.desgaste_neumaticos, 1),
            "velocidad": round(self.velocidad, 1),
            "estado_servicio": self.estado_servicio,
            "disponibilidad": self.disponibilidad,
            "prioridad": self.prioridad,
            "gps": self.gps.obtener_coordenadas_ligero(),
            "incidente": self.incidente,
            "costos": {
                "acumulado": round(self.costos.get("acumulado", 0.0), 2)
            },
            "especialidad": self.especialidad
        }


def crear_vehiculo(vid: str, tipo: str, energia: str = "combustion", nombre: Optional[str] = None) -> VehiculoBase:
    return VehiculoBase(vid=vid, tipo=tipo, energia=energia, nombre=nombre)
