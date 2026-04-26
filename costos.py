"""Modulo de analisis de costes operativos.

Las tarifas listadas (COSTOS_OPERATIVOS_BASE) corresponden al pliego oficial:
no se pueden modificar para los vehiculos del catalogo. Se permite anadir
tarifas para tipos/energia adicionales mediante registrar_tarifa_personalizada,
pero nunca sobreescribir una entrada bloqueada.

El modulo tambien expone:
  * obtener_tarifa: tarifa efectiva (base + extension)
  * desglose_coste_minuto: reparto teorico del coste/minuto en personal,
    energia y desgaste, util para el panel de analisis.
  * prima_tiempo_respuesta: penalizacion por superar el SLA de respuesta.
"""

from __future__ import annotations

import threading
from typing import Dict, Optional, Tuple

ClaveTarifa = Tuple[str, str]


COSTOS_OPERATIVOS_BASE: Dict[ClaveTarifa, dict] = {
    ("policia", "combustion"): {
        "dotacion": 2, "coste_min": 1.20, "coste_activacion": 15.0, "velocidad_max": 180,
    },
    ("policia", "electrico"): {
        "dotacion": 2, "coste_min": 0.80, "coste_activacion": 18.0, "velocidad_max": 170,
    },
    ("ambulancia", "combustion"): {
        "dotacion": 3, "coste_min": 2.50, "coste_activacion": 25.0, "velocidad_max": 160,
    },
    ("ambulancia", "electrico"): {
        "dotacion": 3, "coste_min": 1.80, "coste_activacion": 30.0, "velocidad_max": 150,
    },
    ("bomberos", "combustion"): {
        "dotacion": 5, "coste_min": 4.00, "coste_activacion": 50.0, "velocidad_max": 130,
    },
    ("bomberos", "electrico"): {
        "dotacion": 5, "coste_min": 3.00, "coste_activacion": 60.0, "velocidad_max": 120,
    },
    ("proteccion_civil", "combustion"): {
        "dotacion": 2, "coste_min": 0.80, "coste_activacion": 10.0, "velocidad_max": 140,
    },
    ("proteccion_civil", "electrico"): {
        "dotacion": 2, "coste_min": 0.50, "coste_activacion": 12.0, "velocidad_max": 130,
    },
    ("dron", "unico"): {
        "dotacion": 0, "coste_min": 0.30, "coste_activacion": 5.0, "velocidad_max": 90,
    },
}


TARIFAS_BLOQUEADAS = frozenset(COSTOS_OPERATIVOS_BASE.keys())


_costos_personalizados: Dict[ClaveTarifa, dict] = {}
_lock_tarifas = threading.RLock()


_DISTRIBUCION_DEFECTO = {"personal": 0.50, "energia": 0.30, "desgaste": 0.20}

DISTRIBUCION_COSTE_MIN: Dict[ClaveTarifa, dict] = {
    ("policia", "combustion"):       {"personal": 0.55, "energia": 0.30, "desgaste": 0.15},
    ("policia", "electrico"):        {"personal": 0.65, "energia": 0.18, "desgaste": 0.17},
    ("ambulancia", "combustion"):    {"personal": 0.60, "energia": 0.25, "desgaste": 0.15},
    ("ambulancia", "electrico"):     {"personal": 0.70, "energia": 0.15, "desgaste": 0.15},
    ("bomberos", "combustion"):      {"personal": 0.55, "energia": 0.28, "desgaste": 0.17},
    ("bomberos", "electrico"):       {"personal": 0.65, "energia": 0.17, "desgaste": 0.18},
    ("proteccion_civil", "combustion"): {"personal": 0.55, "energia": 0.30, "desgaste": 0.15},
    ("proteccion_civil", "electrico"):  {"personal": 0.65, "energia": 0.18, "desgaste": 0.17},
    ("dron", "unico"):               {"personal": 0.20, "energia": 0.50, "desgaste": 0.30},
}


SLA_RESPUESTA_SEG = 600

PRIMA_RESPUESTA_EUR_MIN = 1.50

UMBRAL_SOBRECOSTE_FACTOR = 0.50

CLASE_BAJO_COSTE = 50.0
CLASE_MEDIO_COSTE = 200.0


def _normalizar_clave(tipo: str, energia: str) -> ClaveTarifa:
    return (str(tipo or "").strip().lower(), str(energia or "").strip().lower())


def obtener_tarifa(tipo: str, energia: str) -> dict:
    """Devuelve la tarifa para (tipo, energia), priorizando la lista oficial."""
    clave = _normalizar_clave(tipo, energia)
    base = COSTOS_OPERATIVOS_BASE.get(clave)
    if base is not None:
        return _construir_tarifa(clave, base, bloqueada=True)

    with _lock_tarifas:
        custom = _costos_personalizados.get(clave)
        if custom is not None:
            return _construir_tarifa(clave, custom, bloqueada=False)

    return {}


def _construir_tarifa(clave: ClaveTarifa, base: dict, bloqueada: bool) -> dict:
    distribucion = DISTRIBUCION_COSTE_MIN.get(clave, _DISTRIBUCION_DEFECTO)
    return {
        **base,
        "tipo": clave[0],
        "energia": clave[1],
        "minuto": base.get("coste_min"),
        "activacion": base.get("coste_activacion"),
        "bloqueada": bloqueada,
        "distribucion": dict(distribucion),
    }


