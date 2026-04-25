from gevent import monkey
monkey.patch_all()

import gzip
import logging
import os
import threading
import time
import uuid
from io import BytesIO

from flask import Flask, jsonify, redirect, render_template, request, session, url_for, Response
from flask_session import Session

from auth import autenticar_usuario, cerrar_sesion, obtener_usuario_actual, registrar_sesion
from config import (
    CACHE_ESTATICOS,
    CLAVE_FLASK,
    COOKIE_HTTPONLY,
    COOKIE_SAMESITE,
    DEPURACION_FLASK,
    HOST_SERVIDOR,
    INTERVALO_TELEMETRIA,
    PUERTO_SERVIDOR,
    TEAM_ID,
    TIEMPO_SESION,
)
from entorno import configurar_bus, obtener_contexto_entorno_completo
from flota import FleetManager
from ia import responder_chat
from inventario_aruba import InventarioAruba
from kafka_bus import KafkaBus
from socketio_server import inicializar_socketio
from simulaciones import GestorSimulaciones

nivel_log = logging.DEBUG if os.getenv('FLASK_DEBUG', 'false').lower() == 'true' else logging.INFO
logging.basicConfig(
    level=nivel_log,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = CLAVE_FLASK
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_FILE_DIR'] = os.path.join(os.path.dirname(__file__), 'flask_session')
app.config['SESSION_PERMANENT'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = TIEMPO_SESION
app.config['SESSION_COOKIE_HTTPONLY'] = COOKIE_HTTPONLY
app.config['SESSION_COOKIE_SAMESITE'] = COOKIE_SAMESITE
Session(app)

@app.after_request
def comprimir_respuesta(respuesta):
    if respuesta.content_type and any(ct in respuesta.content_type for ct in
                                      ['text/', 'application/json', 'application/javascript']):
        if 'gzip' in request.headers.get('Accept-Encoding', ''):
            if respuesta.content_length is None or respuesta.content_length > 500:
                try:
                    datos = respuesta.get_data()
                    buffer_gzip = BytesIO()
                    with gzip.GzipFile(mode='wb', fileobj=buffer_gzip, compresslevel=6) as f:
                        f.write(datos)
                    respuesta.set_data(buffer_gzip.getvalue())
                    respuesta.headers['Content-Encoding'] = 'gzip'
                    respuesta.headers['Vary'] = 'Accept-Encoding'
                    respuesta.headers['Content-Length'] = len(respuesta.get_data())
                except Exception:
                    pass

    if request.path.startswith('/static/'):
        respuesta.headers['Cache-Control'] = f'public, max-age={CACHE_ESTATICOS}'
    else:
        respuesta.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'

    return respuesta

fleet = FleetManager()
inventario = InventarioAruba()
bus = KafkaBus()
configurar_bus(bus)

socketio = inicializar_socketio(app, fleet)
gestor_simulaciones = GestorSimulaciones(fleet, bus)

def obtener_factor_entorno() -> float:
    contexto = obtener_contexto_entorno_completo() or {}
    clima = contexto.get('clima', {}) or {}
    return float(clima.get('condicion', {}).get('factor_velocidad', 1.0) or 1.0)

def bucle_flotas():
    fleet.loop_actualizacion(obtener_factor_entorno)

def bucle_telemetria():
    while True:
        try:
            time.sleep(INTERVALO_TELEMETRIA)
            for veh in fleet.obtener_todos().values():
                payload = construir_telemetria(veh)
                bus.publicar_telemetria(payload)
        except Exception as exc:
            logger.warning("Telemetria fallo: %s", exc)
            time.sleep(2)

def construir_telemetria(veh) -> dict:
    estado = veh.obtener_estado()
    gps = estado.get('gps') or {}
    incidente = fleet._incidente_actual(veh.id)  
    costes = estado.get('costes') or {}

    payload = {
        "schema_version": "1.0.0",
        "message_type": "fleet_telemetry",
        "team_id": TEAM_ID,
        "sent_at": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        "vehicle": {
            "vehicle_id": veh.id,
            "vehicle_type": veh.TIPO,
            "unit_name": veh.metadatos.get('nombre', veh.id),
            "propulsion": veh.propulsion,
        },
        "telemetry": {
            "position": {"lat": gps.get('latitud'), "lon": gps.get('longitud')},
            "speed": {"value": estado.get('velocidad'), "unit": "kmh"},
            "fuel_pct": estado.get('combustible'),
            "engine_temp_c": estado.get('temperatura_motor'),
            "factor_entorno": estado.get('factor_entorno'),
        },
        "operational_status": {
            "scenario": estado.get('escenario', {}).get('activo'),
            "en_route": estado.get('escenario', {}).get('en_camino'),
            "on_scene": estado.get('escenario', {}).get('en_escena'),
            "eta_seg": estado.get('escenario', {}).get('eta_seg'),
        },
        "incident": {
            "incident_id": incidente.get('incident_id'),
            "incident_type": incidente.get('incident_type'),
            "incident_status": incidente.get('incident_status'),
            "severity": incidente.get('severity'),
        } if incidente else None,
        "costs": {
            "coste_total_eur": costes.get('coste_total_eur', 0.0),
            "coste_intervencion_eur": costes.get('coste_intervencion_eur', 0.0),
            "intervenciones_realizadas": costes.get('intervenciones_realizadas', 0),
            "currency": "EUR",
        },
        "specialty_data": estado.get('especializado', {}),
        "metadata": {
            "trace_id": str(uuid.uuid4()),
            "producer": "digital-twin-backend",
        },
    }
    return payload

bus.iniciar(on_event=fleet.manejar_evento)

threading.Thread(target=bucle_flotas, daemon=True).start()
threading.Thread(target=bucle_telemetria, daemon=True).start()

@app.before_request
def proteger_vistas():
    public_endpoints = {
        'health', 'health_kafka', 'health_kafka_probe', 'openapi',
        'vehicles_status', 'list_vehicles', 'get_vehicle',
        'list_incidents', 'list_simulations', 'sim_replay', 'sim_state', 'sim_pause',
        'sim_speed', 'ask_fleet', 'weather_stations', 'weather_reading', 'index',
        'login', 'static', 'ciudadano', 'api_contexto'
    }
    if request.endpoint in public_endpoints or request.path.startswith('/static/'):
        return

    if not session.get('autenticado'):
        return redirect(url_for('login'))

@app.route('/')
def index():
    return render_template('landing.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usuario = request.form.get('usuario', '').strip().lower()
        contrasena = request.form.get('contrasena', '').strip()

        if not usuario or not contrasena:
            return render_template('login.html', error='Debe ingresar usuario y contrasena')

        datos_usuario = autenticar_usuario(usuario, contrasena)
        if datos_usuario:
            registrar_sesion(datos_usuario)
            rol = datos_usuario.get('rol', 'operador')
            session['rol'] = rol
            session.modified = True

            if rol == 'operador':
                return redirect(url_for('operador'))
            return redirect(url_for('visualizador'))

        return render_template('login.html', error='Usuario o contrasena incorrectos')

    if session.get('autenticado'):
        if session.get('rol') == 'operador':
            return redirect(url_for('operador'))
        return redirect(url_for('visualizador'))

    return render_template('login.html')

@app.route('/logout')
def logout():
    cerrar_sesion()
    return redirect(url_for('login'))

@app.route('/operador')
def operador():
    usuario = obtener_usuario_actual()
    return render_template('simulador.html', usuario=usuario)

@app.route('/visualizador')
def visualizador():
    usuario = obtener_usuario_actual()
    return render_template('comando.html', usuario=usuario)

@app.route('/ciudadano')
def ciudadano():
    return render_template('ciudadano.html')

@app.route('/health')
def health():
    return jsonify({
        "status": "ok",
        "team_id": TEAM_ID,
        "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        "fleet_size": len(fleet.obtener_todos()),
        "active_simulations": len(gestor_simulaciones.listar()),
    })

@app.route('/health/kafka')
def health_kafka():
    weather_cache = list(getattr(bus, '_weather_cache', []))
    events_cache = list(getattr(bus, '_events_cache', []))
    return jsonify({
        "disponible": getattr(bus, 'disponible', False),
        "producer": bus._producer is not None,
        "consumer_weather": bus._weather_consumer is not None,
        "consumer_events": bus._events_consumer is not None,
        "weather_recibidos": len(weather_cache),
        "events_recibidos": len(events_cache),
        "ultimo_weather": weather_cache[-1] if weather_cache else None,
        "ultimos_eventos": events_cache[-5:] if events_cache else [],
        "stations_distintas": len(getattr(bus, '_weather_by_station', {}) or {}),
    })

@app.route('/health/kafka/probe')
def health_kafka_probe():
    import json as _json
    from kafka import KafkaConsumer as _KC
    from kafka_bus import _kafka_common_config as _cfg
    from config import KAFKA_TOPIC_EVENTOS as _TOPIC

    timeout_ms = int(request.args.get('timeout_ms', '5000'))
    desde = request.args.get('from', 'earliest')
    consumer = None
    mensajes = []
    error = None
    try:
        consumer = _KC(
            _TOPIC,
            group_id=None,
            auto_offset_reset=desde,
            enable_auto_commit=False,
            value_deserializer=lambda v: _json.loads(v.decode('utf-8')),
            session_timeout_ms=10000,
            request_timeout_ms=20000,
            api_version_auto_timeout_ms=10000,
            **_cfg(),
        )
        polled = consumer.poll(timeout_ms=timeout_ms, max_records=20)
        for tp_records in polled.values():
            for rec in tp_records:
                mensajes.append({
                    "offset": rec.offset,
                    "partition": rec.partition,
                    "key": rec.key.decode() if isinstance(rec.key, (bytes, bytearray)) else rec.key,
                    "value": rec.value,
                })
    except Exception as exc:
        error = repr(exc)
    finally:
        try:
            if consumer is not None:
                consumer.close()
        except Exception:
            pass

    return jsonify({
        "topic": _TOPIC,
        "auto_offset_reset": desde,
        "timeout_ms": timeout_ms,
        "mensajes_recibidos": len(mensajes),
        "muestras": mensajes,
        "error": error,
    })

@app.route('/openapi.yaml')
def openapi():
    ruta = os.path.join(os.path.dirname(__file__), 'apis', 'team-api.yaml')
    if not os.path.exists(ruta):
        return Response("OpenAPI spec no encontrada", status=404, mimetype='text/plain')
    with open(ruta, 'r', encoding='utf-8') as f:
        contenido = f.read()
    return Response(contenido, mimetype='application/yaml')

@app.route('/vehicles/status')
def vehicles_status():
    vehiculos = fleet.estado_resumen()
    activos = sum(1 for v in vehiculos
                  if v.get('escenario', {}).get('en_progreso'))
    coste_total = sum(v.get('costes', {}).get('coste_total_eur', 0.0)
                      for v in vehiculos)
    por_tipo = {}
    for v in vehiculos:
        por_tipo[v['tipo']] = por_tipo.get(v['tipo'], 0) + 1

    return jsonify({
        "total": len(vehiculos),
        "activos": activos,
        "coste_total_eur": round(coste_total, 2),
        "por_tipo": por_tipo,
        "vehiculos": vehiculos,
    })

@app.route('/vehicles')
def list_vehicles():
    tipo = request.args.get('type')
    vehiculos = fleet.estado_resumen()
    if tipo:
        vehiculos = [v for v in vehiculos if v.get('tipo') == tipo]
    return jsonify({"vehicles": vehiculos, "total": len(vehiculos)})

@app.route('/vehicles/<vehicle_id>')
def get_vehicle(vehicle_id):
    veh = fleet.obtener_vehiculo(vehicle_id)
    if not veh:
        return jsonify({"error": "Vehiculo no encontrado"}), 404
    estado = veh.obtener_estado()
    estado['nombre'] = veh.metadatos.get('nombre', veh.id)
    estado['incidente'] = fleet._incidente_actual(veh.id)  
    return jsonify(estado)

@app.route('/incidents')
def list_incidents():
    incidentes = fleet.listado_incidentes()
    estado = request.args.get('status')
    if estado:
        incidentes = [i for i in incidentes if i.get('status') == estado]
    return jsonify({"total": len(incidentes), "incidents": incidentes})

@app.route('/simulations')
def list_simulations():
    return jsonify({"simulations": gestor_simulaciones.listar()})

@app.route('/simulations/replay', methods=['POST'])
def sim_replay():
    payload = request.get_json(silent=True) or {}
    started_at = payload.get('started_at')
    if not started_at:
        return jsonify({"error": "Falta 'started_at' (ISO 8601)"}), 400
    speed = float(payload.get('speed', 5))
    topics = payload.get('topics') or ['aruba.weather', 'aruba.events']

    try:
        sim = gestor_simulaciones.iniciar_replay(started_at, speed=speed, topics=topics)
        return jsonify(sim)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

@app.route('/simulations/<sim_id>/state')
def sim_state(sim_id):
    sim = gestor_simulaciones.estado(sim_id)
    if not sim:
        return jsonify({"error": "Simulacion no encontrada"}), 404
    return jsonify(sim)

@app.route('/simulations/<sim_id>/pause', methods=['POST'])
def sim_pause(sim_id):
    sim = gestor_simulaciones.alternar_pausa(sim_id)
    if not sim:
        return jsonify({"error": "Simulacion no encontrada"}), 404
    return jsonify(sim)

@app.route('/simulations/<sim_id>/speed', methods=['POST'])
def sim_speed(sim_id):
    payload = request.get_json(silent=True) or {}
    if 'speed' not in payload:
        return jsonify({"error": "Falta 'speed'"}), 400
    sim = gestor_simulaciones.set_velocidad(sim_id, float(payload['speed']))
    if not sim:
        return jsonify({"error": "Simulacion no encontrada"}), 404
    return jsonify(sim)

@app.route('/weather-stations')
def weather_stations():
    estaciones = inventario.obtener_estaciones()
    return jsonify({"stations": estaciones, "total": len(estaciones)})

@app.route('/weather-stations/<station_id>/reading')
def weather_reading(station_id):
    lectura = bus.lectura_estacion(station_id)
    if not lectura:
        return jsonify({"error": "Sin lectura disponible"}), 404
    return jsonify(lectura)

@app.route('/ask')
def ask_fleet():
    pregunta = request.args.get('q', '').strip()
    if len(pregunta) < 3:
        return jsonify({"error": "Pregunta demasiado corta"}), 400

    contexto = construir_contexto_chat()
    rol = session.get('rol', 'ciudadano') if session.get('autenticado') else 'ciudadano'

    try:
        respuesta = responder_chat(pregunta, rol, contexto)
        return jsonify({"answer": respuesta, "context": contexto})
    except Exception as exc:
        logger.error("Chat error: %s", exc)
        return jsonify({"error": "Error en el asistente"}), 500

@app.route('/api/context')
def api_contexto():
    return jsonify(construir_contexto_chat())

def construir_contexto_chat() -> dict:
    contexto_entorno = obtener_contexto_entorno_completo() or {}
    vehiculos = fleet.estado_resumen()
    flota_resumen = {
        "total": len(vehiculos),
        "por_tipo": {},
        "activos": sum(1 for v in vehiculos if v.get('escenario', {}).get('en_progreso')),
        "coste_total_eur": round(sum(
            v.get('costes', {}).get('coste_total_eur', 0.0) for v in vehiculos), 2),
    }
    for v in vehiculos:
        flota_resumen["por_tipo"][v['tipo']] = flota_resumen["por_tipo"].get(v['tipo'], 0) + 1

    return {
        "clima": contexto_entorno.get('clima'),
        "eventos": contexto_entorno.get('eventos', []),
        "alertas": contexto_entorno.get('alertas_entorno', []),
        "flota": flota_resumen,
        "incidentes_activos": [i for i in fleet.listado_incidentes()
                                if i.get('status') in ('en_route', 'on_scene', 'assigned')],
    }

if __name__ == '__main__':
    socketio.run(app, debug=DEPURACION_FLASK, host=HOST_SERVIDOR, port=PUERTO_SERVIDOR,
                 allow_unsafe_werkzeug=True)
