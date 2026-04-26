

from vehiculo_base import VehiculoBase

PROTOCOLOS_VALIDOS = ('ninguno', 'perimetro', 'dispersion', 'armado')

class VehiculoPolicial(VehiculoBase):
    TIPO = 'policia'
    ESTADO_BASE = 'patrulla'

    def __init__(self, id_vehiculo, propulsion='combustion', metadatos=None):
        super().__init__(id_vehiculo, propulsion=propulsion, metadatos=metadatos)
        self.riesgo_dinamico = 0.10
        self.protocolo_contencion = 'ninguno'
        self.agentes_operativos = self.dotacion
        self.detenidos = 0
        self.tiempo_contencion_s = 0.0
        self._acumulador_historial_s = 0.0
        self.historial_riesgo = []

    def aplicar_modificadores_especificos(self, tipo_escenario, modificadores, intensidad):

        salto = 0.4 * float(intensidad or 0)
        self.riesgo_dinamico = min(1.0, self.riesgo_dinamico + salto)

        protocolo = str(modificadores.get('protocolo_contencion', '')).lower()
        if protocolo in PROTOCOLOS_VALIDOS:
            self.protocolo_contencion = protocolo
        elif intensidad >= 0.8:
            self.protocolo_contencion = 'armado'
        elif intensidad >= 0.5:
            self.protocolo_contencion = 'perimetro'
        else:
            self.protocolo_contencion = 'ninguno'

        detenidos = modificadores.get('detenidos_estimados')
        if isinstance(detenidos, (int, float)) and detenidos >= 0:
            self.detenidos += int(detenidos)

    def actualizar_logica_especializada(self, delta_time):
        activo = str(self.escenario_activo).lower()
        if activo == self.ESTADO_BASE.lower():
            self.riesgo_dinamico = max(0.05, self.riesgo_dinamico - 0.0005 * delta_time)
            if self.riesgo_dinamico < 0.2:
                self.protocolo_contencion = 'ninguno'
            return

        if self.en_escena:
            if self.protocolo_contencion == 'armado':
                self.riesgo_dinamico = min(1.0, self.riesgo_dinamico + 0.0015 * delta_time)
            elif self.protocolo_contencion == 'perimetro':
                self.riesgo_dinamico = min(1.0, self.riesgo_dinamico + 0.0010 * delta_time)
            else:
                self.riesgo_dinamico = min(1.0, self.riesgo_dinamico + 0.0006 * delta_time)
        elif self.en_camino:
            self.riesgo_dinamico = min(1.0, self.riesgo_dinamico + 0.0008 * delta_time)
        else:
            self.riesgo_dinamico = max(0.05, self.riesgo_dinamico - 0.0003 * delta_time)

        if self.riesgo_dinamico >= 0.8:
            self.protocolo_contencion = 'armado'
        elif self.riesgo_dinamico >= 0.5:
            self.protocolo_contencion = 'perimetro'
        else:
            self.protocolo_contencion = 'ninguno'

        if self.protocolo_contencion != 'ninguno':
            self.tiempo_contencion_s += delta_time

        self._acumulador_historial_s += delta_time
        if self._acumulador_historial_s >= 15:
            self._acumulador_historial_s = 0.0
            self.historial_riesgo.append({
                't_contencion_s': int(self.tiempo_contencion_s),
                'riesgo': round(self.riesgo_dinamico, 2),
                'protocolo': self.protocolo_contencion,
            })
            if len(self.historial_riesgo) > 30:
                self.historial_riesgo = self.historial_riesgo[-30:]

    def finalizar_intervencion(self):
        self.riesgo_dinamico = max(0.2, min(self.riesgo_dinamico, 0.45))
        self.protocolo_contencion = 'ninguno'

    def obtener_estado_especializado(self):
        return {
            'riesgo_dinamico': round(self.riesgo_dinamico, 2),
            'protocolo_contencion': self.protocolo_contencion,
            'agentes_operativos': self.agentes_operativos,
            'detenidos': self.detenidos,
            'tiempo_contencion_s': int(self.tiempo_contencion_s),
            'historial_riesgo': self.historial_riesgo[-8:]
        }
