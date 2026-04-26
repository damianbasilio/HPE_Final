"""Gestion de simulaciones (live + replay).

Proporciona dos modos:

* `live` (tiempo real): vista del estado actual de la flota y del bus Kafka,
  consultable a traves de la misma API que las replays.
* `replay`: re-ejecuta el histórico del bus Kafka desde una fecha pasada
  (>= REPLAY_FECHA_MIN, por defecto 2026-04-01) hasta una fecha objetivo,
  reconstruyendo:
    - un FleetManager *aislado* (los vehiculos parten de un estado inicial
      simulado y NO afectan al sistema en produccion),
    - el factor climatico derivado del topic `aruba.weather`,
    - la cadena de decisiones tomadas por el despacho ante los eventos del
      topic `aruba.events`,
  con reloj virtual configurable (acelerar / ralentizar / pausar) y
  posicionamiento del consumidor por timestamp (offsets_for_times).

El usuario puede consultar el estado completo (`snapshot`) en cualquier
momento, modificar la velocidad, pausar/reanudar y detener la simulacion.
"""

from __future__ import annotations

import heapq
import logging
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from config import (
    KAFKA_TOPIC_CLIMA,
    KAFKA_TOPIC_EVENTOS,
    REPLAY_FECHA_MIN,
    REPLAY_TICK_VIRTUAL_SEG,
    REPLAY_VELOCIDAD_MAX,
    REPLAY_VELOCIDAD_MIN,
)
from entorno import interpretar_clima
from flota import FleetManager

logger = logging.getLogger(__name__)


def _ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parsear_iso(valor) -> Optional[datetime]:
    """Convierte un valor ISO-8601 (o datetime) a datetime UTC con tz."""
    if not valor:
        return None
    if isinstance(valor, datetime):
        return valor.astimezone(timezone.utc) if valor.tzinfo else valor.replace(tzinfo=timezone.utc)
    try:
        s = str(valor).replace('Z', '+00:00').strip()
        dt = datetime.fromisoformat(s)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _ts_evento(valor: dict, ts_kafka_ms: Optional[int]) -> Optional[datetime]:
    """Extrae el timestamp logico de un mensaje Kafka.

    Prioriza los campos del payload (`started_at`, `timestamp`, `observed_at`...)
    y usa el `record.timestamp` del broker como fallback.
    """
    if isinstance(valor, dict):
        for clave in ('started_at', 'timestamp', 'observed_at', 'time',
                      'created_at', 'ts'):
            ts = _parsear_iso(valor.get(clave))
            if ts:
                return ts
    if ts_kafka_ms and ts_kafka_ms > 0:
        try:
            return datetime.fromtimestamp(ts_kafka_ms / 1000.0, tz=timezone.utc)
        except (ValueError, OSError):
            return None
    return None


def _es_lectura_clima(valor: dict) -> bool:
    if not isinstance(valor, dict):
        return False
    if valor.get('station_id') and (
        valor.get('temperature_c') is not None
        or valor.get('humidity_pct') is not None
        or valor.get('precipitation_mm') is not None
        or valor.get('wind_speed_kmh') is not None
    ):
        return True
    return False


def _validar_velocidad(speed: float) -> float:
    try:
        s = float(speed)
    except (TypeError, ValueError):
        s = 1.0
    return max(REPLAY_VELOCIDAD_MIN, min(REPLAY_VELOCIDAD_MAX, s))


# Marca usada en heapq para desempatar timestamps iguales y mantener el orden
# de llegada (sin comparar dicts, que no son ordenables en Python 3).
_secuencia_global = 0
_secuencia_lock = threading.Lock()


def _siguiente_secuencia() -> int:
    global _secuencia_global
    with _secuencia_lock:
        _secuencia_global += 1
        return _secuencia_global


class _SimulacionBase:
    """Interfaz comun para simulaciones live y replay."""

    sim_id: str = ""
    modo: str = ""

    def estado_dict(self) -> dict:  # pragma: no cover - implementada por subclases
        raise NotImplementedError

    def snapshot(self, decisiones_limit: int = 50,
                 eventos_limit: int = 50) -> dict:  # pragma: no cover
        raise NotImplementedError


