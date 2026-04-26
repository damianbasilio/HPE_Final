"""Generador automatico de incidentes ficticios.

Mantiene la demo viva cuando Kafka no inyecta eventos durante un tiempo,
lanzando incidentes sinteticos en ubicaciones reales de Aruba (landmarks)
para que ambulancias, bomberos, proteccion civil y drones tengan algo
que hacer y no se queden parados en su base.

Diseno:
  * Frecuencia media (60-120 s) configurable.
  * Solo dispara si NO ha entrado un evento real en `silencio_kafka_s`.
  * Sortea tipo y severidad con pesos realistas (medicas son mas frecuentes
    que los incendios estructurales, etc.).
  * Distribuye geograficamente alrededor de los landmarks con jitter para
    que cada incidente caiga sobre carretera o muy cerca.
  * Etiqueta los incidentes con `origen='auto_demo'` para que sean
    identificables en la UI.

El generador se ejecuta en un thread daemon iniciado por `flota.py`.
"""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

from config import ARUBA_LANDMARKS, CENTRO_ARUBA, ARUBA_BOUNDS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Landmarks clasificados por categoria geografica
# ---------------------------------------------------------------------------
# Categoria: 'urbano' -> interior / ciudad, jitter bajo, bueno para cualquier
#            evento terrestre.
#            'costero' -> junto al mar/playa; solo eventos marinos y algunos
#            meteorologicos. Jitter muy pequeno para no caer al agua.
#            'rural'  -> campo o zona poco poblada; accidentes de carretera,
#            incendios de vegetacion, etc.
#            'industrial' -> aeropuerto, zonas industriales.

_LANDMARKS_EXTENDIDOS = [
    # (nombre, lat, lon, categoria, jitter_max_deg)
    ("Oranjestad",               12.5240, -70.0270, "urbano",     0.0025),
    ("Eagle Beach",              12.5538, -70.0518, "costero",    0.0012),
    ("Palm Beach",               12.5762, -70.0489, "costero",    0.0012),
    ("Noord",                    12.5870, -70.0411, "urbano",     0.0025),
    ("Hadicurari",               12.5818, -70.0469, "costero",    0.0010),
    ("California Lighthouse",    12.6164, -70.0488, "rural",      0.0018),
    ("Santa Cruz",               12.5363, -69.9628, "urbano",     0.0025),
    ("Paradera",                 12.5197, -69.9851, "urbano",     0.0025),
    ("Tanki Leendert",           12.5474, -70.0089, "urbano",     0.0025),
    ("Savaneta",                 12.4517, -69.9281, "costero",    0.0012),
    ("Pos Chiquito",             12.4839, -69.9519, "rural",      0.0020),
    ("San Nicolas",              12.4350, -69.9100, "urbano",     0.0025),
    ("Seroe Colorado",           12.4283, -69.8836, "costero",    0.0012),
    ("Sint Cruz",                12.5120, -69.9750, "urbano",     0.0025),
    ("Bushiribana",              12.5680, -69.9420, "rural",      0.0018),
    ("Aeropuerto Reina Beatrix", 12.5014, -70.0152, "industrial", 0.0020),
]

# Que categorias de landmark puede usar cada tipo de evento.
# Orden de preferencia: primera lista = preferido, si no hay se usa fallback urbano.
_AFINIDAD_TIPO: dict = {
    "medical_emergency": ["urbano", "rural", "industrial"],
    "accident":          ["urbano", "rural", "industrial"],
    "fire":              ["urbano", "rural", "industrial"],
    "lane_closure":      ["urbano", "rural", "industrial"],
    "storm":             ["costero", "rural", "urbano"],
    "flood":             ["costero", "rural", "urbano"],
    "power_outage":      ["urbano", "industrial"],
    "marine_rescue":     ["costero"],
    "hazmat_spill":      ["industrial", "rural", "urbano"],
    "public_event":      ["urbano"],  # concentraciones solo en ciudad
    "construction":      ["urbano", "rural"],
    "earthquake":        ["urbano", "rural", "costero"],
    "blackout":          ["urbano", "industrial"],
    "road_block":        ["urbano", "rural"],
}

# Construimos indices por categoria para acceso rapido
_POR_CATEGORIA: dict[str, list] = {}
for _lm in _LANDMARKS_EXTENDIDOS:
    _cat = _lm[3]
    _POR_CATEGORIA.setdefault(_cat, []).append(_lm)


# ---------------------------------------------------------------------------
# Catalogo de incidentes con titulos y descripciones contextualizados
# ---------------------------------------------------------------------------

