COSTOS_OPERATIVOS = {
    ("policia", "combustion"): {"dotacion": 2, "minuto": 1.20, "activacion": 15},
    ("policia", "electrico"): {"dotacion": 2, "minuto": 0.80, "activacion": 18},
    ("ambulancia", "combustion"): {"dotacion": 3, "minuto": 2.50, "activacion": 25},
    ("ambulancia", "electrico"): {"dotacion": 3, "minuto": 1.80, "activacion": 30},
    ("bomberos", "combustion"): {"dotacion": 5, "minuto": 4.00, "activacion": 50},
    ("bomberos", "electrico"): {"dotacion": 5, "minuto": 3.00, "activacion": 60},
    ("proteccion_civil", "combustion"): {"dotacion": 2, "minuto": 0.80, "activacion": 10},
    ("proteccion_civil", "electrico"): {"dotacion": 2, "minuto": 0.50, "activacion": 12},
    ("dron", "unico"): {"dotacion": 0, "minuto": 0.30, "activacion": 5}
}


def obtener_tarifa(tipo: str, energia: str) -> dict:
    clave = (tipo, energia)
    if clave in COSTOS_OPERATIVOS:
        return COSTOS_OPERATIVOS[clave]
    return {"dotacion": 0, "minuto": 0.0, "activacion": 0.0}
