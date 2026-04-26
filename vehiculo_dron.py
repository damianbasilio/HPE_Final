

import logging
from vehiculo_base import VehiculoBase

logger = logging.getLogger(__name__)

MODOS_DRON = ('scout', 'seguimiento', 'mapeo', 'termico')

class VehiculoDron(VehiculoBase):
    TIPO = 'dron'
    ESTADO_BASE = 'aterrizado'
    VELOCIDAD_CRUCERO = 0

    BATERIA_CAPACIDAD_MIN = 25     

    def __init__(self, id_vehiculo, propulsion='unico', metadatos=None):

        super().__init__(id_vehiculo, propulsion='unico', metadatos=metadatos)

        self.modo = 'scout'
        self.altitud_m = 0.0
        self.link_pct = 100.0          
        self.imagenes_capturadas = 0
        self.tiempo_vuelo_s = 0.0
        self.objetivo_bloqueado = False
        self.riesgo_detectado = 'ninguno'
        self.eventos_detectados = 0
        self._tiempo_reporte_s = 0.0
        self._acumulador_deteccion_s = 0.0
        self.reportes_aereos = []

    def _init_telemetria(self):

        super()._init_telemetria()
        self.combustible = 100.0           
        self.nivel_aceite = 100.0
        self.desgaste_frenos = 0.0
        self.desgaste_neumaticos = 0.0
        self.temperatura_motor = 30.0      

    def _iniciar_ruta_patrulla(self):

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

        objetivo = modificadores.get('objetivo_bloqueado')
        if objetivo is not None:
            self.objetivo_bloqueado = bool(objetivo)

    def actualizar_logica_especializada(self, delta_time):

        if self.en_camino or self.en_escena:
            self.tiempo_vuelo_s += delta_time

            descarga_pct_s = 100.0 / (self.BATERIA_CAPACIDAD_MIN * 60.0)
            self.combustible = max(0.0, self.combustible - descarga_pct_s * delta_time)

            if self.en_escena and self.modo in ('scout', 'mapeo', 'termico'):
                self.imagenes_capturadas += 1
                self.objetivo_bloqueado = True
                self._acumulador_deteccion_s += delta_time

                umbral = 8.0 if self.modo == 'termico' else 12.0
                if self._acumulador_deteccion_s >= umbral:
                    self._acumulador_deteccion_s = 0.0
                    self.eventos_detectados += 1
                    if self.modo == 'termico':
                        self.riesgo_detectado = 'hotspot_termico'

            self.link_pct = max(50.0, self.link_pct - 0.001 * delta_time * (self.altitud_m / 100.0))

            if self.combustible < 20:
                self.riesgo_detectado = 'bateria_critica'
            elif self.link_pct < 65:
                self.riesgo_detectado = 'enlace_debil'
            elif self.riesgo_detectado not in ('hotspot_termico',):
                self.riesgo_detectado = 'ninguno'

            self._tiempo_reporte_s += delta_time
            if self._tiempo_reporte_s >= 15:
                self._tiempo_reporte_s = 0.0
                self.reportes_aereos.append({
                    'modo': self.modo,
                    'altitud_m': round(self.altitud_m, 1),
                    'bateria_pct': round(self.combustible, 1),
                    'link_pct': round(self.link_pct, 1),
                    'objetivo_bloqueado': self.objetivo_bloqueado,
                    'riesgo_detectado': self.riesgo_detectado,
                    'eventos_detectados': self.eventos_detectados,
                })
                if len(self.reportes_aereos) > 30:
                    self.reportes_aereos = self.reportes_aereos[-30:]
        else:
            self.objetivo_bloqueado = False
            self.link_pct = min(100.0, self.link_pct + 0.01 * delta_time)

    def _verificar_combustible(self):

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
        self.objetivo_bloqueado = False
        self.riesgo_detectado = 'ninguno'
        self._tiempo_reporte_s = 0.0
        self._acumulador_deteccion_s = 0.0

    def obtener_estado_especializado(self):
        ultimo_reporte = self.reportes_aereos[-1] if self.reportes_aereos else None
        return {
            'modo': self.modo,
            'altitud_m': round(self.altitud_m, 1),
            'bateria_pct': round(self.combustible, 1),
            'link_pct': round(self.link_pct, 1),
            'autonomia_restante_min': round((self.combustible / 100.0) * self.BATERIA_CAPACIDAD_MIN, 1),
            'imagenes_capturadas': self.imagenes_capturadas,
            'tiempo_vuelo_s': int(self.tiempo_vuelo_s),
            'objetivo_bloqueado': self.objetivo_bloqueado,
            'riesgo_detectado': self.riesgo_detectado,
            'eventos_detectados': self.eventos_detectados,
            'reportes_emitidos': len(self.reportes_aereos),
            'ultimo_reporte_aereo': ultimo_reporte,
        }