CATALOGO_INCIDENTES = [
    # (peso, tipo_evento, titulo, descripcion, severidad_pesos)
    (28, "medical_emergency", "Aviso medico ciudadano",
     "Persona en via publica requiere asistencia sanitaria urgente",
     {"low": 1, "medium": 4, "high": 3, "critical": 1}),
    (18, "accident", "Colision de trafico",
     "Vehiculos implicados en calzada; posibles heridos",
     {"low": 1, "medium": 4, "high": 3, "critical": 1}),
    (10, "fire", "Conato de incendio",
     "Humo visible en estructura reportado por vecinos",
     {"low": 1, "medium": 3, "high": 4, "critical": 2}),
    (8, "lane_closure", "Obstruccion en calzada",
     "Carga caida o averia bloquea un carril de circulacion",
     {"low": 4, "medium": 3, "high": 1, "critical": 0}),
    (6, "storm", "Alerta meteorologica costera",
     "Rafagas y precipitacion intensa en la costa",
     {"low": 2, "medium": 4, "high": 2, "critical": 1}),
    (5, "flood", "Inundacion en calzada",
     "Acumulacion de agua de lluvia corta la via",
     {"low": 1, "medium": 4, "high": 3, "critical": 1}),
    (5, "power_outage", "Corte de suministro electrico",
     "Fallo de red afecta a varios bloques del sector",
     {"low": 3, "medium": 4, "high": 2, "critical": 0}),
    (4, "marine_rescue", "Rescate marino",
     "Banista / embarcacion ligera en apuros frente a la costa",
     {"low": 1, "medium": 3, "high": 4, "critical": 2}),
    (3, "hazmat_spill", "Derrame de sustancia peligrosa",
     "Liquido sospechoso en zona industrial requiere contencion",
     {"low": 1, "medium": 3, "high": 4, "critical": 2}),
    (3, "public_event", "Concentracion publica en calzada",
     "Aglomeracion no programada bloquea trafico urbano",
     {"low": 4, "medium": 3, "high": 1, "critical": 0}),
]


def _elegir_severidad(pesos: dict) -> str:
    items = list(pesos.items())
    total = sum(p for _, p in items)
    if total <= 0:
        return "medium"
    r = random.uniform(0, total)
    acum = 0.0
    for nivel, peso in items:
        acum += peso
        if r <= acum:
            return nivel
    return "medium"


def _elegir_categoria() -> tuple:
    total = sum(c[0] for c in CATALOGO_INCIDENTES)
    r = random.uniform(0, total)
    acum = 0.0
    for cat in CATALOGO_INCIDENTES:
        acum += cat[0]
        if r <= acum:
            return cat
    return CATALOGO_INCIDENTES[0]


def _coordenadas_para_tipo(tipo_evento: str) -> tuple:
    """Selecciona landmark apropiado para el tipo de evento y aplica jitter.

    Reglas:
    - Cada tipo tiene categorias de landmark preferidas (afinidad).
    - El jitter maximo por landmark esta calibrado para no caer al mar.
    - Se valida que el punto final este dentro del bounding box terrestre
      de Aruba; si no, se reintenta hasta 5 veces antes de usar el centro.
    """
    lat_min, lat_max, lon_min, lon_max = ARUBA_BOUNDS

    categorias = _AFINIDAD_TIPO.get(tipo_evento, ["urbano", "rural"])

    # Construir lista de candidatos en orden de preferencia
    candidatos: list = []
    for cat in categorias:
        candidatos.extend(_POR_CATEGORIA.get(cat, []))
    if not candidatos:
        # Fallback: todos los landmarks
        candidatos = _LANDMARKS_EXTENDIDOS

    for _intento in range(6):
        _nombre, lat_l, lon_l, _cat, jitter_max = random.choice(candidatos)
        jitter_lat = random.uniform(-jitter_max, jitter_max)
        jitter_lon = random.uniform(-jitter_max, jitter_max)
        lat = lat_l + jitter_lat
        lon = lon_l + jitter_lon
        # Verificar que el punto cae dentro del bounding box de Aruba
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return float(lat), float(lon)

    # Fallback seguro: centro de Aruba con jitter minimo
    lat_c, lon_c = CENTRO_ARUBA
    return (
        float(lat_c + random.uniform(-0.001, 0.001)),
        float(lon_c + random.uniform(-0.001, 0.001)),
    )


def construir_evento_demo() -> dict:
    """Genera un evento sintetico geograficamente coherente."""
    _, tipo, titulo, descripcion, pesos_sev = _elegir_categoria()
    severidad = _elegir_severidad(pesos_sev)
    lat, lon = _coordenadas_para_tipo(tipo)
    return {
        "id": f"DEMO-{uuid.uuid4().hex[:8]}",
        "type": tipo,
        "severity": severidad,
        "title": titulo,
        "description": descripcion,
        "latitude": lat,
        "longitude": lon,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "origen": "auto_demo",
    }


