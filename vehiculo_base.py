

import random
import math
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

from config import (
    RANGO_COMBUSTIBLE_INICIAL, RANGO_KM_INICIAL, RANGO_TEMP_INICIAL, RANGO_ACEITE_INICIAL,
    RANGO_DESGASTE_FRENOS, RANGO_DESGASTE_NEUMATICOS, VELOCIDAD_PATRULLA,
    TASA_REABASTECIMIENTO, UMBRAL_COMBUSTIBLE, TEMP_AMBIENTE, TEMP_MAXIMA, VELOCIDAD_MAXIMA,
    DIST_MAX_RASTRO, obtener_tarifa
)
from gps import SimuladorGPS
from rutas import generar_ruta_patrulla

class VehiculoBase:

    TIPO = 'generico'

    ESTADO_BASE = 'patrulla'

    VELOCIDAD_CRUCERO = VELOCIDAD_PATRULLA

    def __init__(self, id_vehiculo, propulsion='combustion', metadatos=None):
        self.id = id_vehiculo
        self.propulsion = propulsion if propulsion in ('combustion', 'electrico', 'unico') else 'combustion'
        self.metadatos = metadatos or {}

        tarifa = obtener_tarifa(self.TIPO, self.propulsion) or {}
        self.coste_min = float(tarifa.get('coste_min', 1.0))
        self.coste_activacion = float(tarifa.get('coste_activacion', 10.0))
        self.dotacion = int(tarifa.get('dotacion', 2))
        self.velocidad_max_unidad = int(tarifa.get('velocidad_max', VELOCIDAD_MAXIMA))

        self._init_telemetria()
        self._init_dinamica()
        self._init_escenario()
        self._init_costes()
        self.gps = SimuladorGPS()

        self._iniciar_ruta_patrulla()

    def _init_telemetria(self):
        self.combustible = random.uniform(*RANGO_COMBUSTIBLE_INICIAL)
        self.km_totales = random.randint(*RANGO_KM_INICIAL)
        self.temperatura_motor = random.uniform(*RANGO_TEMP_INICIAL)
        self.nivel_aceite = random.uniform(*RANGO_ACEITE_INICIAL)
        self.desgaste_frenos = random.uniform(*RANGO_DESGASTE_FRENOS)
        self.desgaste_neumaticos = random.uniform(*RANGO_DESGASTE_NEUMATICOS)

    def _init_dinamica(self):
        self.velocidad = 0
        self.velocidad_objetivo = self.VELOCIDAD_CRUCERO
        self.consumo_factor = 1.0
        self.temp_factor = 1.0
        self.desgaste_factor = 1.0
        self.aceleracion_max = 5
        self.en_movimiento = True
        self.rastro = []
        self.version_ruta = 0

        self.factor_entorno = 1.0

    def _init_escenario(self):
        self.escenario_activo = self.ESTADO_BASE
        self.tiempo_escenario_inicio = datetime.now()
        self.duracion_escenario = float('inf')
        self.tiempo_escenario_sim = 0
        self.distancia_recorrida = 0

        self.reabasteciendo = False
        self.tiempo_restante_recarga = 0
        self.en_camino = False
        self.tiempo_restante_viaje = 0
        self.tiempo_total_viaje = 0
        self.en_escena = False
        self.tiempo_restante_escena = 0
        self.comportamiento_escena = 'estacionario'
        self.velocidad_escena = None
        self.perfil_velocidad = None

    def _init_costes(self):

        self.coste_total_eur = 0.0

        self.coste_intervencion_eur = 0.0

        self.intervenciones_realizadas = 0

        self._segundos_facturados = 0.0

    def _iniciar_ruta_patrulla(self):

        try:
            ruta = generar_ruta_patrulla()
            if ruta and len(ruta) >= 2:
                self.gps.establecer_ruta(ruta)
                self.version_ruta += 1
        except Exception as e:
            logger.warning(f"Error generando ruta patrulla inicial: {e}")

    def actualizar_simulacion(self, delta_time=1):
        self._actualizar_tiempo(delta_time)

        if self.reabasteciendo:
            self._procesar_reabastecimiento(delta_time)
            self._acumular_costes(delta_time)
            return

        self._procesar_fases(delta_time)
        objetivo = self._calcular_velocidad_objetivo()
        self._actualizar_velocidad(objetivo, delta_time)
        self._actualizar_motor(delta_time)
        self._actualizar_consumo(delta_time)
        self._actualizar_desgaste(delta_time)
        self._actualizar_kilometraje(delta_time)
        self._acumular_costes(delta_time)

        if self.velocidad > 0:
            self.gps.actualizar(self.velocidad, delta_time)

        self._acumular_rastro()
        self._verificar_ruta_completada()
        self._verificar_combustible()

        self.actualizar_logica_especializada(delta_time)

    def _acumular_costes(self, delta_time):

        if self._esta_en_intervencion():
            self._segundos_facturados += delta_time
            coste_tiempo = (self._segundos_facturados / 60.0) * self.coste_min
            self.coste_intervencion_eur = self.coste_activacion + coste_tiempo

    def _esta_en_intervencion(self):
        activo = str(self.escenario_activo or '').lower()
        return activo not in (self.ESTADO_BASE.lower(), 'reabasteciendo combustible', '')

    def _consolidar_coste_intervencion(self):
        if self.coste_intervencion_eur > 0:
            self.coste_total_eur += self.coste_intervencion_eur
            self.intervenciones_realizadas += 1
        self.coste_intervencion_eur = 0.0
        self._segundos_facturados = 0.0

    def obtener_tipo(self):
        return self.TIPO

    def obtener_estado_especializado(self):

        return {}

    def aplicar_modificadores_especificos(self, tipo_escenario, modificadores, intensidad):

        pass

    def actualizar_logica_especializada(self, delta_time):

        pass

    def finalizar_intervencion(self):

        pass

    def _acumular_rastro(self):
        if self.velocidad <= 0:
            return
        pos = [round(self.gps.latitud, 6), round(self.gps.longitud, 6)]
        if self.rastro and self.rastro[-1] == pos:
            return
        if self.rastro:
            ultimo = self.rastro[-1]
            dist_sq = (pos[0] - ultimo[0])**2 + (pos[1] - ultimo[1])**2
            if dist_sq < 0.00005**2:
                return
        self.rastro.append(pos)
        self._recortar_rastro()

    def _recortar_rastro(self):
        if len(self.rastro) < 3:
            return
        total = 0
        for i in range(len(self.rastro) - 1, 0, -1):
            p1 = self.rastro[i]
            p2 = self.rastro[i - 1]
            dlat = p1[0] - p2[0]
            dlon = p1[1] - p2[1]
            total += ((dlat * 111.32)**2 + (dlon * 111.32 * 0.75)**2)**0.5
            if total > DIST_MAX_RASTRO:
                self.rastro = self.rastro[i:]
                return

    def _verificar_ruta_completada(self):
        activo = str(self.escenario_activo or '').lower()
        if self.gps.progreso_ruta >= 0.99 and activo == self.ESTADO_BASE.lower():
            try:
                ruta = generar_ruta_patrulla()
                if ruta and len(ruta) >= 2:
                    self.gps.establecer_ruta(ruta)
                    self.version_ruta += 1
            except Exception as e:
                logger.warning(f"Error generando nueva ruta patrulla: {e}")

    def _actualizar_tiempo(self, delta_time):
        if self.escenario_activo and str(self.escenario_activo).lower() != self.ESTADO_BASE.lower():
            self.tiempo_escenario_sim += delta_time

    def _procesar_reabastecimiento(self, delta_time):
        self.velocidad = 0
        self.velocidad_objetivo = 0
        self.en_movimiento = False

        self.combustible = min(100.0, self.combustible + TASA_REABASTECIMIENTO * delta_time)
        self.tiempo_restante_recarga = max(0, self.tiempo_restante_recarga - delta_time)

        if self.tiempo_restante_recarga <= 0 or self.combustible >= 100.0:
            self.reabasteciendo = False
            self.terminar_escenario()

        if self.temperatura_motor > TEMP_AMBIENTE:
            self.temperatura_motor = max(TEMP_AMBIENTE, self.temperatura_motor - 0.05 * delta_time)

    def _procesar_fases(self, delta_time):
        if self.en_camino:
            self.tiempo_restante_viaje = max(0, self.tiempo_restante_viaje - delta_time)

            if self.tiempo_restante_viaje <= 0:
                self.en_camino = False
                self.en_escena = True
                self._configurar_llegada_escena()

        elif self.en_escena:
            self.tiempo_restante_escena = max(0, self.tiempo_restante_escena - delta_time)

            if self.comportamiento_escena == 'estacionario':
                self._procesar_escena_estacionaria(delta_time)
            else:
                self.en_movimiento = True

            if self.tiempo_restante_escena <= 0:
                self.terminar_escenario()

        else:
            if (self.duracion_escenario != float('inf') and
                self.tiempo_escenario_sim >= self.duracion_escenario):
                self.terminar_escenario()

    def _configurar_llegada_escena(self):
        if not self.tiempo_restante_escena:
            if self.duracion_escenario != float('inf'):
                self.tiempo_restante_escena = max(0, self.duracion_escenario)
            else:
                self.tiempo_restante_escena = 0

        if self.comportamiento_escena == 'movimiento':
            if self.velocidad_escena is not None:
                self.velocidad_objetivo = float(self.velocidad_escena)
            else:
                self.velocidad_objetivo = self.velocidad or self.VELOCIDAD_CRUCERO
            self.en_movimiento = True
        else:
            self.velocidad_objetivo = 0
            self.velocidad = 0
            self.en_movimiento = False

    def _procesar_escena_estacionaria(self, delta_time):
        if self.temperatura_motor > TEMP_AMBIENTE:
            self.temperatura_motor = max(TEMP_AMBIENTE, self.temperatura_motor - 0.02 * delta_time)
        self.en_movimiento = False
        self.velocidad_objetivo = 0
        self.velocidad = 0

    def _calcular_velocidad_objetivo(self):
        objetivo = self.velocidad_objetivo
        pv = self.perfil_velocidad

        if not pv:
            return objetivo

        if self.en_camino:
            objetivo = self._aplicar_perfil_en_ruta(pv, objetivo)
        elif self.en_escena and self.comportamiento_escena == 'movimiento':
            objetivo = pv.get('vel_llegada') or pv.get('vel_sostenida') or objetivo
        elif self.duracion_escenario != float('inf'):
            objetivo = self._aplicar_perfil_duracion(pv, objetivo)

        var = pv.get('variabilidad')
        if var:
            jitter = random.uniform(-var * 5.0, var * 5.0)
            objetivo = max(0, objetivo + jitter)

        factor = max(0.2, min(1.5, float(getattr(self, 'factor_entorno', 1.0) or 1.0)))
        objetivo = objetivo * factor

        return objetivo

    def _aplicar_perfil_en_ruta(self, pv, objetivo):
        if not self.tiempo_total_viaje:
            return objetivo

        transcurrido = max(0, self.tiempo_total_viaje - self.tiempo_restante_viaje)
        fraccion = transcurrido / float(self.tiempo_total_viaje) if self.tiempo_total_viaje > 0 else 0

        if pv.get('vel_inicial') is not None and transcurrido < 5:
            return pv['vel_inicial']
        elif pv.get('vel_pico') is not None and fraccion < 0.25:
            return pv['vel_pico']
        elif pv.get('vel_sostenida') is not None:
            return pv['vel_sostenida']

        return objetivo

    def _aplicar_perfil_duracion(self, pv, objetivo):
        if self.duracion_escenario <= 0:
            return objetivo

        fraccion = self.tiempo_escenario_sim / float(self.duracion_escenario)

        if pv.get('vel_pico') is not None and fraccion < 0.2:
            return pv['vel_pico']
        elif pv.get('vel_sostenida') is not None:
            return pv['vel_sostenida']

        return objetivo

    def _actualizar_velocidad(self, objetivo, delta_time):
        if self.velocidad != objetivo:
            diff = objetivo - self.velocidad

            if abs(diff) > self.aceleracion_max * delta_time:
                self.velocidad += math.copysign(self.aceleracion_max * delta_time, diff)
            else:
                self.velocidad = objetivo

        if self.velocidad > 0:
            self.velocidad = max(0, self.velocidad + random.uniform(-0.5, 0.5))

    def _actualizar_motor(self, delta_time):
        if self.velocidad > 0:
            incremento = (self.velocidad / 100.0) * 0.2 * self.temp_factor * delta_time
            self.temperatura_motor += incremento

        enfriamiento = 1.0 * delta_time
        if self.temperatura_motor > TEMP_AMBIENTE:
            tasa = 2.5 if self.velocidad == 0 else 0.2
            self.temperatura_motor = max(TEMP_AMBIENTE, self.temperatura_motor - enfriamiento * tasa)

        self.temperatura_motor = max(TEMP_AMBIENTE, min(self.temperatura_motor, TEMP_MAXIMA))

    def _actualizar_consumo(self, delta_time):
        if self.velocidad > 0:

            factor_propulsion = 0.7 if self.propulsion == 'electrico' else 1.0
            consumo = (self.velocidad / 100.0) * 0.01 * self.consumo_factor * factor_propulsion * delta_time
            self.combustible = max(0, self.combustible - consumo)

    def _actualizar_desgaste(self, delta_time):
        if self.velocidad > 0:
            factor = (self.velocidad / 200.0) * 0.05 * self.desgaste_factor * delta_time
            self.desgaste_frenos = min(100, self.desgaste_frenos + factor * 0.3)
            self.desgaste_neumaticos = min(100, self.desgaste_neumaticos + factor * 0.5)

    def _actualizar_kilometraje(self, delta_time):
        metros = (self.velocidad / 3.6) * delta_time
        km = metros / 1000.0
        self.km_totales += km
        self.distancia_recorrida += km

    def _verificar_combustible(self):
        if self.combustible <= UMBRAL_COMBUSTIBLE and not self.reabasteciendo:
            self.reabasteciendo = True
            falta = max(0.0, 100.0 - self.combustible)
            self.tiempo_restante_recarga = int(max(5, falta / TASA_REABASTECIMIENTO))
            etiqueta = 'Recargando bateria' if self.propulsion == 'electrico' else 'Reabasteciendo combustible'
            self.escenario_activo = etiqueta
            self.velocidad_objetivo = 0
            self.en_movimiento = False

    def terminar_escenario(self):
        self._consolidar_coste_intervencion()
        self.finalizar_intervencion()

        self.en_camino = False
        self.en_escena = False
        self.tiempo_restante_viaje = 0
        self.tiempo_restante_escena = 0
        self.reabasteciendo = False

        self.consumo_factor = 1.0
        self.temp_factor = 1.0
        self.desgaste_factor = 1.0
        self.aceleracion_max = 5
        self.comportamiento_escena = 'estacionario'
        self.velocidad_escena = None
        self.perfil_velocidad = None

        self.escenario_activo = self.ESTADO_BASE
        self.velocidad_objetivo = self.VELOCIDAD_CRUCERO
        self.en_movimiento = True
        self.duracion_escenario = float('inf')
        self.tiempo_escenario_inicio = datetime.now()
        self.tiempo_escenario_sim = 0
        self.distancia_recorrida = 0

        self._iniciar_ruta_patrulla()

        return True

    def aplicar_escenario(self, tipo_escenario, duracion_minutos=30, intensidad=0.5,
                          velocidad_objetivo=None, nombre_personalizado=None, modificadores=None,
                          ruta=None, **kwargs):

        if self._esta_en_intervencion():
            self._consolidar_coste_intervencion()
            self.finalizar_intervencion()

        self._segundos_facturados = 0.0
        self.coste_intervencion_eur = self.coste_activacion

        self.escenario_activo = nombre_personalizado or tipo_escenario
        self.tiempo_escenario_inicio = datetime.now()
        self.duracion_escenario = duracion_minutos * 60
        self.tiempo_escenario_sim = 0
        self.distancia_recorrida = 0
        self.en_movimiento = True

        if ruta and len(ruta) >= 2:
            self.gps.establecer_ruta(ruta)
            self.version_ruta += 1

        self._aplicar_modificadores(modificadores, tipo_escenario, intensidad)
        self._configurar_velocidad_objetivo(tipo_escenario, intensidad, velocidad_objetivo)
        self._configurar_fases(tipo_escenario, duracion_minutos, modificadores)

        self.aplicar_modificadores_especificos(tipo_escenario, modificadores or {}, intensidad)

        return {
            'escenario': self.escenario_activo,
            'duracion': duracion_minutos,
            'velocidad_objetivo': round(self.velocidad_objetivo, 1),
            'intensidad': intensidad,
            'tiene_ruta': self.gps.ruta is not None,
            'coste_activacion_eur': round(self.coste_activacion, 2)
        }

    def _aplicar_modificadores(self, modificadores, tipo_escenario, intensidad):
        if isinstance(modificadores, dict):
            self.consumo_factor = float(modificadores.get('consumo_factor', 1.0))
            self.temp_factor = float(modificadores.get('temp_factor', 1.0))
            self.desgaste_factor = float(modificadores.get('desgaste_factor', 1.0))
            self.aceleracion_max = int(modificadores.get('aceleracion_max', 5))

            comportamiento = str(modificadores.get('comportamiento_escena', 'estacionario')).lower()
            self.comportamiento_escena = comportamiento if comportamiento in ['estacionario', 'movimiento'] else 'estacionario'

            if modificadores.get('velocidad_escena') is not None:
                self.velocidad_escena = float(modificadores['velocidad_escena'])

            if modificadores.get('perfil_velocidad'):
                self._aplicar_perfil_velocidad(modificadores['perfil_velocidad'])

        if not self.perfil_velocidad:
            self._generar_perfil_velocidad_base(tipo_escenario, intensidad)

    def _aplicar_perfil_velocidad(self, pv):
        def _a_float(v):
            return None if v is None else float(v)

        self.perfil_velocidad = {
            'vel_inicial': _a_float(pv.get('vel_inicial')),
            'vel_pico': _a_float(pv.get('vel_pico')),
            'vel_sostenida': _a_float(pv.get('vel_sostenida')),
            'vel_llegada': _a_float(pv.get('vel_llegada')),
            'variabilidad': _a_float(pv.get('variabilidad')),
            'notas': pv.get('notas')
        }

    def _generar_perfil_velocidad_base(self, tipo, intensidad):
        base = self.velocidad_objetivo

        bonus_pico = intensidad * 30
        bonus_sostenido = intensidad * 10
        variabilidad = min(1.0, 0.05 + intensidad * 0.4)

        self.perfil_velocidad = {
            'vel_pico': max(base, base + bonus_pico),
            'vel_sostenida': max(base, base + bonus_sostenido),
            'vel_inicial': max(0, base * (0.6 + intensidad * 0.3)),
            'vel_llegada': max(0, base - (10 * intensidad)),
            'variabilidad': variabilidad
        }

    def _configurar_velocidad_objetivo(self, tipo, intensidad, velocidad_objetivo):
        tope_unidad = self.velocidad_max_unidad

        if velocidad_objetivo is not None:
            self.velocidad_objetivo = min(tope_unidad, max(0, velocidad_objetivo))
        else:
            base = 30
            tope = min(150, tope_unidad)
            self.velocidad_objetivo = base + (intensidad * (tope - base))

            if intensidad >= 0.7:
                self.temperatura_motor += 10
                self.desgaste_frenos += 5

        if self.perfil_velocidad:
            pv_objetivo = self.perfil_velocidad.get('vel_pico') or self.perfil_velocidad.get('vel_sostenida')
            if pv_objetivo:
                self.velocidad_objetivo = min(tope_unidad, pv_objetivo)

    def _configurar_fases(self, tipo, duracion_minutos, modificadores):
        intensidad = modificadores.get('intensidad', 0.5) if isinstance(modificadores, dict) else 0.5

        if isinstance(modificadores, dict) and modificadores.get('tiempo_viaje'):
            self.tiempo_restante_viaje = int(modificadores['tiempo_viaje'])
        else:
            dur_sec = duracion_minutos * 60
            if intensidad >= 0.7:
                self.tiempo_restante_viaje = int(max(10, min(dur_sec - 5, dur_sec * 0.85)))
            else:
                ratio_viaje = 0.2 + (intensidad * 0.2)
                self.tiempo_restante_viaje = int(max(10, min(600, dur_sec * ratio_viaje)))

        self.tiempo_total_viaje = self.tiempo_restante_viaje

        if isinstance(modificadores, dict) and modificadores.get('tiempo_escena'):
            self.tiempo_restante_escena = int(modificadores['tiempo_escena'])
        else:
            self.tiempo_restante_escena = 0

        if intensidad >= 0.7:
            if self.comportamiento_escena == 'estacionario':
                self.comportamiento_escena = 'movimiento'
            if self.velocidad_escena is None and self.perfil_velocidad:
                self.velocidad_escena = (self.perfil_velocidad.get('vel_sostenida') or
                                         self.perfil_velocidad.get('vel_pico'))

        self.en_camino = True
        self.en_escena = False

    def _payload_costes(self):
        return {
            'tipo': self.TIPO,
            'propulsion': self.propulsion,
            'dotacion': self.dotacion,
            'coste_min_eur': round(self.coste_min, 2),
            'coste_activacion_eur': round(self.coste_activacion, 2),
            'coste_intervencion_eur': round(self.coste_intervencion_eur, 2),
            'coste_total_eur': round(self.coste_total_eur + self.coste_intervencion_eur, 2),
            'intervenciones_realizadas': self.intervenciones_realizadas
        }

    def obtener_estado(self):
        activo = self.escenario_activo or self.ESTADO_BASE
        distancia = round(self.distancia_recorrida, 2)
        transcurrido = int(self.tiempo_escenario_sim)

        duracion_seg = None
        if self.duracion_escenario != float('inf'):
            duracion_seg = int(self.duracion_escenario)
            transcurrido = min(transcurrido, duracion_seg)

        en_progreso = (self.en_camino or self.en_escena or
                       (str(activo).lower() != self.ESTADO_BASE.lower() and
                        duracion_seg is not None and transcurrido < duracion_seg))

        return {
            "timestamp": datetime.now().isoformat(),
            "id": self.id,
            "tipo": self.TIPO,
            "propulsion": self.propulsion,
            "combustible": round(self.combustible, 1),
            "temperatura_motor": round(self.temperatura_motor, 1),
            "km_totales": round(self.km_totales, 1),
            "nivel_aceite": round(self.nivel_aceite, 1),
            "desgaste_frenos": round(self.desgaste_frenos, 1),
            "desgaste_neumaticos": round(self.desgaste_neumaticos, 1),
            "velocidad": round(self.velocidad, 1),
            "velocidad_objetivo": round(self.velocidad_objetivo, 1),
            "en_movimiento": self.en_movimiento,
            "factor_entorno": round(float(getattr(self, 'factor_entorno', 1.0) or 1.0), 2),
            "gps": self.gps.obtener_coordenadas(),
            "escenario": {
                "activo": activo,
                "distancia_recorrida": distancia,
                "duracion": duracion_seg,
                "transcurrido": int(transcurrido),
                "en_progreso": en_progreso,
                "en_camino": bool(self.en_camino),
                "en_escena": bool(self.en_escena),
                "eta_seg": int(self.tiempo_restante_viaje) if self.en_camino else 0,
                "tiempo_restante_escena_seg": int(self.tiempo_restante_escena) if self.en_escena else 0
            },
            "costes": self._payload_costes(),
            "especializado": self.obtener_estado_especializado()
        }

    def obtener_estado_broadcast(self):
        activo = self.escenario_activo or self.ESTADO_BASE
        distancia = round(self.distancia_recorrida, 2)
        transcurrido = int(self.tiempo_escenario_sim)

        duracion_seg = None
        if self.duracion_escenario != float('inf'):
            duracion_seg = int(self.duracion_escenario)
            transcurrido = min(transcurrido, duracion_seg)

        en_progreso = (self.en_camino or self.en_escena or
                       (str(activo).lower() != self.ESTADO_BASE.lower() and
                        duracion_seg is not None and transcurrido < duracion_seg))

        return {
            "tipo": self.TIPO,
            "propulsion": self.propulsion,
            "combustible": round(self.combustible, 1),
            "temperatura_motor": round(self.temperatura_motor, 1),
            "km_totales": round(self.km_totales, 1),
            "nivel_aceite": round(self.nivel_aceite, 1),
            "desgaste_frenos": round(self.desgaste_frenos, 1),
            "desgaste_neumaticos": round(self.desgaste_neumaticos, 1),
            "velocidad": round(self.velocidad, 1),
            "factor_entorno": round(float(getattr(self, 'factor_entorno', 1.0) or 1.0), 2),
            "gps": self.gps.obtener_coordenadas_ligero(),
            "escenario": {
                "activo": activo,
                "distancia_recorrida": distancia,
                "duracion": duracion_seg,
                "transcurrido": int(transcurrido),
                "en_progreso": en_progreso,
                "en_camino": bool(self.en_camino),
                "en_escena": bool(self.en_escena),
                "eta_seg": int(self.tiempo_restante_viaje) if self.en_camino else 0,
                "tiempo_restante_escena_seg": int(self.tiempo_restante_escena) if self.en_escena else 0
            },
            "costes": self._payload_costes(),
            "especializado": self.obtener_estado_especializado()
        }
