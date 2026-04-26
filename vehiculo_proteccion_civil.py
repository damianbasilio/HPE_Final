

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
        self.alertas_emitidas = 0
        self.centros_evacuacion_activos = 0
        self.indice_estabilidad = 1.0
        self._tiempo_reporte_s = 0.0
        self.reportes_pc = []

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

        centros = modificadores.get('centros_evacuacion')
        if isinstance(centros, (int, float)) and centros >= 0:
            self.centros_evacuacion_activos = int(centros)

    def actualizar_logica_especializada(self, delta_time):
        if not self.mision_actual:
            self.indice_estabilidad = min(1.0, self.indice_estabilidad + 0.001 * delta_time)
            return

        if self.en_escena:
            if self.mision_actual == 'evacuacion':
                tasa = max(1, int(self.voluntarios_activos * 0.6))
                self.evacuados_total += int(tasa * max(0.2, delta_time / 10.0))
                self.kits_disponibles = max(0, self.kits_disponibles - int(max(1, delta_time // 12)))
                self.centros_evacuacion_activos = max(1, self.centros_evacuacion_activos)
                self.indice_estabilidad = max(0.0, self.indice_estabilidad - 0.001 * delta_time)
            elif self.mision_actual == 'balizamiento':
                self.kits_disponibles = max(0, self.kits_disponibles - int(max(1, delta_time // 20)))
                self.indice_estabilidad = min(1.0, self.indice_estabilidad + 0.0008 * delta_time)
            elif self.mision_actual == 'logistica':
                self.kits_disponibles = max(0, self.kits_disponibles - int(max(1, delta_time // 18)))
                self.indice_estabilidad = min(1.0, self.indice_estabilidad + 0.0006 * delta_time)
            else:
                self.indice_estabilidad = min(1.0, self.indice_estabilidad + 0.0003 * delta_time)

        if self.kits_disponibles <= 5:
            self.alertas_emitidas += 1

        self._tiempo_reporte_s += delta_time
        if self._tiempo_reporte_s >= 25:
            self._tiempo_reporte_s = 0.0
            self.reportes_pc.append({
                'mision': self.mision_actual,
                'kits_disponibles': self.kits_disponibles,
                'evacuados_total': self.evacuados_total,
                'centros_evacuacion': self.centros_evacuacion_activos,
                'indice_estabilidad': round(self.indice_estabilidad, 2),
            })
            if len(self.reportes_pc) > 25:
                self.reportes_pc = self.reportes_pc[-25:]

    def finalizar_intervencion(self):
        self.mision_actual = None
        self.centros_evacuacion_activos = 0
        self._tiempo_reporte_s = 0.0

    def obtener_estado_especializado(self):
        ultimo_reporte = self.reportes_pc[-1] if self.reportes_pc else None
        return {
            'mision_actual': self.mision_actual,
            'kits_disponibles': self.kits_disponibles,
            'voluntarios_activos': self.voluntarios_activos,
            'evacuados_total': self.evacuados_total,
            'alertas_emitidas': self.alertas_emitidas,
            'centros_evacuacion_activos': self.centros_evacuacion_activos,
            'indice_estabilidad': round(self.indice_estabilidad, 2),
            'reportes_emitidos': len(self.reportes_pc),
            'ultimo_reporte_operativo': ultimo_reporte,
        }