class SimulacionTiempoReal(_SimulacionBase):
    """Vista del estado en tiempo real del sistema en produccion."""

    def __init__(self, fleet: FleetManager, bus):
        self.sim_id = "live"
        self.modo = "tiempo_real"
        self._fleet = fleet
        self._bus = bus
        self._iniciado = _ahora_utc()

    def _factor_clima_actual(self) -> Tuple[float, Optional[dict]]:
        try:
            lectura = self._bus.ultimo_clima()
        except Exception:
            lectura = None
        if not lectura:
            return 1.0, None
        clima = interpretar_clima(lectura)
        factor = float(clima.get('condicion', {}).get('factor_velocidad', 1.0) or 1.0)
        return factor, clima

    def estado_dict(self) -> dict:
        factor, clima = self._factor_clima_actual()
        weather_cache = list(getattr(self._bus, '_weather_cache', []) or [])
        events_cache = list(getattr(self._bus, '_events_cache', []) or [])
        return {
            "sim_id": self.sim_id,
            "modo": self.modo,
            "estado": "running",
            "velocidad": 1.0,
            "started_at": self._iniciado.isoformat(),
            "virtual_now": _ahora_utc().isoformat(),
            "cursor": _ahora_utc().isoformat(),
            "topics": [KAFKA_TOPIC_CLIMA, KAFKA_TOPIC_EVENTOS],
            "factor_clima": round(factor, 3),
            "clima_actual": clima,
            "eventos_procesados": len(events_cache),
            "weather_procesados": len(weather_cache),
            "vehiculos_total": len(self._fleet.vehiculos),
            "incidentes_total": len(self._fleet.incidentes),
            "buffer_pendiente": 0,
            "iniciado_en": self._iniciado.isoformat(),
            "terminado_en": None,
        }

    def snapshot(self, decisiones_limit: int = 50, eventos_limit: int = 50) -> dict:
        snap = self._fleet.snapshot(decisiones_limit=decisiones_limit)
        try:
            eventos_recientes = list(self._bus.eventos_recientes(eventos_limit))
        except Exception:
            eventos_recientes = []
        try:
            lecturas_clima = list(getattr(self._bus, '_weather_cache', []) or [])[-eventos_limit:]
        except Exception:
            lecturas_clima = []
        return {
            **self.estado_dict(),
            "vehiculos": snap["vehiculos"],
            "incidentes": snap["incidentes"],
            "asignaciones": snap["asignaciones"],
            "decisiones": snap["decisiones"],
            "eventos_recientes": eventos_recientes,
            "lecturas_clima_recientes": lecturas_clima,
        }