class GeneradorIncidentesDemo:
    """Hilo daemon que inyecta incidentes sinteticos cuando Kafka calla."""

    def __init__(
        self,
        fleet,
        bus,
        intervalo_min_s: float = 60.0,
        intervalo_max_s: float = 120.0,
        silencio_kafka_s: float = 90.0,
        max_activos: int = 6,
    ) -> None:
        self.fleet = fleet
        self.bus = bus
        self.intervalo_min_s = float(intervalo_min_s)
        self.intervalo_max_s = float(intervalo_max_s)
        self.silencio_kafka_s = float(silencio_kafka_s)
        self.max_activos = int(max_activos)

        self._stop = threading.Event()
        self._hilo: Optional[threading.Thread] = None
        self._ultimo_evento_kafka_ts: float = 0.0
        self._ultimo_disparo_demo: float = 0.0
        self._eventos_inyectados = 0

    # ------------------------------------------------------------------
    # API publica
    # ------------------------------------------------------------------

    def iniciar(self) -> None:
        if self._hilo and self._hilo.is_alive():
            return
        self._hilo = threading.Thread(target=self._bucle, daemon=True)
        self._hilo.start()
        logger.info(
            "[Auto-demo] Generador de incidentes iniciado (cada %.0f-%.0f s, silencio %.0f s)",
            self.intervalo_min_s, self.intervalo_max_s, self.silencio_kafka_s,
        )

    def detener(self) -> None:
        self._stop.set()

    def notificar_evento_real(self) -> None:
        """Llamado desde el callback de Kafka cuando entra un evento real."""
        self._ultimo_evento_kafka_ts = time.monotonic()

    def estado(self) -> dict:
        return {
            "ejecutando": bool(self._hilo and self._hilo.is_alive()),
            "intervalo_s": [self.intervalo_min_s, self.intervalo_max_s],
            "silencio_requerido_s": self.silencio_kafka_s,
            "eventos_inyectados": self._eventos_inyectados,
            "ultimo_evento_real_hace_s": (
                None if not self._ultimo_evento_kafka_ts
                else round(time.monotonic() - self._ultimo_evento_kafka_ts, 1)
            ),
        }

    # ------------------------------------------------------------------
    # Logica interna
    # ------------------------------------------------------------------

    def _bucle(self) -> None:
        time.sleep(15.0)
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:
                logger.warning("[Auto-demo] Tick fallo: %s", exc)
            espera = random.uniform(8.0, 14.0)
            self._stop.wait(espera)

    def _tick(self) -> None:
        ahora = time.monotonic()

        if self._ultimo_evento_kafka_ts and (ahora - self._ultimo_evento_kafka_ts) < self.silencio_kafka_s:
            return

        if self._ultimo_disparo_demo and (ahora - self._ultimo_disparo_demo) < self.intervalo_min_s:
            return

        try:
            activos = sum(
                1 for inc in self.fleet.listado_incidentes()
                if (inc or {}).get('status') in ('assigned', 'en_route', 'on_scene', 'queued')
            )
        except Exception:
            activos = 0
        if activos >= self.max_activos:
            return

        if self._ultimo_disparo_demo:
            ventana = max(self.intervalo_min_s, self.intervalo_max_s - self.intervalo_min_s)
            t_desde = ahora - self._ultimo_disparo_demo
            prob = min(1.0, max(0.0, (t_desde - self.intervalo_min_s) / ventana))
            if random.random() > prob:
                return

        evento = construir_evento_demo()
        try:
            inc_id = self.fleet.manejar_evento(evento)
            if inc_id:
                self._eventos_inyectados += 1
                self._ultimo_disparo_demo = ahora
                logger.info(
                    "[Auto-demo] Inyectado %s (%s/%s) lat=%.4f lon=%.4f -> %s",
                    evento['id'], evento['type'], evento['severity'],
                    evento['latitude'], evento['longitude'], inc_id,
                )
        except Exception as exc:
            logger.warning("[Auto-demo] No se pudo inyectar evento: %s", exc)


def envolver_callback_kafka(callback_real: Callable, generador: GeneradorIncidentesDemo) -> Callable:
    """Devuelve un callback que ademas notifica al generador para que sepa
    que han llegado eventos reales y se mantenga en silencio mientras tanto."""
    def _wrapper(evento):
        try:
            generador.notificar_evento_real()
        except Exception:
            pass
        return callback_real(evento)
    return _wrapper
