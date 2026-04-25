

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

        if str(self.escenario_activo).lower() == self.ESTADO_BASE.lower():
            self.riesgo_dinamico = max(0.05, self.riesgo_dinamico - 0.0005 * delta_time)

    def finalizar_intervencion(self):
        self.protocolo_contencion = 'ninguno'

    def obtener_estado_especializado(self):
        return {
            'riesgo_dinamico': round(self.riesgo_dinamico, 2),
            'protocolo_contencion': self.protocolo_contencion,
            'agentes_operativos': self.agentes_operativos,
            'detenidos': self.detenidos
        }