def tipos_validos() -> tuple:
    todas = set(COSTOS_OPERATIVOS_BASE.keys()) | set(_costos_personalizados.keys())
    return tuple(sorted({clave[0] for clave in todas}))


def tarifas_completas(incluir_personalizadas: bool = True) -> list:
    """Devuelve la lista de tarifas en formato JSON-friendly para el panel."""
    salida = []
    for clave, base in COSTOS_OPERATIVOS_BASE.items():
        salida.append(_construir_tarifa(clave, base, bloqueada=True))

    if incluir_personalizadas:
        with _lock_tarifas:
            for clave, base in _costos_personalizados.items():
                salida.append(_construir_tarifa(clave, base, bloqueada=False))

    salida.sort(key=lambda t: (t["tipo"], t["energia"]))
    return salida


def registrar_tarifa_personalizada(
    tipo: str,
    energia: str,
    coste_min: float,
    coste_activacion: float,
    dotacion: int = 1,
    velocidad_max: Optional[int] = None,
    distribucion: Optional[dict] = None,
) -> dict:
    """Registra una tarifa nueva. Lanza ValueError si la combinacion esta bloqueada
    o los valores no son validos.
    """
    clave = _normalizar_clave(tipo, energia)

    if not clave[0] or not clave[1]:
        raise ValueError("Tipo y energia son obligatorios")

    if clave in TARIFAS_BLOQUEADAS:
        raise ValueError(
            "La tarifa para %s/%s esta bloqueada por el catalogo y no se puede modificar"
            % clave
        )

    try:
        cm = float(coste_min)
        ca = float(coste_activacion)
        dot = int(dotacion)
        vmax = int(velocidad_max) if velocidad_max is not None else 120
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Valores numericos invalidos: {exc}") from exc

    if cm < 0 or ca < 0 or dot < 0 or vmax <= 0:
        raise ValueError("Los costes y dotacion deben ser >= 0 y la velocidad > 0")

    nueva = {
        "dotacion": dot,
        "coste_min": cm,
        "coste_activacion": ca,
        "velocidad_max": vmax,
    }

    with _lock_tarifas:
        _costos_personalizados[clave] = nueva
        if distribucion and isinstance(distribucion, dict):
            DISTRIBUCION_COSTE_MIN[clave] = _normalizar_distribucion(distribucion)

    return _construir_tarifa(clave, nueva, bloqueada=False)


def eliminar_tarifa_personalizada(tipo: str, energia: str) -> bool:
    """Elimina una tarifa personalizada (no afecta a tarifas bloqueadas)."""
    clave = _normalizar_clave(tipo, energia)
    if clave in TARIFAS_BLOQUEADAS:
        return False
    with _lock_tarifas:
        return _costos_personalizados.pop(clave, None) is not None


def _normalizar_distribucion(d: dict) -> dict:
    out = {
        "personal": float(d.get("personal", 0.0) or 0.0),
        "energia": float(d.get("energia", 0.0) or 0.0),
        "desgaste": float(d.get("desgaste", 0.0) or 0.0),
    }
    suma = sum(out.values())
    if suma <= 0:
        return dict(_DISTRIBUCION_DEFECTO)
    return {k: v / suma for k, v in out.items()}


def desglose_coste_minuto(tipo: str, energia: str, minutos_facturados: float) -> dict:
    """Reparte (tipo, energia) coste_min * minutos_facturados en personal/energia/desgaste.
    Las cantidades estan en EUR y siempre suman al baseline.
    """
    tarifa = obtener_tarifa(tipo, energia)
    if not tarifa:
        return {"personal": 0.0, "energia": 0.0, "desgaste": 0.0, "total": 0.0}

    minutos_facturados = max(0.0, float(minutos_facturados or 0.0))
    base = tarifa["coste_min"] * minutos_facturados
    distribucion = tarifa.get("distribucion") or _DISTRIBUCION_DEFECTO

    return {
        "personal": round(base * distribucion["personal"], 4),
        "energia": round(base * distribucion["energia"], 4),
        "desgaste": round(base * distribucion["desgaste"], 4),
        "total": round(base, 4),
    }


def prima_tiempo_respuesta(tiempo_respuesta_seg: Optional[float]) -> float:
    """Penalizacion en EUR cuando se supera el SLA de respuesta."""
    if tiempo_respuesta_seg is None:
        return 0.0
    extra_seg = max(0.0, float(tiempo_respuesta_seg) - SLA_RESPUESTA_SEG)
    if extra_seg <= 0:
        return 0.0
    return round((extra_seg / 60.0) * PRIMA_RESPUESTA_EUR_MIN, 2)


def clasificar_coste(coste_eur: float) -> str:
    """Etiqueta de magnitud para el panel: bajo / medio / alto."""
    valor = float(coste_eur or 0.0)
    if valor < CLASE_BAJO_COSTE:
        return "bajo"
    if valor < CLASE_MEDIO_COSTE:
        return "medio"
    return "alto"
