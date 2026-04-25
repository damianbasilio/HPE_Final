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
    return {
        "bootstrap_servers": KAFKA_BROKER,
        "security_protocol": KAFKA_SECURITY_PROTOCOL,
        "sasl_mechanism": KAFKA_SASL_MECHANISM,
        "sasl_plain_username": KAFKA_USERNAME,
        "sasl_plain_password": KAFKA_PASSWORD
    }


class KafkaBus:
    def __init__(self, max_cache: int = 2000):
        self._stop_event = threading.Event()
        self._weather_cache: Deque[dict] = deque(maxlen=max_cache)
        self._events_cache: Deque[dict] = deque(maxlen=max_cache)
        self._weather_by_station = {}

        self._producer = KafkaProducer(
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            **_kafka_common_config()
        )

        self._weather_consumer = self._crear_consumer(
            KAFKA_TOPIC_CLIMA,
            group_id=f"{TEAM_ID}-weather"
        )
        self._events_consumer = self._crear_consumer(
            KAFKA_TOPIC_EVENTOS,
            group_id=f"{TEAM_ID}-events"
        )

    def _crear_consumer(self, topic: str, group_id: str, auto_offset_reset: str = "earliest") -> KafkaConsumer:
        return KafkaConsumer(
            topic,
            group_id=group_id,
            auto_offset_reset=auto_offset_reset,
            enable_auto_commit=True,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            **_kafka_common_config()
        )

    def iniciar(self,
               on_weather: Optional[Callable[[dict], None]] = None,
               on_event: Optional[Callable[[dict], None]] = None) -> None:
        threading.Thread(
            target=self._bucle_consumer,
            args=(self._weather_consumer, self._weather_cache, on_weather, "weather"),
            daemon=True
        ).start()

        threading.Thread(
            target=self._bucle_consumer,
            args=(self._events_consumer, self._events_cache, on_event, "events"),
            daemon=True
        ).start()

    def detener(self) -> None:
        self._stop_event.set()
        try:
            self._weather_consumer.close()
        except Exception:
            pass
        try:
            self._events_consumer.close()
        except Exception:
            pass
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
