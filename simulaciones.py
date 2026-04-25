

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

def _parsear_iso(valor) -> Optional[datetime]:
    if not valor:
        return None
    if isinstance(valor, datetime):
        return valor.astimezone(timezone.utc) if valor.tzinfo else valor.replace(tzinfo=timezone.utc)
    try:
        s = str(valor).replace('Z', '+00:00')
        dt = datetime.fromisoformat(s)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None

class SimulacionReplay:
    def __init__(self, sim_id: str, started_at: datetime, speed: float, topics: List[str], fleet, bus):
        self.sim_id = sim_id
        self.modo = 'replay'
        self.started_at = started_at
        self.speed = max(0.5, min(30.0, speed))
        self.topics = list(topics)
        self.estado = 'starting'
        self.eventos_procesados = 0
        self.cursor = started_at
        self.iniciado_en = datetime.utcnow()
        self.terminado_en = None

        self._fleet = fleet
        self._bus = bus
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._lock = threading.Lock()
        self._hilo = threading.Thread(target=self._run, daemon=True)

    def iniciar(self):
        self._hilo.start()

    def pausar(self):
        with self._lock:
            if self.estado == 'running':
                self._pause.set()
                self.estado = 'paused'
            elif self.estado == 'paused':
                self._pause.clear()
                self.estado = 'running'

    def detener(self):
        self._stop.set()
        self._pause.clear()

    def set_velocidad(self, speed: float):
        self.speed = max(0.5, min(30.0, float(speed)))

    def estado_dict(self) -> dict:
        return {
            "sim_id": self.sim_id,
            "modo": self.modo,
            "estado": self.estado,
            "velocidad": self.speed,
            "started_at": self.started_at.isoformat(),
            "cursor": self.cursor.isoformat() if self.cursor else None,
            "eventos_procesados": self.eventos_procesados,
            "topics": self.topics,
            "iniciado_en": self.iniciado_en.isoformat() + 'Z',
            "terminado_en": self.terminado_en.isoformat() + 'Z' if self.terminado_en else None,
        }

    def _run(self):
        try:
            from kafka_bus import _kafka_common_config
            from kafka import KafkaConsumer, TopicPartition
        except Exception as exc:
            logger.warning("[%s] Replay no disponible (kafka import): %s", self.sim_id, exc)
            self.estado = 'finished'
            return

        try:
            consumer = KafkaConsumer(
                group_id=f"replay-{self.sim_id}",
                auto_offset_reset='earliest',
                enable_auto_commit=False,
                value_deserializer=lambda v: json.loads(v.decode('utf-8')),
                consumer_timeout_ms=5000,
                **_kafka_common_config()
            )
            consumer.subscribe(self.topics)

            self.estado = 'running'
            logger.info("[%s] Replay arrancado desde %s a x%.1f", self.sim_id,
                        self.started_at.isoformat(), self.speed)

            while not self._stop.is_set():
                if self._pause.is_set():
                    time.sleep(0.5)
                    continue

                msgs = consumer.poll(timeout_ms=1000)
                if not msgs:
                    self.estado = 'finished'
                    self.terminado_en = datetime.utcnow()
                    break

                for _, registros in msgs.items():
                    for record in registros:
                        if self._stop.is_set():
                            break
                        valor = record.value
                        ts = _parsear_iso(
                            (valor or {}).get('started_at') or (valor or {}).get('timestamp')
                        )
                        if ts and ts < self.started_at:
                            continue

                        self.cursor = ts or self.cursor
                        self._procesar(valor)
                        self.eventos_procesados += 1

                        time.sleep(max(0.0, 1.0 / self.speed))
        except Exception as exc:
            logger.exception("[%s] Replay fallo: %s", self.sim_id, exc)
        finally:
            try:
                consumer.close()
            except Exception:
                pass
            if self.estado != 'finished':
                self.estado = 'finished'
            self.terminado_en = self.terminado_en or datetime.utcnow()
            logger.info("[%s] Replay finalizado tras %d eventos",
                        self.sim_id, self.eventos_procesados)

    def _procesar(self, valor: dict):

        if not isinstance(valor, dict):
            return
        if valor.get('type') and valor.get('latitude') is not None:
            valor = dict(valor)
            valor['origen'] = f'replay:{self.sim_id}'
            self._fleet.manejar_evento(valor)

class GestorSimulaciones:
    def __init__(self, fleet, bus):
        self._fleet = fleet
        self._bus = bus
        self._lock = threading.Lock()
        self._simulaciones: Dict[str, SimulacionReplay] = {}
        self._tiempo_real = self._construir_tiempo_real()

    def _construir_tiempo_real(self) -> dict:
        return {
            "sim_id": "live",
            "modo": "tiempo_real",
            "estado": "running",
            "velocidad": 1.0,
            "started_at": datetime.utcnow().isoformat() + 'Z',
            "cursor": None,
            "eventos_procesados": None,
            "topics": ["aruba.weather", "aruba.events"]
        }

    def listar(self) -> List[dict]:
        with self._lock:
            return [self._tiempo_real] + [s.estado_dict() for s in self._simulaciones.values()]

    def estado(self, sim_id: str) -> Optional[dict]:
        if sim_id == 'live':
            return self._tiempo_real
        with self._lock:
            sim = self._simulaciones.get(sim_id)
            return sim.estado_dict() if sim else None

    def iniciar_replay(self, started_at_iso: str, speed: float = 5.0,
                       topics: Optional[List[str]] = None) -> dict:
        started = _parsear_iso(started_at_iso)
        if not started:
            raise ValueError("started_at no es ISO 8601 valido")

        topics = topics or ['aruba.weather', 'aruba.events']
        sim_id = f"sim-{uuid.uuid4().hex[:8]}"
        sim = SimulacionReplay(sim_id, started, speed, topics, self._fleet, self._bus)
        with self._lock:
            self._simulaciones[sim_id] = sim
        sim.iniciar()
        return sim.estado_dict()

    def alternar_pausa(self, sim_id: str) -> Optional[dict]:
        if sim_id == 'live':
            return self._tiempo_real
        with self._lock:
            sim = self._simulaciones.get(sim_id)
        if not sim:
            return None
        sim.pausar()
        return sim.estado_dict()

    def set_velocidad(self, sim_id: str, speed: float) -> Optional[dict]:
        if sim_id == 'live':
            return self._tiempo_real
        with self._lock:
            sim = self._simulaciones.get(sim_id)
        if not sim:
            return None
        sim.set_velocidad(speed)
        return sim.estado_dict()

    def detener(self, sim_id: str) -> bool:
        if sim_id == 'live':
            return False
        with self._lock:
            sim = self._simulaciones.get(sim_id)
        if not sim:
            return False
        sim.detener()
        return True
