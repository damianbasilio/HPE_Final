# Dron de reconocimiento aereo. Caso especial:
#   - No tiene frenos, neumaticos, aceite ni motor termico: lo apagamos.
#   - "Combustible" = bateria (%). Recarga es 'cambio de bateria'.
#   - Vuelo en linea recta (no usa OSRM): rutas se generan con generar_ruta_vuelo.
#   - Tiene altitud, modo (scout | seguimiento | mapeo | termico), y telemetria
#     de senal de control (link_pct).
#   - Coste fijo unico por minuto (no hay diferencia combustion/electrico).

import logging
from vehiculo_base import VehiculoBase

logger = logging.getLogger(__name__)

MODOS_DRON = ('scout', 'seguimiento', 'mapeo', 'termico')


class VehiculoDron(VehiculoBase):
    TIPO = 'dron'
    ESTADO_BASE = 'aterrizado'
    VELOCIDAD_CRUCERO = 0

    BATERIA_CAPACIDAD_MIN = 25     # Autonomia maxima de vuelo en minutos

    def __init__(self, id_vehiculo, propulsion='unico', metadatos=None):
        # Forzamos propulsion 'unico' aunque llamen con otra cosa.
        super().__init__(id_vehiculo, propulsion='unico', metadatos=metadatos)

        self.modo = 'scout'
        self.altitud_m = 0.0
        self.link_pct = 100.0          # Calidad de la senal con la base
        self.imagenes_capturadas = 0
        self.tiempo_vuelo_s = 0.0

    def _init_telemetria(self):
        # Sin frenos, sin neumaticos, sin aceite. Solo bateria + temperatura ESC.
        super()._init_telemetria()
        self.combustible = 100.0           # bateria al 100% al spawnear
        self.nivel_aceite = 100.0
        self.desgaste_frenos = 0.0
        self.desgaste_neumaticos = 0.0
        self.temperatura_motor = 30.0      # temp del controlador electronico

    def _iniciar_ruta_patrulla(self):
        # Aterrizado en base. No vuela hasta recibir mision.
        self.escenario_activo = self.ESTADO_BASE
        self.en_movimiento = False
        self.velocidad_objetivo = 0

    def aplicar_modificadores_especificos(self, tipo_escenario, modificadores, intensidad):
        modificadores = modificadores or {}

        modo = str(modificadores.get('modo_dron', '')).lower()
        self.modo = modo if modo in MODOS_DRON else 'scout'

        altitud = modificadores.get('altitud_m')
        if isinstance(altitud, (int, float)):
            self.altitud_m = max(0.0, min(500.0, float(altitud)))
        else:
            self.altitud_m = 80.0 + 100.0 * float(intensidad or 0)

    def actualizar_logica_especializada(self, delta_time):
        # Mientras vuela (en escena o en camino), descarga bateria mas rapido.
        if self.en_camino or self.en_escena:
            self.tiempo_vuelo_s += delta_time

            # Descarga: 100% en BATERIA_CAPACIDAD_MIN minutos => %/s
            descarga_pct_s = 100.0 / (self.BATERIA_CAPACIDAD_MIN * 60.0)
            self.combustible = max(0.0, self.combustible - descarga_pct_s * delta_time)

            # Imagenes capturadas si esta en escena con modo activo.
            if self.en_escena and self.modo in ('scout', 'mapeo', 'termico'):
                self.imagenes_capturadas += 1

            # La senal degrada con la distancia y altitud (simulado).
            self.link_pct = max(50.0, self.link_pct - 0.001 * delta_time * (self.altitud_m / 100.0))

    def _verificar_combustible(self):
        # Para el dron 'recargar combustible' significa volver a base y cambiar bateria.
        from config import UMBRAL_COMBUSTIBLE, TASA_REABASTECIMIENTO
        if self.combustible <= UMBRAL_COMBUSTIBLE and not self.reabasteciendo:
            self.reabasteciendo = True
            falta = max(0.0, 100.0 - self.combustible)
            self.tiempo_restante_recarga = int(max(5, falta / TASA_REABASTECIMIENTO))
            self.escenario_activo = 'Cambio de bateria'
            self.velocidad_objetivo = 0
            self.en_movimiento = False
            self.altitud_m = 0.0

    def finalizar_intervencion(self):
        logger.info(f"[Dron {self.id[:8]}] Mision finalizada, aterrizando")
        self.altitud_m = 0.0
        self.modo = 'scout'

    def obtener_estado_especializado(self):
        return {
            'modo': self.modo,
            'altitud_m': round(self.altitud_m, 1),
            'bateria_pct': round(self.combustible, 1),
            'link_pct': round(self.link_pct, 1),
            'autonomia_restante_min': round((self.combustible / 100.0) * self.BATERIA_CAPACIDAD_MIN, 1),
            'imagenes_capturadas': self.imagenes_capturadas,
            'tiempo_vuelo_s': int(self.tiempo_vuelo_s)
        }
