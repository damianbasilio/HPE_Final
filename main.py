from gevent import monkey
monkey.patch_all()

import gzip
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
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

try:
    _roads_iniciales = inventario.obtener_carreteras() or []
    _pois_iniciales = inventario.obtener_pois() or []
    _stations_iniciales = inventario.obtener_estaciones() or []
    logger.info(
        "[Inventory] cargado al arrancar: %d carreteras, %d POIs, %d estaciones",
        len(_roads_iniciales), len(_pois_iniciales), len(_stations_iniciales),
    )
    if not _roads_iniciales:
        logger.warning(
            "[Inventory] sin carreteras del inventory API: rutas seran lineas rectas entre landmarks",
        )
except Exception as exc:
    logger.error("[Inventory] no se pudo precargar inventory: %s", exc)

try:
    from osrm_client import warmup as _osrm_warmup
    threading.Thread(target=_osrm_warmup, daemon=True).start()
except Exception as exc:
    logger.error("[OSRM] no se pudo lanzar warmup: %s", exc)

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

_PRIORIDAD_POR_SEVERIDAD = {
    'critical': 'P1',
    'high': 'P2',
    'medium': 'P3',
    'low': 'P4',
}

def _ahora_iso_ms() -> str:
    ahora = datetime.now(timezone.utc)
    return ahora.strftime('%Y-%m-%dT%H:%M:%S.') + f"{ahora.microsecond // 1000:03d}Z"

def _derivar_state_availability(estado: dict) -> tuple:
    esc = estado.get('escenario') or {}
    activo = str(esc.get('activo') or '').lower()
    if 'reabasteci' in activo or 'recargando' in activo:
        return 'refueling', 'unavailable'
    if esc.get('en_camino'):
        return 'en_route', 'busy'
    if esc.get('en_escena'):
        return 'on_scene', 'busy'
    if activo in ('', 'patrulla') or not esc.get('en_progreso'):
        return 'patrol', 'available'
    return 'intervention', 'busy'

def construir_telemetria(veh) -> dict:
    estado = veh.obtener_estado()
    gps = estado.get('gps') or {}
    incidente = fleet._incidente_actual(veh.id)
    costes = estado.get('costes') or {}
    esc = estado.get('escenario') or {}

    state, availability = _derivar_state_availability(estado)
    priority = None
    if incidente:
        priority = _PRIORIDAD_POR_SEVERIDAD.get((incidente.get('severity') or '').lower())

    velocidad = float(estado.get('velocidad') or 0.0)
    heading = float(getattr(veh, 'heading', 0.0)) if velocidad > 0.5 else None

    payload = {
        "schema_version": "1.0.0",
        "message_type": "fleet_telemetry",
        "team_id": TEAM_ID,
        "sent_at": _ahora_iso_ms(),
        "vehicle": {
            "vehicle_id": str(veh.id),
            "vehicle_type": str(veh.TIPO),
            "unit_name": veh.metadatos.get('nombre') if veh.metadatos else None,
        },
        "telemetry": {
            "position": {
                "lat": float(gps.get('latitud') or 0.0),
                "lon": float(gps.get('longitud') or 0.0),
            },
            "speed": {
                "value": round(velocidad, 2),
                "unit": "kmh",
            },
            "heading": heading,
        },
        "operational_status": {
            "state": state,
            "availability": availability,
            "priority": priority,
        },
        "incident": ({
            "incident_id": incidente.get('incident_id'),
            "incident_type": incidente.get('incident_type'),
            "incident_status": incidente.get('incident_status'),
            "severity": incidente.get('severity'),
        } if incidente else None),
        "costs": ({
            "total_eur": round(float(costes.get('coste_total_eur') or 0.0), 2),
            "intervention_eur": round(float(costes.get('coste_intervencion_eur') or 0.0), 2),
            "interventions_done": int(costes.get('intervenciones_realizadas') or 0),
            "currency": "EUR",
        } if costes else None),
        "specialty_data": estado.get('especializado') or {},
        "metadata": {
            "trace_id": str(uuid.uuid4()),
            "producer": "digital-twin-backend",
            "propulsion": veh.propulsion,
            "scenario": esc.get('activo'),
            "eta_seg": esc.get('eta_seg'),
            "fuel_pct": estado.get('combustible'),
            "engine_temp_c": estado.get('temperatura_motor'),
            "factor_entorno": estado.get('factor_entorno'),
        },
    }
    return payload