class SimulacionReplay(_SimulacionBase):
    """Replay aislado del histórico Kafka.

    Usa un FleetManager privado para no contaminar el estado en produccion.
    El reloj virtual avanza a velocidad `speed` (1.0 = tiempo real).
    """

    def __init__(self, sim_id: str, started_at: datetime,
                 end_at: Optional[datetime], speed: float, topics: List[str]):
        self.sim_id = sim_id
        self.modo = "replay"
        self.started_at = started_at
        self.end_at = end_at
        self.speed = _validar_velocidad(speed)
        self.topics = list(topics)

        self._fleet = FleetManager()

        self._virtual_now = started_at
        self._iniciado = _ahora_utc()
        self._terminado: Optional[datetime] = None
        self.estado = "starting"

        self._buffer: List[Tuple[datetime, int, str, dict]] = []
        self._buffer_lock = threading.Lock()
        self._consumer_done = False
        self._consumer_error: Optional[str] = None

        self._weather_by_station: Dict[str, dict] = {}
        self._weather_cache: deque = deque(maxlen=200)
        self._events_cache: deque = deque(maxlen=200)
        self._factor_clima = 1.0
        self._clima_actual: Optional[dict] = None

        self.eventos_procesados = 0
        self.weather_procesados = 0
        self.eventos_descartados = 0

        self._stop = threading.Event()
        self._pause = threading.Event()
        self._lock = threading.Lock()

        # Offsets de fin de particion en el momento de crear el consumer.
        # Cuando el consumer haya alcanzado estos offsets habremos leido todo
        # el historico que existia al arrancar el replay.
        self._fin_historico: dict = {}

        self._consumer_thread = threading.Thread(
            target=self._loop_consumer, daemon=True, name=f"replay-cons-{sim_id}",
        )
        self._player_thread = threading.Thread(
            target=self._loop_player, daemon=True, name=f"replay-play-{sim_id}",
        )

    # ------------------------------------------------------------------
    # API publica

    def iniciar(self) -> None:
        self._consumer_thread.start()
        self._player_thread.start()

    def detener(self) -> None:
        self._stop.set()
        self._pause.clear()

    def alternar_pausa(self) -> str:
        if self._pause.is_set():
            self._pause.clear()
            self.estado = "running"
        else:
            self._pause.set()
            self.estado = "paused"
        return self.estado

    def set_velocidad(self, speed: float) -> float:
        self.speed = _validar_velocidad(speed)
        return self.speed

    def estado_dict(self) -> dict:
        with self._buffer_lock:
            pendientes = len(self._buffer)
        return {
            "sim_id": self.sim_id,
            "modo": self.modo,
            "estado": self.estado,
            "velocidad": self.speed,
            "started_at": self.started_at.isoformat(),
            "end_at": self.end_at.isoformat() if self.end_at else None,
            "virtual_now": self._virtual_now.isoformat(),
            "cursor": self._virtual_now.isoformat(),
            "topics": list(self.topics),
            "factor_clima": round(self._factor_clima, 3),
            "clima_actual": self._clima_actual,
            "eventos_procesados": self.eventos_procesados,
            "weather_procesados": self.weather_procesados,
            "eventos_descartados": self.eventos_descartados,
            "vehiculos_total": len(self._fleet.vehiculos),
            "incidentes_total": len(self._fleet.incidentes),
            "buffer_pendiente": pendientes,
            "consumer_done": self._consumer_done,
            "consumer_error": self._consumer_error,
            "iniciado_en": self._iniciado.isoformat(),
            "terminado_en": self._terminado.isoformat() if self._terminado else None,
        }

    def snapshot(self, decisiones_limit: int = 50, eventos_limit: int = 50) -> dict:
        snap = self._fleet.snapshot(decisiones_limit=decisiones_limit)
        return {
            **self.estado_dict(),
            "vehiculos": snap["vehiculos"],
            "incidentes": snap["incidentes"],
            "asignaciones": snap["asignaciones"],
            "decisiones": snap["decisiones"],
            "eventos_recientes": list(self._events_cache)[-eventos_limit:],
            "lecturas_clima_recientes": list(self._weather_cache)[-eventos_limit:],
        }

    # ------------------------------------------------------------------
    # Consumidor Kafka

    def _crear_consumer(self):
        """Crea y configura el KafkaConsumer del replay.

        1. Asigna explicitamente las particiones (sin group_id para no
           interferir con los consumidores de produccion).
        2. Usa offsets_for_times para posicionarse exactamente en el offset
           correspondiente a `started_at` en cada particion.
        3. Captura los end-offsets actuales para saber cuando hemos agotado
           el historico (ver `_ha_alcanzado_fin_historico`).
        """
        from kafka import KafkaConsumer, TopicPartition
        from kafka_bus import _deserializar_json_seguro, _kafka_common_config

        # Sin group_id: el consumer no hace commit ni interfiere con la
        # posicion de los consumidores de produccion.
        consumer = KafkaConsumer(
            group_id=None,
            enable_auto_commit=False,
            value_deserializer=_deserializar_json_seguro,
            session_timeout_ms=15000,
            request_timeout_ms=60000,
            api_version_auto_timeout_ms=10000,
            fetch_max_wait_ms=500,
            **_kafka_common_config(),
        )

        tps: List = []
        for topic in self.topics:
            try:
                particiones = consumer.partitions_for_topic(topic) or set()
            except Exception as exc:
                logger.warning("[%s] partitions_for_topic(%s) fallo: %s",
                               self.sim_id, topic, exc)
                particiones = set()
            for p in sorted(particiones):
                tps.append(TopicPartition(topic, p))

        if not tps:
            consumer.close()
            raise RuntimeError(
                f"No hay particiones disponibles para los topics {self.topics}. "
                "Comprueba que el bus Kafka esta accesible y los topics existen."
            )

        consumer.assign(tps)

        # ---------------------------------------------------------------
        # Capturar end-offsets ANTES de hacer seek para saber hasta donde
        # llega el historico disponible en este momento.
        try:
            fin = consumer.end_offsets(tps)
            self._fin_historico = {tp: off for tp, off in fin.items() if off > 0}
        except Exception as exc:
            logger.warning("[%s] end_offsets fallo, se usara deteccion por idle: %s",
                           self.sim_id, exc)
            self._fin_historico = {}

        # ---------------------------------------------------------------
        # Posicionar el consumidor por timestamp (filtrando por started_at).
        # El topic aruba.weather y aruba.events tienen retencion total del
        # historico desde el 1 de abril, por lo que offsets_for_times
        # devuelve el primer offset >= started_at en cada particion.
        ts_ms = int(self.started_at.timestamp() * 1000)
        try:
            offsets = consumer.offsets_for_times({tp: ts_ms for tp in tps})
        except Exception as exc:
            logger.warning("[%s] offsets_for_times fallo (%s); seek_to_beginning",
                           self.sim_id, exc)
            offsets = {}

        particiones_con_datos = 0
        for tp in tps:
            meta = (offsets or {}).get(tp)
            if meta is not None and getattr(meta, 'offset', None) is not None:
                consumer.seek(tp, meta.offset)
                particiones_con_datos += 1
            else:
                # Sin mensajes en o despues de started_at: ir al final para
                # no procesar mensajes fuera del rango.
                consumer.seek_to_end(tp)

        logger.info(
            "[%s] Consumer replay listo: %d particiones totales, "
            "%d con datos desde %s, end_offsets=%s",
            self.sim_id, len(tps), particiones_con_datos,
            self.started_at.isoformat(),
            {str(tp): off for tp, off in self._fin_historico.items()},
        )
        return consumer

    def _ha_alcanzado_fin_historico(self, consumer) -> bool:
        """True cuando el consumer ha leido todos los mensajes historicos.

        Compara la posicion actual de cada particion con el end-offset capturado
        al inicio del replay. Si no tenemos end-offsets (broker no lo soporto)
        devuelve False y la deteccion cae al mecanismo de idle por tiempo.
        """
        if not self._fin_historico:
            return False
        try:
            for tp, end_off in self._fin_historico.items():
                pos = consumer.position(tp)
                if pos < end_off:
                    return False
            return True
        except Exception:
            return False

    def _loop_consumer(self) -> None:
        consumer = None
        try:
            consumer = self._crear_consumer()
        except Exception as exc:
            logger.exception("[%s] No se pudo crear consumer: %s", self.sim_id, exc)
            self._consumer_error = repr(exc)
            self._consumer_done = True
            self.estado = "error"
            return

        # Contador de polls vacios consecutivos para la deteccion de idle
        # cuando no tenemos end_offsets del broker.
        _IDLE_POLLS_MAX = 20   # 20 * 1 s = 20 s de silencio => historico agotado
        idle_count = 0

        try:
            while not self._stop.is_set():
                # El consumer para de producir datos cuando el reloj virtual
                # ha superado el end_at pedido por el usuario.
                if self.end_at and self._virtual_now >= self.end_at:
                    break

                try:
                    polled = consumer.poll(timeout_ms=1000, max_records=500)
                except Exception as exc:
                    logger.warning("[%s] poll fallo: %s", self.sim_id, exc)
                    self._consumer_error = repr(exc)
                    time.sleep(2.0)
                    continue

                if not polled:
                    idle_count += 1
                    # Metodo 1 (preciso): comprobar offsets reales
                    if not self._consumer_done:
                        if self._ha_alcanzado_fin_historico(consumer):
                            self._consumer_done = True
                            logger.info(
                                "[%s] Consumer: historico agotado (offset-based). "
                                "Esperando nuevos mensajes en tiempo real.",
                                self.sim_id,
                            )
                        # Metodo 2 (fallback): idle prolongado
                        elif idle_count >= _IDLE_POLLS_MAX:
                            self._consumer_done = True
                            logger.info(
                                "[%s] Consumer: %d polls vacios, historico probablemente agotado.",
                                self.sim_id, idle_count,
                            )
                    time.sleep(0.2)
                    continue

                # Recibimos mensajes: reiniciar contador idle y procesar
                idle_count = 0
                aniadidos = 0
                for tp, registros in polled.items():
                    topic = tp.topic
                    for record in registros:
                        valor = record.value
                        if not isinstance(valor, dict):
                            valor = {"raw": valor}

                        # Extraer timestamp logico del mensaje.
                        # Prioridad: campo del payload > timestamp del broker Kafka.
                        ts = _ts_evento(valor, getattr(record, 'timestamp', None))
                        if ts is None:
                            # Fallback: usar el reloj virtual actual del replay
                            ts = self._virtual_now

                        # Filtrar por rango [started_at, end_at].
                        # offsets_for_times garantiza que no leemos antes de
                        # started_at, pero verificamos igualmente por seguridad.
                        if ts < self.started_at:
                            self.eventos_descartados += 1
                            continue
                        if self.end_at and ts > self.end_at:
                            self.eventos_descartados += 1
                            continue

                        self._buffer_push(topic, valor, ts)
                        aniadidos += 1

                if aniadidos:
                    logger.debug("[%s] Consumer +%d eventos (buffer=%d)",
                                 self.sim_id, aniadidos, len(self._buffer))

                # Comprobar fin de historico tras procesar el batch
                if not self._consumer_done and self._ha_alcanzado_fin_historico(consumer):
                    self._consumer_done = True
                    logger.info(
                        "[%s] Consumer: historico agotado tras batch (offset-based).",
                        self.sim_id,
                    )

        except Exception as exc:
            logger.exception("[%s] Loop consumer fallo: %s", self.sim_id, exc)
            self._consumer_error = repr(exc)
        finally:
            self._consumer_done = True
            try:
                if consumer is not None:
                    consumer.close()
            except Exception:
                pass

    def _buffer_push(self, topic: str, valor: dict, ts: datetime) -> None:
        seq = _siguiente_secuencia()
        with self._buffer_lock:
            heapq.heappush(self._buffer, (ts, seq, topic, valor))

    def _buffer_pop_si(self, limite: datetime) -> Optional[Tuple[datetime, str, dict]]:
        with self._buffer_lock:
            if not self._buffer:
                return None
            if self._buffer[0][0] > limite:
                return None
            ts, _seq, topic, valor = heapq.heappop(self._buffer)
        return ts, topic, valor

    # ------------------------------------------------------------------
    # Player (avanza reloj virtual y procesa eventos)

    def _loop_player(self) -> None:
        try:
            self.estado = "running"
            logger.info(
                "[%s] Player replay arrancado. start=%s end=%s speed=%.2f",
                self.sim_id, self.started_at.isoformat(),
                self.end_at.isoformat() if self.end_at else "(presente)",
                self.speed,
            )

            tick_virtual = max(0.05, float(REPLAY_TICK_VIRTUAL_SEG))
            ult_real = time.monotonic()
            acumulador = 0.0

            while not self._stop.is_set():
                if self._pause.is_set():
                    self.estado = "paused"
                    time.sleep(0.2)
                    ult_real = time.monotonic()
                    continue
                self.estado = "running"

                ahora_real = time.monotonic()
                dt_real = max(0.0, ahora_real - ult_real)
                ult_real = ahora_real

                acumulador += dt_real * self.speed

                pasos = 0
                while acumulador >= tick_virtual and pasos < 200 and not self._stop.is_set():
                    nuevo_virtual = self._virtual_now + timedelta(seconds=tick_virtual)
                    # No superar end_at ni el momento actual si no hay end_at.
                    techo = self.end_at or _ahora_utc()
                    if nuevo_virtual > techo:
                        nuevo_virtual = techo
                    self._virtual_now = nuevo_virtual

                    self._procesar_eventos_hasta(self._virtual_now)
                    self._fleet.actualizar(
                        factor_entorno=self._factor_clima,
                        delta_time=tick_virtual,
                    )

                    acumulador -= tick_virtual
                    pasos += 1

                    if nuevo_virtual >= techo:
                        break

                if self._debe_terminar():
                    break

                time.sleep(0.1)
        except Exception as exc:
            logger.exception("[%s] Player replay fallo: %s", self.sim_id, exc)
        finally:
            self.estado = "finished"
            self._terminado = _ahora_utc()
            logger.info(
                "[%s] Replay finalizado: %d eventos, %d weather, virtual=%s",
                self.sim_id, self.eventos_procesados, self.weather_procesados,
                self._virtual_now.isoformat(),
            )

    def _debe_terminar(self) -> bool:
        """Decide si el player debe finalizar el replay.

        Condiciones de parada:
        1. Se llego al end_at pedido por el usuario Y el buffer esta vacio.
        2. No hay end_at pero el consumer ha agotado el historico (offset-based
           o idle), el buffer esta vacio Y el reloj virtual ha llegado o
           superado el momento real actual (hemos reproducido todo).
        """
        with self._buffer_lock:
            buffer_vacio = not self._buffer

        # Caso 1: replay con end_at definido
        if self.end_at and self._virtual_now >= self.end_at:
            return buffer_vacio

        # Caso 2: replay "hasta el presente"
        if self._consumer_done and buffer_vacio:
            # Permitir que el reloj virtual alcance el momento en que
            # se arranco el replay (no solo el momento del ultimo evento).
            if self._virtual_now >= _ahora_utc():
                return True
        return False

    def _procesar_eventos_hasta(self, limite: datetime) -> int:
        procesados = 0
        # En cada tick procesamos todos los mensajes cuyo timestamp ya ha
        # llegado segun el reloj virtual.
        while True:
            item = self._buffer_pop_si(limite)
            if not item:
                break
            ts, topic, valor = item
            try:
                self._procesar_mensaje(topic, valor, ts)
            except Exception as exc:
                logger.warning("[%s] Error procesando mensaje (%s): %s",
                               self.sim_id, topic, exc)
            procesados += 1
        return procesados

    def _procesar_mensaje(self, topic: str, valor: dict, ts: datetime) -> None:
        if topic == KAFKA_TOPIC_CLIMA or _es_lectura_clima(valor):
            self._procesar_clima(valor)
            return
        if topic == KAFKA_TOPIC_EVENTOS:
            self._procesar_evento(valor)
            return
        # Topic desconocido: lo tratamos como evento si tiene tipo + coords
        self._procesar_evento(valor)

    def _procesar_clima(self, valor: dict) -> None:
        if not isinstance(valor, dict):
            return
        station = valor.get('station_id')
        if station:
            self._weather_by_station[station] = valor
        self._weather_cache.append(valor)
        self.weather_procesados += 1
        clima = interpretar_clima(valor)
        self._clima_actual = clima
        self._factor_clima = float(
            clima.get('condicion', {}).get('factor_velocidad', 1.0) or 1.0
        )

    def _procesar_evento(self, valor: dict) -> None:
        if not isinstance(valor, dict):
            return
        marcado = dict(valor)
        marcado['origen'] = f"replay:{self.sim_id}"
        try:
            self._fleet.manejar_evento(marcado)
        except Exception as exc:
            logger.warning("[%s] manejar_evento fallo: %s", self.sim_id, exc)
            return
        self._events_cache.append(marcado)
        self.eventos_procesados += 1


