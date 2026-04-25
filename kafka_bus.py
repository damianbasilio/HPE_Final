import json
import logging
import threading
from collections import deque
from typing import Callable, Deque, Optional

from kafka import KafkaConsumer, KafkaProducer

from config import (
    KAFKA_BROKER,
    KAFKA_USERNAME,
    KAFKA_PASSWORD,
    KAFKA_SECURITY_PROTOCOL,
    KAFKA_SASL_MECHANISM,
    KAFKA_TOPIC_CLIMA,
    KAFKA_TOPIC_EVENTOS,
    KAFKA_TOPIC_TELEMETRIA,
    TEAM_ID
)

logger = logging.getLogger(__name__)

def _kafka_common_config() -> dict:
    cfg: dict = {
        "bootstrap_servers": KAFKA_BROKER,
        "security_protocol": KAFKA_SECURITY_PROTOCOL or "PLAINTEXT",
    }
    if KAFKA_USERNAME and KAFKA_PASSWORD:
        cfg["security_protocol"] = KAFKA_SECURITY_PROTOCOL or "SASL_PLAINTEXT"
        cfg["sasl_mechanism"] = KAFKA_SASL_MECHANISM or "PLAIN"
        cfg["sasl_plain_username"] = KAFKA_USERNAME
        cfg["sasl_plain_password"] = KAFKA_PASSWORD
    return cfg

class KafkaBus:
    def __init__(self, max_cache: int = 2000):
        self._stop_event = threading.Event()
        self._weather_cache: Deque[dict] = deque(maxlen=max_cache)
        self._events_cache: Deque[dict] = deque(maxlen=max_cache)
        self._weather_by_station = {}

        self._producer: Optional[KafkaProducer] = None
        self._weather_consumer: Optional[KafkaConsumer] = None
        self._events_consumer: Optional[KafkaConsumer] = None
        self.disponible = False

        try:
            self._producer = KafkaProducer(
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                request_timeout_ms=5000,
                api_version_auto_timeout_ms=5000,
                **_kafka_common_config(),
            )
        except Exception as exc:
            logger.warning(
                "Kafka producer no disponible (%s). El servidor arrancara sin telemetria saliente.",
                exc,
            )
            self._producer = None

        try:
            self._weather_consumer = self._crear_consumer(
                KAFKA_TOPIC_CLIMA, group_id=f"{TEAM_ID}-weather"
            )
        except Exception as exc:
            logger.warning("Kafka consumer clima no disponible (%s).", exc)
            self._weather_consumer = None

        try:
            self._events_consumer = self._crear_consumer(
                KAFKA_TOPIC_EVENTOS, group_id=f"{TEAM_ID}-events"
            )
        except Exception as exc:
            logger.warning("Kafka consumer eventos no disponible (%s).", exc)
            self._events_consumer = None

        self.disponible = bool(
            self._producer or self._weather_consumer or self._events_consumer
        )
        if not self.disponible:
            logger.warning(
                "KafkaBus deshabilitado: el servidor seguira corriendo en modo offline."
            )

    def _crear_consumer(self, topic: str, group_id: str, auto_offset_reset: str = "earliest") -> KafkaConsumer:
        return KafkaConsumer(
            topic,
            group_id=group_id,
            auto_offset_reset=auto_offset_reset,
            enable_auto_commit=True,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            consumer_timeout_ms=0,
            request_timeout_ms=8000,
            api_version_auto_timeout_ms=5000,
            **_kafka_common_config(),
        )

    def iniciar(self,
               on_weather: Optional[Callable[[dict], None]] = None,
               on_event: Optional[Callable[[dict], None]] = None) -> None:
        if self._weather_consumer is not None:
            threading.Thread(
                target=self._bucle_consumer,
                args=(self._weather_consumer, self._weather_cache, on_weather, "weather"),
                daemon=True,
            ).start()

        if self._events_consumer is not None:
            threading.Thread(
                target=self._bucle_consumer,
                args=(self._events_consumer, self._events_cache, on_event, "events"),
                daemon=True,
            ).start()

    def detener(self) -> None:
        self._stop_event.set()
        for closeable in (self._weather_consumer, self._events_consumer):
            if closeable is None:
                continue
            try:
                closeable.close()
            except Exception:
                pass
        if self._producer is not None:
            try:
                self._producer.flush(2)
                self._producer.close(2)
            except Exception:
                pass

    def _bucle_consumer(self, consumer: KafkaConsumer, cache: Deque[dict], hook, etiqueta: str) -> None:
        while not self._stop_event.is_set():
            try:
                for mensaje in consumer.poll(timeout_ms=1000).values():
                    for record in mensaje:
                        valor = record.value
                        cache.append(valor)
                        if etiqueta == "weather":
                            station_id = valor.get("station_id")
                            if station_id:
                                self._weather_by_station[station_id] = valor
                        if hook:
                            hook(valor)
            except Exception as exc:
                logger.warning("Kafka %s: %s", etiqueta, exc)

    def publicar_telemetria(self, payload: dict) -> None:
        if self._producer is None:
            return
        try:
            self._producer.send(KAFKA_TOPIC_TELEMETRIA, payload)
            self._producer.flush(1)
        except Exception as exc:
            logger.warning("Error publicando telemetria: %s", exc)

    def ultimo_clima(self) -> Optional[dict]:
        return self._weather_cache[-1] if self._weather_cache else None

    def lectura_estacion(self, station_id: str) -> Optional[dict]:
        return self._weather_by_station.get(station_id)

    def eventos_recientes(self, limite: int = 50) -> list:
        if not self._events_cache:
            return []
        return list(self._events_cache)[-limite:]
