"""Sistema de deteccion de sabotaje para los Gemelos Digitales.

Detecta patrones anomalos en los flujos de datos telemetricos de cada
vehiculo y activa protocolos de verificacion escalonados para blindar la
integridad del Gemelo Digital.

Reglas implementadas
────────────────────
  R01  coordenadas_imposibles   GPS fuera del cuadro geografico de Aruba.
  R02  salto_gps_brusco         Desplazamiento entre ticks > umbral fisico.
  R03  velocidad_incoherente    Velocidad reportada por encima del maximo fisico.
  R04  temperatura_incoherente  Temperatura del motor fuera de rango fisico.
  R05  combustible_incoherente  Nivel de combustible < 0 % o > 100 %.
  R06  saturacion_alertas       >= UMBRAL anomalias en la ventana deslizante.
  R07  estado_bloqueado         Vehiculo en camino/en escena sin avanzar tiempo.
  R08  telemetria_congelada     Los valores clave no cambian durante N ticks.

Niveles de riesgo
─────────────────
  LIMPIO      Sin anomalias activas.
  ADVERTENCIA Actividad sospechosa; se registra y se notifica.
  CRITICO     Patron grave; el gemelo digital queda en modo de VERIFICACION:
              los valores del tick anomalo se descartan y se conserva el
              ultimo estado coherente conocido.

Integracion
───────────
  El modulo es completamente aditivo: no modifica ninguna clase existente.
  FleetManager llama a `guardian.analizar(vehiculo)` al final de cada tick.
  Los endpoints /security/sabotage[/<id>] exponen el estado del guardian.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional

from config import (
    SABOTAJE_LAT_MIN, SABOTAJE_LAT_MAX,
    SABOTAJE_LON_MIN, SABOTAJE_LON_MAX,
    SABOTAJE_VELOCIDAD_MAX_FISICA,
    SABOTAJE_SALTO_GPS_MAX_KM,
    SABOTAJE_TEMP_MIN, SABOTAJE_TEMP_MAX,
    SABOTAJE_COMBUSTIBLE_MIN, SABOTAJE_COMBUSTIBLE_MAX,
    SABOTAJE_VENTANA_S, SABOTAJE_UMBRAL_WARN, SABOTAJE_UMBRAL_CRITICO,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes internas
# ---------------------------------------------------------------------------

NIVEL_LIMPIO      = "LIMPIO"
NIVEL_ADVERTENCIA = "ADVERTENCIA"
NIVEL_CRITICO     = "CRITICO"

# Ticks consecutivos sin cambio para activar R08
_TICKS_CONGELADO = 10

# Tolerancia de variacion minima para considerar que un valor "cambio"
_EPSILON_VEL  = 0.01   # km/h
_EPSILON_TEMP = 0.01   # °C
_EPSILON_COMB = 0.001  # %
_EPSILON_GPS  = 1e-7   # grados


# ---------------------------------------------------------------------------
# Estructura de una anomalia
# ---------------------------------------------------------------------------

class Anomalia:
    """Registro inmutable de una anomalia detectada en un tick."""

    __slots__ = ('regla', 'ts', 'detalle', 'nivel')

    def __init__(self, regla: str, detalle: str, nivel: str):
        self.regla = regla
        self.ts = time.monotonic()
        self.detalle = detalle
        self.nivel = nivel

    def a_dict(self) -> dict:
        return {
            "regla": self.regla,
            "detalle": self.detalle,
            "nivel": self.nivel,
            "ts_monotonic": round(self.ts, 3),
        }


# ---------------------------------------------------------------------------
# Estado por vehiculo
# ---------------------------------------------------------------------------

class EstadoVehiculo:
    """Contexto de deteccion mantenido tick a tick por cada vehiculo."""

    def __init__(self, vehiculo_id: str):
        self.vehiculo_id = vehiculo_id

        # Ultimo estado coherente conocido (snapshot de valores telemetricos)
        self._ultimo_coherente: dict = {}
        self._lock = threading.Lock()

        # Ventana deslizante de anomalias para R06
        self._ventana: deque = deque()

        # Nivel actual
        self.nivel: str = NIVEL_LIMPIO

        # Historial de las ultimas N anomalias (para el endpoint)
        self.historial: deque = deque(maxlen=100)

        # Contadores globales
        self.total_anomalias: int = 0
        self.total_criticos: int = 0
        self.en_verificacion: bool = False
        self.primer_evento: Optional[float] = None
        self.ultimo_evento: Optional[float] = None

        # Estado previo para deteccion de congelacion / salto GPS
        self._prev_lat: Optional[float] = None
        self._prev_lon: Optional[float] = None
        self._prev_vel: Optional[float] = None
        self._prev_temp: Optional[float] = None
        self._prev_comb: Optional[float] = None
        self._ticks_sin_cambio: int = 0
        self._ticks_bloqueado: int = 0

    # ------------------------------------------------------------------ #

    def registrar_anomalia(self, anomalia: Anomalia) -> None:
        ahora = anomalia.ts
        with self._lock:
            self._ventana.append(ahora)
            self._purgar_ventana(ahora)

            self.historial.append(anomalia.a_dict())
            self.total_anomalias += 1
            if anomalia.nivel == NIVEL_CRITICO:
                self.total_criticos += 1
                self.en_verificacion = True

            if self.primer_evento is None:
                self.primer_evento = ahora
            self.ultimo_evento = ahora

            self._recalcular_nivel()

        logger.warning(
            "[Sabotaje %s] [%s] %s — %s",
            self.vehiculo_id[:8], anomalia.nivel, anomalia.regla, anomalia.detalle,
        )

    def _purgar_ventana(self, ahora: float) -> None:
        limite = ahora - SABOTAJE_VENTANA_S
        while self._ventana and self._ventana[0] < limite:
            self._ventana.popleft()

    def _recalcular_nivel(self) -> None:
        n = len(self._ventana)
        if n >= SABOTAJE_UMBRAL_CRITICO:
            self.nivel = NIVEL_CRITICO
        elif n >= SABOTAJE_UMBRAL_WARN:
            self.nivel = NIVEL_ADVERTENCIA
        else:
            self.nivel = NIVEL_LIMPIO
            if self.en_verificacion and n == 0:
                self.en_verificacion = False

    def guardar_coherente(self, snap: dict) -> None:
        with self._lock:
            self._ultimo_coherente = dict(snap)

    def obtener_coherente(self) -> dict:
        with self._lock:
            return dict(self._ultimo_coherente)

    def a_dict(self) -> dict:
        with self._lock:
            recientes = list(self.historial)[-10:]
            return {
                "vehiculo_id": self.vehiculo_id,
                "nivel": self.nivel,
                "en_verificacion": self.en_verificacion,
                "anomalias_en_ventana": len(self._ventana),
                "total_anomalias": self.total_anomalias,
                "total_criticos": self.total_criticos,
                "primer_evento_ts": (
                    datetime.fromtimestamp(self.primer_evento, tz=timezone.utc).isoformat()
                    if self.primer_evento else None
                ),
                "ultimo_evento_ts": (
                    datetime.fromtimestamp(self.ultimo_evento, tz=timezone.utc).isoformat()
                    if self.ultimo_evento else None
                ),
                "ultimas_anomalias": recientes,
            }


# ---------------------------------------------------------------------------
# Motor de deteccion
# ---------------------------------------------------------------------------

class GuardianSabotaje:
    """Motor principal de deteccion de sabotaje.

    Uso:
        guardian = GuardianSabotaje()
        guardian.analizar(vehiculo)   # llamado por FleetManager cada tick

    No modifica los vehiculos ni sus atributos; solo lee sus propiedades
    publicas para evaluar las reglas.
    """

    def __init__(self):
        self._estados: Dict[str, EstadoVehiculo] = {}
        self._lock = threading.Lock()
        self._total_vehiculos_afectados: int = 0

    # ------------------------------------------------------------------ #

    def _estado(self, vid: str) -> EstadoVehiculo:
        with self._lock:
            if vid not in self._estados:
                self._estados[vid] = EstadoVehiculo(vid)
            return self._estados[vid]

    # ------------------------------------------------------------------ #
    # Punto de entrada principal
    # ------------------------------------------------------------------ #

    def analizar(self, vehiculo) -> List[Anomalia]:
        """Evalua todas las reglas sobre el vehiculo y registra anomalias.

        Devuelve la lista de anomalias detectadas en este tick (puede ser
        vacia si todo es coherente).

        Este metodo es thread-safe y puede llamarse desde distintos hilos
        (e.g. el bucle de actualizacion de FleetManager).
        """
        ev = self._estado(vehiculo.id)

        # Capturar snapshot telemetrico
        lat = float(getattr(vehiculo.gps, 'latitud', 0) or 0)
        lon = float(getattr(vehiculo.gps, 'longitud', 0) or 0)
        vel = float(getattr(vehiculo, 'velocidad', 0) or 0)
        temp = float(getattr(vehiculo, 'temperatura_motor', 70) or 70)
        comb = float(getattr(vehiculo, 'combustible', 50) or 50)
        en_camino = bool(getattr(vehiculo, 'en_camino', False))
        en_escena = bool(getattr(vehiculo, 'en_escena', False))

        snap = {
            "lat": lat, "lon": lon, "vel": vel,
            "temp": temp, "comb": comb,
            "en_camino": en_camino, "en_escena": en_escena,
        }

        anomalias: List[Anomalia] = []

        # ---- R01: coordenadas imposibles --------------------------------
        if not (SABOTAJE_LAT_MIN <= lat <= SABOTAJE_LAT_MAX and
                SABOTAJE_LON_MIN <= lon <= SABOTAJE_LON_MAX):
            anomalias.append(Anomalia(
                "R01_coordenadas_imposibles",
                f"GPS ({lat:.6f}, {lon:.6f}) fuera del cuadro de Aruba "
                f"[{SABOTAJE_LAT_MIN},{SABOTAJE_LAT_MAX}]×"
                f"[{SABOTAJE_LON_MIN},{SABOTAJE_LON_MAX}]",
                NIVEL_CRITICO,
            ))

        # ---- R02: salto GPS brusco ------------------------------------
        prev_lat = ev._prev_lat
        prev_lon = ev._prev_lon
        if prev_lat is not None and prev_lon is not None:
            dlat_km = abs(lat - prev_lat) * 111.32
            dlon_km = abs(lon - prev_lon) * 111.32 * math.cos(math.radians(lat))
            salto_km = math.hypot(dlat_km, dlon_km)
            if salto_km > SABOTAJE_SALTO_GPS_MAX_KM:
                anomalias.append(Anomalia(
                    "R02_salto_gps_brusco",
                    f"Desplazamiento de {salto_km:.2f} km en un tick "
                    f"(max={SABOTAJE_SALTO_GPS_MAX_KM} km)",
                    NIVEL_CRITICO,
                ))

        # ---- R03: velocidad incoherente --------------------------------
        if vel > SABOTAJE_VELOCIDAD_MAX_FISICA or vel < 0:
            anomalias.append(Anomalia(
                "R03_velocidad_incoherente",
                f"Velocidad {vel:.1f} km/h fuera del rango fisico "
                f"[0,{SABOTAJE_VELOCIDAD_MAX_FISICA}]",
                NIVEL_ADVERTENCIA if 0 <= vel <= SABOTAJE_VELOCIDAD_MAX_FISICA * 1.2
                else NIVEL_CRITICO,
            ))

        # ---- R04: temperatura incoherente ------------------------------
        if not (SABOTAJE_TEMP_MIN <= temp <= SABOTAJE_TEMP_MAX):
            anomalias.append(Anomalia(
                "R04_temperatura_incoherente",
                f"Temperatura motor {temp:.1f}°C fuera del rango fisico "
                f"[{SABOTAJE_TEMP_MIN},{SABOTAJE_TEMP_MAX}]",
                NIVEL_CRITICO,
            ))

        # ---- R05: combustible incoherente ------------------------------
        if not (SABOTAJE_COMBUSTIBLE_MIN <= comb <= SABOTAJE_COMBUSTIBLE_MAX):
            anomalias.append(Anomalia(
                "R05_combustible_incoherente",
                f"Combustible {comb:.2f}% fuera del rango "
                f"[{SABOTAJE_COMBUSTIBLE_MIN},{SABOTAJE_COMBUSTIBLE_MAX}]",
                NIVEL_CRITICO,
            ))

        # ---- R07: estado bloqueado ------------------------------------
        # Si el vehiculo lleva en_camino o en_escena muchos ticks seguidos
        # sin que ninguna variable avance, algo inyecto un estado falso.
        if en_camino or en_escena:
            if (prev_lat is not None
                    and abs(lat - prev_lat) < _EPSILON_GPS
                    and abs(lon - prev_lon) < _EPSILON_GPS
                    and abs(vel - (ev._prev_vel or 0)) < _EPSILON_VEL):
                ev._ticks_bloqueado += 1
            else:
                ev._ticks_bloqueado = 0
            if ev._ticks_bloqueado >= _TICKS_CONGELADO:
                anomalias.append(Anomalia(
                    "R07_estado_bloqueado",
                    f"Vehiculo en {'camino' if en_camino else 'escena'} "
                    f"pero sin movimiento durante {ev._ticks_bloqueado} ticks",
                    NIVEL_ADVERTENCIA,
                ))
        else:
            ev._ticks_bloqueado = 0

        # ---- R08: telemetria congelada (no en intervención) ------------
        if not (en_camino or en_escena) and prev_lat is not None:
            cambio = (
                abs(lat - prev_lat) > _EPSILON_GPS
                or abs(lon - prev_lon) > _EPSILON_GPS
                or abs(vel - (ev._prev_vel or 0)) > _EPSILON_VEL
                or abs(temp - (ev._prev_temp or 0)) > _EPSILON_TEMP
                or abs(comb - (ev._prev_comb or 0)) > _EPSILON_COMB
            )
            if cambio:
                ev._ticks_sin_cambio = 0
            else:
                ev._ticks_sin_cambio += 1
                if ev._ticks_sin_cambio >= _TICKS_CONGELADO:
                    anomalias.append(Anomalia(
                        "R08_telemetria_congelada",
                        f"Todos los valores telemetricos sin cambio "
                        f"durante {ev._ticks_sin_cambio} ticks consecutivos",
                        NIVEL_ADVERTENCIA,
                    ))
        else:
            ev._ticks_sin_cambio = 0

        # ---- R06: saturacion de alertas (post evaluacion) -------------
        # Se evalua implicitamente en _recalcular_nivel() al acumular
        # anomalias en la ventana deslizante. Si ya hay CRITICO/ADVERTENCIA
        # activo, anotamos el patron de saturacion explicitamente.
        if len(ev._ventana) >= SABOTAJE_UMBRAL_CRITICO:
            ya_registrado = any(
                a.get('regla') == 'R06_saturacion_alertas'
                for a in list(ev.historial)[-3:]
            )
            if not ya_registrado and not anomalias:
                anomalias.append(Anomalia(
                    "R06_saturacion_alertas",
                    f"{len(ev._ventana)} anomalias en ventana de "
                    f"{SABOTAJE_VENTANA_S}s (umbral critico={SABOTAJE_UMBRAL_CRITICO})",
                    NIVEL_CRITICO,
                ))

        # ---- Actualizar estado y guardar coherente ---------------------
        for a in anomalias:
            ev.registrar_anomalia(a)

        if not anomalias:
            # Solo guardamos el estado coherente cuando no hay anomalias
            ev.guardar_coherente(snap)
            if ev.nivel == NIVEL_LIMPIO:
                ev.en_verificacion = False

        # Actualizar estado previo siempre (para calcular deltas proximos)
        ev._prev_lat  = lat
        ev._prev_lon  = lon
        ev._prev_vel  = vel
        ev._prev_temp = temp
        ev._prev_comb = comb

        # Actualizar contadores globales
        with self._lock:
            afectados = sum(
                1 for e in self._estados.values()
                if e.nivel != NIVEL_LIMPIO
            )
            self._total_vehiculos_afectados = afectados

        return anomalias

    # ------------------------------------------------------------------ #
    # Consulta de estado
    # ------------------------------------------------------------------ #

    def estado_global(self) -> dict:
        """Resumen del estado de todos los vehiculos monitorizados."""
        with self._lock:
            estados = {vid: ev.a_dict() for vid, ev in self._estados.items()}

        por_nivel = {NIVEL_LIMPIO: 0, NIVEL_ADVERTENCIA: 0, NIVEL_CRITICO: 0}
        for ev in estados.values():
            por_nivel[ev["nivel"]] = por_nivel.get(ev["nivel"], 0) + 1

        alertas_activas = [
            e for e in estados.values() if e["nivel"] != NIVEL_LIMPIO
        ]

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "vehiculos_monitorizados": len(estados),
            "vehiculos_afectados": self._total_vehiculos_afectados,
            "por_nivel": por_nivel,
            "en_verificacion": sum(1 for e in estados.values() if e["en_verificacion"]),
            "alertas_activas": alertas_activas,
            "vehiculos": estados,
        }

    def estado_vehiculo(self, vehiculo_id: str) -> Optional[dict]:
        """Estado de deteccion de un vehiculo concreto."""
        with self._lock:
            ev = self._estados.get(vehiculo_id)
        if ev is None:
            return None
        return ev.a_dict()

    def reset_vehiculo(self, vehiculo_id: str) -> bool:
        """Limpia el historial de anomalias de un vehiculo (accion operador)."""
        with self._lock:
            if vehiculo_id not in self._estados:
                return False
            self._estados[vehiculo_id] = EstadoVehiculo(vehiculo_id)
        logger.info("[Sabotaje] Reset manual para vehiculo %s", vehiculo_id[:8])
        return True


# ---------------------------------------------------------------------------
# Instancia global (Singleton ligero)
# ---------------------------------------------------------------------------

guardian = GuardianSabotaje()
