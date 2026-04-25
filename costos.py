

COSTOS_OPERATIVOS = {
    ("policia", "combustion"): {
        "dotacion": 2, "coste_min": 1.20, "coste_activacion": 15.0, "velocidad_max": 180
    },
    ("policia", "electrico"): {
        "dotacion": 2, "coste_min": 0.80, "coste_activacion": 18.0, "velocidad_max": 170
    },
    ("ambulancia", "combustion"): {
        "dotacion": 3, "coste_min": 2.50, "coste_activacion": 25.0, "velocidad_max": 160
    },
    ("ambulancia", "electrico"): {
        "dotacion": 3, "coste_min": 1.80, "coste_activacion": 30.0, "velocidad_max": 150
    },
    ("bomberos", "combustion"): {
        "dotacion": 5, "coste_min": 4.00, "coste_activacion": 50.0, "velocidad_max": 130
    },
    ("bomberos", "electrico"): {
        "dotacion": 5, "coste_min": 3.00, "coste_activacion": 60.0, "velocidad_max": 120
    },
    ("proteccion_civil", "combustion"): {
        "dotacion": 2, "coste_min": 0.80, "coste_activacion": 10.0, "velocidad_max": 140
    },
    ("proteccion_civil", "electrico"): {
        "dotacion": 2, "coste_min": 0.50, "coste_activacion": 12.0, "velocidad_max": 130
    },
    ("dron", "unico"): {
        "dotacion": 0, "coste_min": 0.30, "coste_activacion": 5.0, "velocidad_max": 90
    },
}

def obtener_tarifa(tipo: str, energia: str) -> dict:
    base = COSTOS_OPERATIVOS.get((tipo, energia))
    if not base:
        return {}

    return {
        **base,

        "minuto": base["coste_min"],
        "activacion": base["coste_activacion"],
    }

def tipos_validos() -> tuple:
    return tuple(sorted({clave[0] for clave in COSTOS_OPERATIVOS.keys()}))
