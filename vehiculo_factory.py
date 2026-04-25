# Fabrica unica para instanciar cualquier unidad del Gemelo Digital.
#
# Punto unico de creacion. Si manana migrais a microservicios y cada tipo de
# vehiculo vive en un servicio distinto, basta con cambiar este archivo (o
# convertirlo en un cliente HTTP/Kafka que delegue al servicio correcto).

import logging

from config import TIPOS_VEHICULO_VALIDOS, obtener_tarifa
from vehiculo_policia import VehiculoPolicial
from vehiculo_ambulancia import Ambulancia
from vehiculo_bomberos import VehiculoBomberos
from vehiculo_proteccion_civil import VehiculoProteccionCivil
from vehiculo_dron import VehiculoDron

logger = logging.getLogger(__name__)


# Registro tipo -> clase
_REGISTRO = {
    'policia': VehiculoPolicial,
    'ambulancia': Ambulancia,
    'bomberos': VehiculoBomberos,
    'proteccion_civil': VehiculoProteccionCivil,
    'dron': VehiculoDron,
}


def tipos_disponibles():
    return list(_REGISTRO.keys())


def crear_vehiculo(tipo, id_vehiculo, propulsion='combustion', metadatos=None):
    tipo = (tipo or '').lower()

    if tipo not in _REGISTRO:
        raise ValueError(
            f"Tipo de vehiculo desconocido: {tipo!r}. "
            f"Validos: {list(_REGISTRO.keys())}"
        )

    if tipo == 'dron':
        propulsion = 'unico'

    if not obtener_tarifa(tipo, propulsion):
        raise ValueError(
            f"Combinacion no disponible: tipo={tipo}, propulsion={propulsion}"
        )

    clase = _REGISTRO[tipo]
    logger.info(f"[Factory] Creando {tipo}/{propulsion} id={id_vehiculo[:8]}")
    return clase(id_vehiculo, propulsion=propulsion, metadatos=metadatos or {})