class GestorSimulaciones:
    """Coordina la simulacion live + el conjunto de replays activas."""

    def __init__(self, fleet: FleetManager, bus):
        self._fleet = fleet
        self._bus = bus
        self._lock = threading.Lock()
        self._replays: Dict[str, SimulacionReplay] = {}
        self._tiempo_real = SimulacionTiempoReal(fleet, bus)
        self._fecha_minima = _parsear_iso(REPLAY_FECHA_MIN) or datetime(
            2026, 4, 1, tzinfo=timezone.utc
        )

    # ------------------------------------------------------------------

    def fecha_minima(self) -> datetime:
        return self._fecha_minima

    def listar(self) -> List[dict]:
        with self._lock:
            replays = [s.estado_dict() for s in self._replays.values()]
        return [self._tiempo_real.estado_dict()] + replays

    def estado(self, sim_id: str) -> Optional[dict]:
        if sim_id == 'live':
            return self._tiempo_real.estado_dict()
        with self._lock:
            sim = self._replays.get(sim_id)
        return sim.estado_dict() if sim else None

    def snapshot(self, sim_id: str, decisiones_limit: int = 50,
                 eventos_limit: int = 50) -> Optional[dict]:
        if sim_id == 'live':
            return self._tiempo_real.snapshot(decisiones_limit=decisiones_limit,
                                              eventos_limit=eventos_limit)
        with self._lock:
            sim = self._replays.get(sim_id)
        if not sim:
            return None
        return sim.snapshot(decisiones_limit=decisiones_limit,
                            eventos_limit=eventos_limit)

    # ------------------------------------------------------------------

    def iniciar_replay(self, started_at_iso: str,
                       end_at_iso: Optional[str] = None,
                       speed: float = 5.0,
                       topics: Optional[List[str]] = None) -> dict:
        started = _parsear_iso(started_at_iso)
        if not started:
            raise ValueError("started_at no es ISO 8601 valido")
        if started < self._fecha_minima:
            raise ValueError(
                f"started_at no puede ser anterior a {self._fecha_minima.isoformat()} "
                "(retencion del topic Kafka)"
            )

        end = _parsear_iso(end_at_iso) if end_at_iso else None
        if end and end <= started:
            raise ValueError("end_at debe ser posterior a started_at")
        if end and end > _ahora_utc() + timedelta(minutes=1):
            # Limitar al presente: replay no puede ir al futuro real.
            end = None

        topics_norm = topics or [KAFKA_TOPIC_CLIMA, KAFKA_TOPIC_EVENTOS]
        sim_id = f"sim-{uuid.uuid4().hex[:8]}"

        sim = SimulacionReplay(sim_id, started, end, speed, topics_norm)
        with self._lock:
            self._replays[sim_id] = sim
        sim.iniciar()
        logger.info(
            "[GestorSim] Replay %s creado start=%s end=%s speed=%.2f topics=%s",
            sim_id, started.isoformat(),
            end.isoformat() if end else "(presente)", sim.speed, topics_norm,
        )
        return sim.estado_dict()

    def alternar_pausa(self, sim_id: str) -> Optional[dict]:
        if sim_id == 'live':
            return self._tiempo_real.estado_dict()
        with self._lock:
            sim = self._replays.get(sim_id)
        if not sim:
            return None
        sim.alternar_pausa()
        return sim.estado_dict()

    def set_velocidad(self, sim_id: str, speed: float) -> Optional[dict]:
        if sim_id == 'live':
            return self._tiempo_real.estado_dict()
        with self._lock:
            sim = self._replays.get(sim_id)
        if not sim:
            return None
        sim.set_velocidad(speed)
        return sim.estado_dict()

    def detener(self, sim_id: str) -> Optional[dict]:
        if sim_id == 'live':
            return None
        with self._lock:
            sim = self._replays.get(sim_id)
        if not sim:
            return None
        sim.detener()
        return sim.estado_dict()

    def eliminar(self, sim_id: str) -> bool:
        if sim_id == 'live':
            return False
        with self._lock:
            sim = self._replays.pop(sim_id, None)
        if not sim:
            return False
        sim.detener()
        return True

    def purgar_terminadas(self, antes_de_segundos: int = 3600) -> int:
        """Elimina simulaciones replay finalizadas hace mas de N segundos."""
        umbral = _ahora_utc() - timedelta(seconds=max(0, antes_de_segundos))
        eliminadas = 0
        with self._lock:
            for sid in list(self._replays.keys()):
                sim = self._replays[sid]
                if sim.estado in ('finished', 'error') and sim._terminado and sim._terminado < umbral:
                    self._replays.pop(sid, None)
                    eliminadas += 1
        return eliminadas
