

import random
import logging
import threading
from vehiculo_base import VehiculoBase
from historial_clinico import obtener_historial

logger = logging.getLogger(__name__)

NIVELES_SOPORTE = ('BLS', 'ALS')
EVOLUCIONES = ('estable', 'mejora', 'deterioro_leve', 'deterioro_grave', 'critico')

class Ambulancia(VehiculoBase):
    TIPO = 'ambulancia'
    ESTADO_BASE = 'en_base'
    VELOCIDAD_CRUCERO = 0   

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
        self.tiempo_desde_ultimo_reporte_s = 0.0
        self.reportes_clinicos = []
        self.alertas_clinicas = []

        # Historial clinico obtenido del nodo de salud de la isla.
        # Se rellena de forma asincrona en on_asignacion_incidente().
        self._historial_clinico: dict = {}
        self._historial_lock = threading.Lock()
        self._incidente_actual_dict: dict = {}

    def _iniciar_ruta_patrulla(self):

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

        litros = (self.consumo_oxigeno_lpm / 60.0) * delta_time
        self.oxigeno_litros = max(0.0, self.oxigeno_litros - litros)

        self.tiempo_atencion_s += delta_time

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
        else:  
            sv['fc'] = sv.get('fc', 80) + random.uniform(-0.05, 0.05)
            sv['spo2'] = sv.get('spo2', 95) + random.uniform(-0.02, 0.02)

        self._actualizar_alertas_clinicas(sv)
        self._emitir_reporte_clinico(delta_time, sv)

    def _actualizar_alertas_clinicas(self, sv: dict):
        fc = sv.get('fc')
        spo2 = sv.get('spo2')
        ta_sist = sv.get('ta_sist')

        alertas = []
        if isinstance(fc, (int, float)) and (fc >= 140 or fc <= 45):
            alertas.append('frecuencia_cardiaca_critica')
        if isinstance(spo2, (int, float)) and spo2 < 90:
            alertas.append('spo2_baja')
        if isinstance(ta_sist, (int, float)) and ta_sist < 90:
            alertas.append('hipotension')

        self.alertas_clinicas = alertas

    def _emitir_reporte_clinico(self, delta_time: float, sv: dict):
        self.tiempo_desde_ultimo_reporte_s += float(delta_time or 0)
        if self.tiempo_desde_ultimo_reporte_s < 30.0:
            return

        self.tiempo_desde_ultimo_reporte_s = 0.0
        reporte = {
            't_atencion_s': int(self.tiempo_atencion_s),
            'triage': (self.paciente or {}).get('triage'),
            'fc': round(float(sv.get('fc', 0)), 1) if isinstance(sv.get('fc'), (int, float)) else None,
            'spo2': round(float(sv.get('spo2', 0)), 1) if isinstance(sv.get('spo2'), (int, float)) else None,
            'ta_sist': round(float(sv.get('ta_sist', 0)), 1) if isinstance(sv.get('ta_sist'), (int, float)) else None,
            'alertas': list(self.alertas_clinicas),
        }
        self.reportes_clinicos.append(reporte)
        if len(self.reportes_clinicos) > 20:
            self.reportes_clinicos = self.reportes_clinicos[-20:]

    # ------------------------------------------------------------------
    # Historial clinico — nodo de salud de la isla
    # ------------------------------------------------------------------

    def on_asignacion_incidente(self, incidente: dict) -> None:
        """Consulta el nodo de salud de forma asincrona nada mas recibir
        la asignacion, para que el historial clinico este disponible
        mientras la ambulancia se dirige al punto de intervencion.

        Almacena el resultado en `_historial_clinico` de forma thread-safe.
        """
        self._incidente_actual_dict = dict(incidente or {})
        with self._historial_lock:
            self._historial_clinico = {}

        def _consultar():
            paciente_id = (
                incidente.get('paciente_id')
                or incidente.get('patient_id')
                or incidente.get('incident_id')
                or f"PAC-{incidente.get('id', self.id)}"
            )
            try:
                historial = obtener_historial(paciente_id, incidente=incidente)
                with self._historial_lock:
                    self._historial_clinico = historial
                logger.info(
                    "[Ambulancia %s] Historial clinico disponible (fuente=%s, alergias=%s)",
                    self.id[:8],
                    historial.get('fuente', '?'),
                    historial.get('alergias', []),
                )
            except Exception as exc:
                logger.warning(
                    "[Ambulancia %s] No se pudo obtener historial clinico: %s",
                    self.id[:8], exc,
                )

        threading.Thread(target=_consultar, daemon=True,
                         name=f"hc-{self.id[:6]}").start()

    def obtener_contexto_mision(self) -> dict:
        """Devuelve el historial clinico disponible para el personal medico
        a bordo. Si la consulta al nodo de salud aun no ha terminado,
        devuelve lo que haya (puede ser un dict vacio).
        """
        with self._historial_lock:
            historial = dict(self._historial_clinico)
        return {
            "tipo_contexto": "historial_clinico",
            "incidente_id": self._incidente_actual_dict.get('incident_id'),
            "historial": historial,
            "disponible": bool(historial),
        }

    # ------------------------------------------------------------------

    def finalizar_intervencion(self):

        if self.paciente_a_bordo:
            logger.info(f"[Ambulancia {self.id[:8]}] Paciente entregado tras {self.tiempo_atencion_s:.0f}s")
        self.paciente_a_bordo = False
        self.paciente = None
        self.tiempo_atencion_s = 0.0
        self.tiempo_desde_ultimo_reporte_s = 0.0
        self.consumo_oxigeno_lpm = 0.0
        self.alertas_clinicas = []
        with self._historial_lock:
            self._historial_clinico = {}
        self._incidente_actual_dict = {}

    def obtener_estado_especializado(self):
        sv = (self.paciente or {}).get('signos_vitales') if self.paciente else None
        if isinstance(sv, dict):
            sv = {k: round(v, 1) if isinstance(v, (int, float)) else v for k, v in sv.items()}

        ultimo_reporte = self.reportes_clinicos[-1] if self.reportes_clinicos else None

        with self._historial_lock:
            historial_resumen = {
                "disponible": bool(self._historial_clinico),
                "fuente": self._historial_clinico.get('fuente'),
                "alergias": self._historial_clinico.get('alergias', []),
                "medicacion_activa": self._historial_clinico.get('medicacion_activa', []),
                "antecedentes": self._historial_clinico.get('antecedentes', []),
                "notas_clinicas": self._historial_clinico.get('notas_clinicas', []),
                "grupo_sanguineo": self._historial_clinico.get('grupo_sanguineo'),
                "obtenido_en": self._historial_clinico.get('obtenido_en'),
            } if self._historial_clinico else {"disponible": False}

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
            'tiempo_atencion_s': int(self.tiempo_atencion_s),
            'alertas_clinicas': list(self.alertas_clinicas),
            'reportes_emitidos': len(self.reportes_clinicos),
            'ultimo_reporte_clinico': ultimo_reporte,
            'historial_clinico': historial_resumen,
        }
