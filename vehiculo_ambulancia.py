# Unidad ambulancia. Idiosincrasia operativa propia:
#   - Nivel de soporte (BLS basico / ALS avanzado).
#   - Estado del paciente con signos vitales (FC, SpO2, TA, evolucion).
#   - Reservas de oxigeno (litros) que se consumen en intervenciones.
#   - No patrulla por defecto: espera en hospital base.

import random
import logging
from vehiculo_base import VehiculoBase

logger = logging.getLogger(__name__)

NIVELES_SOPORTE = ('BLS', 'ALS')
EVOLUCIONES = ('estable', 'mejora', 'deterioro_leve', 'deterioro_grave', 'critico')


class Ambulancia(VehiculoBase):
    TIPO = 'ambulancia'
    ESTADO_BASE = 'en_base'
    VELOCIDAD_CRUCERO = 0   # En base no se mueve hasta recibir aviso

    # Capacidad de la botella de oxigeno (litros)
    OXIGENO_CAPACIDAD_L = 2000

    def __init__(self, id_vehiculo, propulsion='combustion', metadatos=None):
        super().__init__(id_vehiculo, propulsion=propulsion, metadatos=metadatos)

        nivel = (metadatos or {}).get('nivel_soporte', 'ALS')
        self.nivel_soporte = nivel if nivel in NIVELES_SOPORTE else 'ALS'

        self.oxigeno_litros = self.OXIGENO_CAPACIDAD_L
        self.consumo_oxigeno_lpm = 0.0

        self.paciente_a_bordo = False
        self.paciente = None
        self.tiempo_atencion_s = 0.0

    def _iniciar_ruta_patrulla(self):
        # Las ambulancias no patrullan: se quedan en el hospital base esperando.
        self.escenario_activo = self.ESTADO_BASE
        self.en_movimiento = False
        self.velocidad_objetivo = 0

    def aplicar_modificadores_especificos(self, tipo_escenario, modificadores, intensidad):
        clinico = modificadores.get('estado_clinico') if isinstance(modificadores, dict) else None
        paciente = modificadores.get('paciente') if isinstance(modificadores, dict) else None

        if paciente or clinico:
            self.paciente_a_bordo = True
            self.paciente = self._construir_paciente(paciente, clinico)

        consumo = (modificadores or {}).get('consumo_oxigeno_lpm')
        if consumo is not None:
            try:
                self.consumo_oxigeno_lpm = max(0.0, float(consumo))
            except (TypeError, ValueError):
                pass
        else:
            # Si no viene, lo inferimos de la intensidad y del soporte clinico.
            base = 8.0 if self.nivel_soporte == 'ALS' else 4.0
            self.consumo_oxigeno_lpm = base * (0.5 + float(intensidad or 0))

    def _construir_paciente(self, paciente_raw, clinico_raw):
        paciente_raw = paciente_raw or {}
        clinico_raw = clinico_raw or {}

        return {
            'nombre': paciente_raw.get('nombre', 'desconocido'),
            'edad': paciente_raw.get('edad'),
            'sexo': paciente_raw.get('sexo'),
            'condiciones': paciente_raw.get('condiciones'),
            'urgencia': paciente_raw.get('urgencia'),
            'triage': clinico_raw.get('triage', 'amarillo'),
            'prioridad_traslado': clinico_raw.get('prioridad_traslado', 'P2'),
            'ventana_critica_min': clinico_raw.get('ventana_critica_minutos'),
            'riesgo_deterioro': clinico_raw.get('riesgo_deterioro_en_ruta', 'medio'),
            'especialidad_requerida': clinico_raw.get('especialidad_requerida', 'general'),
            'evolucion': clinico_raw.get('evolucion_esperada', 'estable'),
            'soporte_clinico': clinico_raw.get('soporte_clinico_requerido', []),
            'signos_vitales': self._signos_vitales_iniciales(clinico_raw)
        }

    def _signos_vitales_iniciales(self, clinico):
        # Si la IA mando signos los aceptamos; si no, simulamos un set realista.
        signos = clinico.get('signos_vitales') if isinstance(clinico, dict) else None
        if isinstance(signos, dict):
            return signos

        triage = (clinico or {}).get('triage', 'amarillo')
        bases = {
            'rojo':     {'fc': 130, 'spo2': 88, 'ta_sist': 90,  'ta_diast': 55, 'temp': 37.5},
            'amarillo': {'fc': 105, 'spo2': 94, 'ta_sist': 130, 'ta_diast': 80, 'temp': 37.0},
            'verde':    {'fc': 85,  'spo2': 98, 'ta_sist': 120, 'ta_diast': 75, 'temp': 36.8},
            'negro':    {'fc': 0,   'spo2': 0,  'ta_sist': 0,   'ta_diast': 0,  'temp': 0.0},
        }
        return bases.get(triage, bases['amarillo'])

    def actualizar_logica_especializada(self, delta_time):
        if not self.paciente_a_bordo or not self.paciente:
            return

        # Consumo de oxigeno en litros.
        litros = (self.consumo_oxigeno_lpm / 60.0) * delta_time
        self.oxigeno_litros = max(0.0, self.oxigeno_litros - litros)

        self.tiempo_atencion_s += delta_time

        # Evolucion ligera de los signos vitales segun la 'evolucion' prevista.
        evol = self.paciente.get('evolucion', 'estable')
        sv = self.paciente.get('signos_vitales', {})
        if not isinstance(sv, dict):
            return

        if evol == 'mejora':
            sv['fc'] = max(60, sv.get('fc', 80) - 0.02 * delta_time)
            sv['spo2'] = min(100, sv.get('spo2', 95) + 0.01 * delta_time)
        elif evol == 'deterioro_leve':
            sv['fc'] = min(180, sv.get('fc', 80) + 0.03 * delta_time)
            sv['spo2'] = max(70, sv.get('spo2', 95) - 0.015 * delta_time)
        elif evol == 'deterioro_grave':
            sv['fc'] = min(200, sv.get('fc', 80) + 0.07 * delta_time)
            sv['spo2'] = max(60, sv.get('spo2', 95) - 0.04 * delta_time)
            sv['ta_sist'] = max(50, sv.get('ta_sist', 120) - 0.05 * delta_time)
        else:  # estable
            sv['fc'] = sv.get('fc', 80) + random.uniform(-0.05, 0.05)
            sv['spo2'] = sv.get('spo2', 95) + random.uniform(-0.02, 0.02)

    def finalizar_intervencion(self):
        # Paciente entregado en hospital. Reseteamos.
        if self.paciente_a_bordo:
            logger.info(f"[Ambulancia {self.id[:8]}] Paciente entregado tras {self.tiempo_atencion_s:.0f}s")
        self.paciente_a_bordo = False
        self.paciente = None
        self.tiempo_atencion_s = 0.0
        self.consumo_oxigeno_lpm = 0.0

    def obtener_estado_especializado(self):
        sv = (self.paciente or {}).get('signos_vitales') if self.paciente else None
        if isinstance(sv, dict):
            sv = {k: round(v, 1) if isinstance(v, (int, float)) else v for k, v in sv.items()}

        return {
            'nivel_soporte': self.nivel_soporte,
            'oxigeno_litros': round(self.oxigeno_litros, 1),
            'oxigeno_pct': round(100.0 * self.oxigeno_litros / self.OXIGENO_CAPACIDAD_L, 1),
            'consumo_oxigeno_lpm': round(self.consumo_oxigeno_lpm, 1),
            'paciente_a_bordo': self.paciente_a_bordo,
            'paciente': {
                **(self.paciente or {}),
                'signos_vitales': sv
            } if self.paciente else None,
            'tiempo_atencion_s': int(self.tiempo_atencion_s)
        }