bus.iniciar(on_event=fleet.manejar_evento)

threading.Thread(target=bucle_flotas, daemon=True).start()
threading.Thread(target=bucle_telemetria, daemon=True).start()

@app.before_request
def proteger_vistas():
    public_endpoints = {
        'health', 'health_kafka', 'health_kafka_probe', 'health_inventory',
        'health_fleet', 'health_osrm', 'health_events_trace',
        'openapi', 'vehicles_status', 'list_vehicles', 'get_vehicle',
        'list_incidents', 'list_simulations', 'sim_replay', 'sim_state', 'sim_pause',
        'sim_speed', 'ask_fleet', 'weather_stations', 'weather_reading', 'index',
        'login', 'static', 'ciudadano', 'api_contexto',
        'internal_vehicles', 'internal_incidents',
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

def _map_status_vehicle(estado: dict) -> str:
    esc = estado.get('escenario') or {}
    activo = str(esc.get('activo') or '').lower()
    if 'reabasteci' in activo or 'recargando' in activo:
        return 'refueling'
    if esc.get('en_camino'):
        return 'en_route'
    if esc.get('en_escena'):
        return 'on_scene'
    if activo == 'patrulla' or activo == '' or not esc.get('en_progreso'):
        return 'patrol'
    return 'intervention'

def _map_vehicle(estado: dict) -> dict:
    gps = estado.get('gps') or {}
    costes = estado.get('costes') or {}
    veh_id = estado.get('id')
    veh = fleet.obtener_vehiculo(veh_id) if veh_id else None
    callsign = estado.get('nombre') or (veh.metadatos.get('nombre') if veh else None)
    last_updated = estado.get('timestamp') or time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    incidente = estado.get('incidente')

    return {
        "id": str(veh_id),
        "type": str(estado.get('tipo') or 'unknown'),
        "status": _map_status_vehicle(estado),
        "latitude": float(gps.get('latitud') or 0.0),
        "longitude": float(gps.get('longitud') or 0.0),
        "fuel_level": float(estado.get('combustible') or 0.0),
        "callsign": callsign,
        "speed_kmh": float(estado.get('velocidad') or 0.0),
        "last_updated": last_updated,
        "metadata": {
            "propulsion": estado.get('propulsion'),
            "scenario": (estado.get('escenario') or {}).get('activo'),
            "eta_seg": (estado.get('escenario') or {}).get('eta_seg'),
            "factor_entorno": estado.get('factor_entorno'),
            "cost_total_eur": costes.get('coste_total_eur'),
            "cost_intervention_eur": costes.get('coste_intervencion_eur'),
            "interventions_done": costes.get('intervenciones_realizadas'),
            "crew_size": costes.get('dotacion'),
            "incident_id": (incidente or {}).get('incident_id') if incidente else None,
            "incident_type": (incidente or {}).get('incident_type') if incidente else None,
        },
    }

@app.route('/health')
def health():
    return jsonify({"status": "ok"})

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

@app.route('/health/osrm')
def health_osrm():
    from config import ARUBA_LANDMARKS as _LM, OSRM_PROFILE as _PROF
    from osrm_client import osrm_estado as _estado, osrm_route as _route
    if len(_LM) < 2:
        return jsonify({"ok": False, "error": "no hay landmarks"}), 500
    a = (_LM[0][1], _LM[0][2])
    b = (_LM[1][1], _LM[1][2])
    ruta = None
    error = None
    try:
        ruta = _route(a, b)
    except Exception as exc:
        error = repr(exc)
    estado = _estado()
    return jsonify({
        "profile": _PROF,
        **estado,
        "origen": {"nombre": _LM[0][0], "lat": a[0], "lon": a[1]},
        "destino": {"nombre": _LM[1][0], "lat": b[0], "lon": b[1]},
        "puntos_ruta": len(ruta) if ruta else 0,
        "muestra": ruta[:5] if ruta else None,
        "ok": ruta is not None and len(ruta) >= 2,
        "error": error,
    })

@app.route('/health/inventory')
def health_inventory():
    try:
        roads = inventario.obtener_carreteras() or []
        pois = inventario.obtener_pois() or []
        stations = inventario.obtener_estaciones() or []
        muestra_road = roads[0] if roads else None
        return jsonify({
            "base_url": inventario.base_url,
            "roads": len(roads),
            "pois": len(pois),
            "stations": len(stations),
            "muestra_road": muestra_road,
            "ok": True,
        })
    except Exception as exc:
        return jsonify({
            "base_url": inventario.base_url,
            "ok": False,
            "error": repr(exc),
        }), 500

@app.route('/health/events_trace')
def health_events_trace():
    limite = int(request.args.get('limit', '50'))
    return jsonify({
        "trace": fleet.traza_eventos(limite=limite),
        "incidentes_total": len(fleet.listado_incidentes()),
        "asignaciones": dict(fleet.asignaciones),
    })

@app.route('/health/fleet')
def health_fleet():
    vehiculos = fleet.estado_resumen()
    incidentes = fleet.listado_incidentes()
    return jsonify({
        "vehiculos": [{
            "id": v.get('id'),
            "tipo": v.get('tipo'),
            "nombre": v.get('nombre'),
            "lat": (v.get('gps') or {}).get('latitud'),
            "lon": (v.get('gps') or {}).get('longitud'),
            "escenario": (v.get('escenario') or {}).get('activo'),
            "en_camino": (v.get('escenario') or {}).get('en_camino'),
            "en_escena": (v.get('escenario') or {}).get('en_escena'),
            "incidente": (v.get('incidente') or {}).get('incident_id') if v.get('incidente') else None,
        } for v in vehiculos],
        "incidentes": [{
            "id": i.get('incident_id'),
            "type": i.get('incident_type'),
            "status": i.get('status'),
            "lat": i.get('lat'),
            "lon": i.get('lon'),
            "unidad": i.get('unidad_id'),
        } for i in incidentes],
        "asignaciones": dict(fleet.asignaciones),
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
    return Response(contenido, mimetype='text/plain; charset=utf-8')

@app.route('/vehicles/status')
def vehicles_status():
    vehiculos = fleet.estado_resumen()
    by_type: dict = {}
    by_status: dict = {}
    for v in vehiculos:
        tipo = str(v.get('tipo') or 'unknown')
        status = _map_status_vehicle(v)
        by_type[tipo] = by_type.get(tipo, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
    return jsonify({
        "total": len(vehiculos),
        "by_type": by_type,
        "by_status": by_status,
    })

@app.route('/vehicles')
def list_vehicles():
    tipo = request.args.get('type')
    vehiculos = fleet.estado_resumen()
    if tipo:
        vehiculos = [v for v in vehiculos if v.get('tipo') == tipo]
    return jsonify({"vehicles": [_map_vehicle(v) for v in vehiculos]})

@app.route('/vehicles/<vehicle_id>')
def get_vehicle(vehicle_id):
    veh = fleet.obtener_vehiculo(vehicle_id)
    if not veh:
        return jsonify({
            "detail": [{
                "loc": ["path", "vehicle_id"],
                "msg": "Vehicle not found",
                "type": "value_error.not_found",
            }]
        }), 404
    estado = veh.obtener_estado()
    estado['nombre'] = veh.metadatos.get('nombre', veh.id)
    estado['incidente'] = fleet._incidente_actual(veh.id)
    return jsonify(_map_vehicle(estado))

@app.route('/incidents')
def list_incidents():
    incidentes = fleet.listado_incidentes()
    estado = request.args.get('status')
    if estado:
        incidentes = [i for i in incidentes if i.get('status') == estado]
    return jsonify({"total": len(incidentes), "incidents": incidentes})

@app.route('/_internal/vehicles')
def internal_vehicles():
    return jsonify({"vehicles": fleet.estado_resumen()})

@app.route('/_internal/incidents')
def internal_incidents():
    return jsonify({"incidents": fleet.listado_incidentes()})

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

def _map_station(raw: dict) -> dict:
    if not isinstance(raw, dict):
        return {"id": "", "name": "", "latitude": 0.0, "longitude": 0.0}
    sid = raw.get('id') or raw.get('station_id') or raw.get('code') or ''
    name = raw.get('name') or raw.get('label') or sid
    lat = raw.get('latitude')
    if lat is None:
        lat = raw.get('lat')
    if lat is None:
        loc = raw.get('location') or {}
        if isinstance(loc, dict):
            lat = loc.get('lat') or loc.get('latitude')
    lon = raw.get('longitude')
    if lon is None:
        lon = raw.get('lon') or raw.get('lng')
    if lon is None:
        loc = raw.get('location') or {}
        if isinstance(loc, dict):
            lon = loc.get('lon') or loc.get('lng') or loc.get('longitude')
    return {
        "id": str(sid),
        "name": str(name),
        "latitude": float(lat or 0.0),
        "longitude": float(lon or 0.0),
    }

def _map_reading(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    temp = (raw.get('temperature') if raw.get('temperature') is not None
            else raw.get('temperature_c') if raw.get('temperature_c') is not None
            else raw.get('temp'))
    hum = (raw.get('humidity') if raw.get('humidity') is not None
           else raw.get('humidity_pct') if raw.get('humidity_pct') is not None
           else raw.get('rh'))
    wind = (raw.get('wind_speed') if raw.get('wind_speed') is not None
            else raw.get('wind_speed_kmh') if raw.get('wind_speed_kmh') is not None
            else raw.get('wind') if raw.get('wind') is not None
            else raw.get('wind_kmh'))
    ts = raw.get('timestamp') or raw.get('time') or raw.get('observed_at') or raw.get('ts')
    if not ts:
        ts = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    return {
        "temperature": float(temp or 0.0),
        "humidity": float(hum or 0.0),
        "wind_speed": float(wind or 0.0),
        "timestamp": str(ts),
    }

@app.route('/weather-stations')
def weather_stations():
    estaciones = inventario.obtener_estaciones() or []
    return jsonify({"stations": [_map_station(s) for s in estaciones]})

@app.route('/weather-stations/<station_id>/reading')
def weather_reading(station_id):
    lectura = bus.lectura_estacion(station_id)
    if not lectura:
        return jsonify({
            "detail": [{
                "loc": ["path", "station_id"],
                "msg": "No reading available for station",
                "type": "value_error.not_found",
            }]
        }), 404
    return jsonify(_map_reading(lectura))

@app.route('/ask')
def ask_fleet():
    pregunta = request.args.get('q', '').strip()
    if len(pregunta) < 3:
        return jsonify({
            "detail": [{
                "loc": ["query", "q"],
                "msg": "ensure this value has at least 3 characters",
                "type": "value_error.any_str.min_length",
                "input": pregunta,
                "ctx": {"limit_value": 3},
            }]
        }), 422

    contexto = construir_contexto_chat()
    rol = session.get('rol', 'ciudadano') if session.get('autenticado') else 'ciudadano'

    try:
        respuesta = responder_chat(pregunta, rol, contexto)
        return jsonify({
            "answer": respuesta,
            "confidence": None,
            "data": contexto,
        })
    except Exception as exc:
        logger.error("Chat error: %s", exc)
        return jsonify({
            "answer": "Asistente no disponible en este momento.",
            "confidence": 0.0,
            "data": None,
        }), 500

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
