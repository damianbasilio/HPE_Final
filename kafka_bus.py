import json
import logging
import threading
import time
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
    KAFKA_OFFSET_CLIMA,
    KAFKA_OFFSET_EVENTOS,
    TEAM_ID
)

logger = logging.getLogger(__name__)


def _deserializar_json_seguro(raw):
    if raw is None:
        return {}

    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")

    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}

    if isinstance(raw, dict):
        return raw

    return {"raw": raw}


def _kafka_common_config() -> dict:
    usuario = (KAFKA_USERNAME or "").strip()
    password = (KAFKA_PASSWORD or "").strip()
    requested_protocol = (KAFKA_SECURITY_PROTOCOL or "PLAINTEXT").upper()

    cfg: dict = {
        "bootstrap_servers": KAFKA_BROKER,
        "security_protocol": requested_protocol,
    }

    if usuario and password:
        cfg["security_protocol"] = requested_protocol if requested_protocol.startswith("SASL") else "SASL_PLAINTEXT"
        cfg["sasl_mechanism"] = KAFKA_SASL_MECHANISM or "PLAIN"
        cfg["sasl_plain_username"] = usuario
        cfg["sasl_plain_password"] = password
    elif requested_protocol.startswith("SASL"):
        # Si se pide SASL sin credenciales, degradamos a PLAINTEXT para evitar
        # que el consumidor quede inutilizable por configuracion incompleta.
        cfg["security_protocol"] = "PLAINTEXT"
        logger.warning(
            "Kafka configurado con %s pero sin usuario/contrasena; usando PLAINTEXT.",
            requested_protocol,
        )

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
        self._publish_since_flush = 0
        self._last_publish_flush = time.monotonic()
        self.disponible = False

        try:
            self._producer = KafkaProducer(
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                request_timeout_ms=20000,
                api_version_auto_timeout_ms=10000,
                **_kafka_common_config(),
            )
            logger.info("Kafka producer conectado a %s", KAFKA_BROKER)
        except Exception as exc:
            logger.error(
                "Kafka producer NO disponible (%s). El servidor arranca sin telemetria saliente.",
                exc,
            )
            self._producer = None

        try:
            self._weather_consumer = self._crear_consumer(
                KAFKA_TOPIC_CLIMA,
                group_id=f"{TEAM_ID}-weather",
                auto_offset_reset=KAFKA_OFFSET_CLIMA,
            )
            logger.info(
                "Kafka consumer suscrito a %s (group=%s offset=%s)",
                KAFKA_TOPIC_CLIMA,
                f"{TEAM_ID}-weather",
                KAFKA_OFFSET_CLIMA,
            )
        except Exception as exc:
            logger.error("Kafka consumer clima NO disponible (%s).", exc, exc_info=True)
            self._weather_consumer = None

        try:
            self._events_consumer = self._crear_consumer(
                KAFKA_TOPIC_EVENTOS,
                group_id=f"{TEAM_ID}-events",
                auto_offset_reset=KAFKA_OFFSET_EVENTOS,
            )
            logger.info(
                "Kafka consumer suscrito a %s (group=%s offset=%s)",
                KAFKA_TOPIC_EVENTOS,
                f"{TEAM_ID}-events",
                KAFKA_OFFSET_EVENTOS,
            )
        except Exception as exc:
            logger.error("Kafka consumer eventos NO disponible (%s).", exc, exc_info=True)
            self._events_consumer = None

        self.disponible = bool(
            self._producer or self._weather_consumer or self._events_consumer
        )
        if not self.disponible:
            logger.warning(
                "KafkaBus deshabilitado: el servidor seguira corriendo en modo offline."
            )

    def _crear_consumer(self, topic: str, group_id: str, auto_offset_reset: str = "earliest") -> KafkaConsumer:
        auto_offset = (auto_offset_reset or "earliest").lower()
        if auto_offset not in ("earliest", "latest"):
            auto_offset = "earliest"

        return KafkaConsumer(
            topic,
            group_id=group_id,
            auto_offset_reset=auto_offset,
            enable_auto_commit=True,
            value_deserializer=_deserializar_json_seguro,
            session_timeout_ms=15000,
            request_timeout_ms=40000,
            api_version_auto_timeout_ms=10000,
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
        cfg = getattr(consumer, "config", {}) or {}
        logger.info(
            "[Kafka] Bucle %s iniciado (auto_offset_reset=%s, group_id=%s)",
            etiqueta,
            cfg.get("auto_offset_reset"),
            cfg.get("group_id"),
        )
        while not self._stop_event.is_set():
            try:
                resultado = consumer.poll(timeout_ms=1000)
                if not resultado:
                    continue
                total = sum(len(v) for v in resultado.values())
                if etiqueta == "events" and total:
                    logger.info("[Kafka] %s: %d mensajes recibidos en este poll", etiqueta, total)
                for mensaje in resultado.values():
                    for record in mensaje:
                        valor = record.value
                        if not isinstance(valor, dict):
                            valor = {"raw": valor}
                        cache.append(valor)
                        if etiqueta == "weather":
                            station_id = valor.get("station_id")
                            if station_id:
                                self._weather_by_station[station_id] = valor
                        elif etiqueta == "events":
                            logger.debug(
                                "[Kafka] events offset=%s key=%s payload=%s",
                                record.offset, record.key, valor,
                            )
                        if hook:
                            try:
                                hook(valor)
                            except Exception as hook_exc:
                                logger.exception("[Kafka] hook %s fallo: %s", etiqueta, hook_exc)
            except Exception as exc:
                logger.warning("Kafka %s: %s", etiqueta, exc)

    def publicar_telemetria(self, payload: dict) -> None:
        if self._producer is None:
            return
        try:
            self._producer.send(KAFKA_TOPIC_TELEMETRIA, payload)
            self._publish_since_flush += 1
            ahora = time.monotonic()
            if self._publish_since_flush >= 25 or (ahora - self._last_publish_flush) >= 2.0:
                self._producer.flush(0.5)
                self._publish_since_flush = 0
                self._last_publish_flush = ahora
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
