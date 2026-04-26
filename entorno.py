import logging
from datetime import datetime
from typing import Optional

from config import CACHE_ENTORNO, TEMP_AMBIENTE

logger = logging.getLogger(__name__)

_bus = None
_ultimo_contexto = None
_ultimo_ts = 0.0

def configurar_bus(kafka_bus) -> None:
    global _bus
    _bus = kafka_bus


def temperatura_ambiente_actual() -> float:
    """Devuelve la temperatura ambiente real (Kafka) o el valor por defecto.

    Utilizada por la simulacion del motor de cada vehiculo para que el
    arranque en frio, la disipacion y el punto de operacion dependan de
    la temperatura real de la isla (en lugar de un valor fijo).
    """
    try:
        if _bus is None:
            return float(TEMP_AMBIENTE)
        lectura = _bus.ultimo_clima()
        if not isinstance(lectura, dict):
            return float(TEMP_AMBIENTE)
        temp = lectura.get('temperature_c')
        if temp is None:
            temp = lectura.get('temperature')
        if temp is None:
            temp = lectura.get('temp')
        if temp is None:
            return float(TEMP_AMBIENTE)
        valor = float(temp)
        if valor < -20 or valor > 60:
            return float(TEMP_AMBIENTE)
        return valor
    except Exception:
        return float(TEMP_AMBIENTE)

def interpretar_clima(lectura: dict) -> dict:
    """Interpreta una lectura de clima cruda y devuelve un contexto enriquecido.

    Es publica porque la usan tanto el contexto en tiempo real como las
    simulaciones replay (que reconstruyen su propio factor climatico a
    partir del histórico de Kafka).
    """
    if not isinstance(lectura, dict):
        lectura = {}
    temp = lectura.get("temperature_c")
    viento = lectura.get("wind_speed_kmh", 0) or 0
    lluvia = lectura.get("precipitation_mm", 0) or 0
    visibilidad = lectura.get("visibility_km", 100) or 100
    uv = lectura.get("uv_index", 0) or 0

    factor = 1.0
    condiciones = "buenas"
    descripcion = "estable"

    if lluvia >= 20 or visibilidad < 2:
        factor = 0.5
        condiciones = "muy peligrosas"
        descripcion = "lluvia intensa y baja visibilidad"
    elif lluvia >= 8 or visibilidad < 5:
        factor = 0.7
        condiciones = "peligrosas"
        descripcion = "precipitacion moderada"
    elif lluvia > 0:
        factor = 0.85
        condiciones = "reducidas"
        descripcion = "llovizna"

    if viento >= 70:
        factor = min(factor, 0.6)
        descripcion = "viento fuerte"
        condiciones = "peligrosas"
    elif viento >= 45:
        factor = min(factor, 0.75)
        descripcion = "viento moderado"
        condiciones = "reducidas"

    if uv >= 10 and descripcion == "estable":
        descripcion = "uv extremo"

    return {
        "temperatura": temp,
        "viento_kmh": viento,
        "humedad_pct": lectura.get("humidity_pct"),
        "presion_hpa": lectura.get("pressure_hpa"),
        "precipitacion_mm": lluvia,
        "visibilidad_km": visibilidad,
        "uv_index": uv,
        "condicion": {
            "descripcion": descripcion,
            "condiciones_conduccion": condiciones,
            "factor_velocidad": factor
        },
        "ultima_actualizacion": lectura.get("timestamp") or datetime.now().isoformat()
    }

def invalidar_cache_entorno() -> None:
    """Fuerza que la proxima llamada a obtener_contexto_entorno_completo recalcule.

    Llamado por el on_weather hook de Kafka cuando llega una lectura nueva,
    de modo que el factor de velocidad se actualiza sin esperar los 5 min del
    CACHE_ENTORNO habitual.
    """
    global _ultimo_ts
    _ultimo_ts = 0.0


def obtener_contexto_entorno_completo() -> Optional[dict]:
    global _ultimo_contexto, _ultimo_ts

    ahora = datetime.now().timestamp()
    if _ultimo_contexto and (ahora - _ultimo_ts) < CACHE_ENTORNO:
        return _ultimo_contexto

    if not _bus:
        return None

    lectura = _bus.ultimo_clima()
    if not lectura:
        return None

    clima = interpretar_clima(lectura)
    eventos = _bus.eventos_recientes(50)
    alertas = []

    if clima.get("condicion", {}).get("factor_velocidad", 1.0) < 0.8:
        alertas.append(f"Clima adverso: {clima.get('condicion', {}).get('descripcion')}")
    if eventos:
        alertas.append(f"Eventos activos en la isla: {len(eventos)}")

    _ultimo_contexto = {
        "clima": clima,
        "eventos": eventos,
        "alertas_entorno": alertas,
        "timestamp": datetime.now().isoformat()
    }
    _ultimo_ts = ahora
    return _ultimo_contexto
