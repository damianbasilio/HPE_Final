

import logging
from vehiculo_base import VehiculoBase

logger = logging.getLogger(__name__)

ROLES_BOMBEROS = ('extincion', 'rescate', 'escala', 'mixto')
TIPOS_INCENDIO = ('estructural', 'forestal', 'vehicular', 'derrame', 'otro')

class VehiculoBomberos(VehiculoBase):
    TIPO = 'bomberos'
    ESTADO_BASE = 'en_parque'
    VELOCIDAD_CRUCERO = 0   

    AGUA_CAPACIDAD_L = 6000
    ESPUMA_CAPACIDAD_L = 400

    def __init__(self, id_vehiculo, propulsion='combustion', metadatos=None):
        super().__init__(id_vehiculo, propulsion=propulsion, metadatos=metadatos)

        rol = (metadatos or {}).get('rol_bomberos', 'mixto')
        self.rol = rol if rol in ROLES_BOMBEROS else 'mixto'

        self.agua_litros = self.AGUA_CAPACIDAD_L
        self.espuma_litros = self.ESPUMA_CAPACIDAD_L

        self.caudal_agua_lpm = 0.0       
        self.caudal_espuma_lpm = 0.0
        self.escala_desplegada = False
        self.tipo_incendio = None
        self.efectivos_operativos = self.dotacion
        self.indice_control_fuego = 0.0
        self.riesgo_reignicion = 0.0
        self._tiempo_reporte_s = 0.0
        self.reportes_operativos = []

    def _iniciar_ruta_patrulla(self):
        self.escenario_activo = self.ESTADO_BASE
        self.en_movimiento = False
        self.velocidad_objetivo = 0

    def aplicar_modificadores_especificos(self, tipo_escenario, modificadores, intensidad):
        modificadores = modificadores or {}

        tipo_inc = str(modificadores.get('tipo_incendio', '')).lower()
        if tipo_inc in TIPOS_INCENDIO:
            self.tipo_incendio = tipo_inc
        else:
            self.tipo_incendio = 'otro'

        base_agua = {'estructural': 800, 'forestal': 1200, 'vehicular': 400, 'derrame': 200, 'otro': 600}
        self.caudal_agua_lpm = base_agua.get(self.tipo_incendio, 600) * (0.4 + float(intensidad or 0))

        if self.tipo_incendio in ('derrame', 'vehicular') and intensidad >= 0.5:
            self.caudal_espuma_lpm = 80 * float(intensidad or 0)
        else:
            self.caudal_espuma_lpm = 0.0

        escala_ia = modificadores.get('requiere_escala')
        if escala_ia is not None:
            self.escala_desplegada = bool(escala_ia)
        else:
            self.escala_desplegada = self.tipo_incendio == 'estructural' and intensidad >= 0.6

        efectivos = modificadores.get('efectivos_desplegados')
        if isinstance(efectivos, (int, float)):
            self.efectivos_operativos = max(1, min(self.dotacion, int(efectivos)))

    def actualizar_logica_especializada(self, delta_time):

        if not self.en_escena:
            self.riesgo_reignicion = max(0.0, self.riesgo_reignicion - 0.002 * delta_time)
            return

        litros_agua = (self.caudal_agua_lpm / 60.0) * delta_time
        litros_espuma = (self.caudal_espuma_lpm / 60.0) * delta_time

        self.agua_litros = max(0.0, self.agua_litros - litros_agua)
        self.espuma_litros = max(0.0, self.espuma_litros - litros_espuma)

        if self.agua_litros <= 0:
            self.caudal_agua_lpm = 0.0
        if self.espuma_litros <= 0:
            self.caudal_espuma_lpm = 0.0

        recurso_agua = self.agua_litros / float(self.AGUA_CAPACIDAD_L)
        recurso_espuma = self.espuma_litros / float(self.ESPUMA_CAPACIDAD_L)
        eficacia = 0.45 * recurso_agua + 0.25 * recurso_espuma + 0.30 * (self.efectivos_operativos / max(1, self.dotacion))

        if self.tipo_incendio in ('estructural', 'forestal'):
            factor_control = 0.020
            base_riesgo = 0.55
        elif self.tipo_incendio == 'derrame':
            factor_control = 0.028
            base_riesgo = 0.65
        elif self.tipo_incendio == 'vehicular':
            factor_control = 0.025
            base_riesgo = 0.45
        else:
            factor_control = 0.018
            base_riesgo = 0.40

        self.indice_control_fuego = min(1.0, self.indice_control_fuego + factor_control * eficacia * delta_time)
        self.riesgo_reignicion = max(0.0, min(1.0, base_riesgo * (1.0 - self.indice_control_fuego)))

        self._tiempo_reporte_s += delta_time
        if self._tiempo_reporte_s >= 20:
            self._tiempo_reporte_s = 0.0
            self.reportes_operativos.append({
                'control_fuego': round(self.indice_control_fuego, 2),
                'riesgo_reignicion': round(self.riesgo_reignicion, 2),
                'agua_pct': round(100.0 * recurso_agua, 1),
                'espuma_pct': round(100.0 * recurso_espuma, 1),
                'efectivos_operativos': self.efectivos_operativos,
            })
            if len(self.reportes_operativos) > 25:
                self.reportes_operativos = self.reportes_operativos[-25:]

    def finalizar_intervencion(self):

        if self.tipo_incendio:
            logger.info(f"[Bomberos {self.id[:8]}] Recargando tras incendio {self.tipo_incendio}")
        self.tipo_incendio = None
        self.escala_desplegada = False
        self.caudal_agua_lpm = 0.0
        self.caudal_espuma_lpm = 0.0
        self.agua_litros = self.AGUA_CAPACIDAD_L
        self.espuma_litros = self.ESPUMA_CAPACIDAD_L
        self.indice_control_fuego = 0.0
        self.riesgo_reignicion = 0.0
        self._tiempo_reporte_s = 0.0

    def obtener_estado_especializado(self):
        ultimo_reporte = self.reportes_operativos[-1] if self.reportes_operativos else None
        return {
            'rol': self.rol,
            'agua_litros': round(self.agua_litros, 0),
            'agua_pct': round(100.0 * self.agua_litros / self.AGUA_CAPACIDAD_L, 1),
            'espuma_litros': round(self.espuma_litros, 0),
            'espuma_pct': round(100.0 * self.espuma_litros / self.ESPUMA_CAPACIDAD_L, 1),
            'caudal_agua_lpm': round(self.caudal_agua_lpm, 0),
            'caudal_espuma_lpm': round(self.caudal_espuma_lpm, 0),
            'escala_desplegada': self.escala_desplegada,
            'tipo_incendio': self.tipo_incendio,
            'efectivos_operativos': self.efectivos_operativos,
            'control_fuego': round(self.indice_control_fuego, 2),
            'riesgo_reignicion': round(self.riesgo_reignicion, 2),
            'reportes_emitidos': len(self.reportes_operativos),
            'ultimo_reporte_operativo': ultimo_reporte,
        }
