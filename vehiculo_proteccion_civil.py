

from vehiculo_base import VehiculoBase

MISIONES_PC = ('logistica', 'evacuacion', 'balizamiento', 'apoyo', 'otros')

class VehiculoProteccionCivil(VehiculoBase):
    TIPO = 'proteccion_civil'
    ESTADO_BASE = 'en_base'
    VELOCIDAD_CRUCERO = 0

    KITS_INICIAL = 30   

    def __init__(self, id_vehiculo, propulsion='combustion', metadatos=None):
        super().__init__(id_vehiculo, propulsion=propulsion, metadatos=metadatos)
        self.kits_disponibles = self.KITS_INICIAL
        self.voluntarios_activos = self.dotacion
        self.mision_actual = None
        self.evacuados_total = 0

    def _iniciar_ruta_patrulla(self):
        self.escenario_activo = self.ESTADO_BASE
        self.en_movimiento = False
        self.velocidad_objetivo = 0

    def aplicar_modificadores_especificos(self, tipo_escenario, modificadores, intensidad):
        modificadores = modificadores or {}

        mision = str(modificadores.get('mision_pc', '')).lower()
        self.mision_actual = mision if mision in MISIONES_PC else 'apoyo'

        kits = modificadores.get('kits_a_repartir')
        if isinstance(kits, (int, float)) and kits > 0:
            kits = min(self.kits_disponibles, int(kits))
            self.kits_disponibles -= kits

        evacuados = modificadores.get('personas_evacuadas')
        if isinstance(evacuados, (int, float)) and evacuados > 0:
            self.evacuados_total += int(evacuados)

    def finalizar_intervencion(self):
        self.mision_actual = None

    def obtener_estado_especializado(self):
        return {
            'mision_actual': self.mision_actual,
            'kits_disponibles': self.kits_disponibles,
            'voluntarios_activos': self.voluntarios_activos,
            'evacuados_total': self.evacuados_total
        }
