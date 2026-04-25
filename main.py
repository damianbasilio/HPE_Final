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
    TIEMPO_SESION
)
from entorno import configurar_bus, obtener_contexto_entorno_completo
from flota import FleetManager
from ia import responder_chat
from inventario_aruba import InventarioAruba
from kafka_bus import KafkaBus
from socketio_server import inicializar_socketio

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
    if respuesta.content_type and any(ct in respuesta.content_type for ct in ['text/', 'application/json', 'application/javascript']):
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


def obtener_factor_entorno() -> float:
    contexto = obtener_contexto_entorno_completo() or {}
    clima = contexto.get('clima', {})
    return clima.get('condicion', {}).get('factor_velocidad', 1.0)


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


def construir_telemetria(veh):
    gps = veh.gps.obtener_coordenadas_ligero()
    incidente = veh.incidente
    return {
        "schema_version": "1.0.0",
        "message_type": "fleet_telemetry",
        "team_id": TEAM_ID,
        "sent_at": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        "vehicle": {
            "vehicle_id": veh.id,
            "vehicle_type": veh.tipo,
            "unit_name": veh.nombre
        },
        "telemetry": {
            "position": {"lat": gps.get('latitud'), "lon": gps.get('longitud')},
            "speed": {"value": veh.velocidad, "unit": "kmh"},
            "heading": None
        },
        "operational_status": {
            "state": veh.estado_servicio,
            "availability": veh.disponibilidad,
            "priority": veh.prioridad
        },
        "incident": {
            "incident_id": incidente.get('incident_id'),
            "incident_type": incidente.get('incident_type'),
            "incident_status": incidente.get('incident_status')
        } if incidente else None,
        "costs": {
            "estimated_operational_cost": round(veh.costos.get('acumulado', 0.0), 2),
            "currency": "EUR"
        },
        "specialty_data": veh.especialidad,
        "metadata": {
            "trace_id": str(uuid.uuid4()),
            "producer": "digital-twin-backend"
        }
    }


bus.iniciar(on_event=fleet.manejar_evento)

threading.Thread(target=bucle_flotas, daemon=True).start()
threading.Thread(target=bucle_telemetria, daemon=True).start()


@app.before_request
def proteger_vistas():
    public_endpoints = {
        'health', 'openapi', 'vehicles_status', 'list_vehicles', 'get_vehicle',
        'ask_fleet', 'weather_stations', 'weather_reading', 'index', 'landing',
        'login', 'static', 'ciudadano'
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
            return render_template('login.html', error='Debe ingresar usuario y contraseña')

        datos_usuario = autenticar_usuario(usuario, contrasena)
        if datos_usuario:
            registrar_sesion(datos_usuario)
            session['rol'] = datos_usuario.get('rol', 'operador')
            session.modified = True

            if datos_usuario.get('rol') == 'operador':
                veh_id = fleet.asignar_vehiculo(datos_usuario['usuario'], datos_usuario.get('tipo_vehiculo'))
                session['vehiculo_id'] = veh_id
                return redirect(url_for('operador'))

            return redirect(url_for('visualizador'))

        return render_template('login.html', error='Usuario o contraseña incorrectos')

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
    veh_id = session.get('vehiculo_id')
    veh = fleet.obtener_vehiculo(veh_id) if veh_id else None
    return render_template('simulador.html', usuario=usuario, vehiculo=veh.obtener_estado() if veh else {})


@app.route('/visualizador')
def visualizador():
    usuario = obtener_usuario_actual()
    return render_template('comando.html', usuario=usuario)


@app.route('/ciudadano')
def ciudadano():
    return render_template('ciudadano.html')


@app.route('/health')
def health():
    return jsonify({"status": "ok"})


@app.route('/openapi.yaml')
def openapi():
    ruta = os.path.join(os.path.dirname(__file__), 'apis', 'aruba-island-inventory.json')
    with open(ruta, 'r', encoding='utf-8') as f:
        contenido = f.read()
    return Response(contenido, mimetype='text/plain')


@app.route('/vehicles/status')
def vehicles_status():
    vehiculos = [v.obtener_estado() for v in fleet.obtener_todos().values()]
    activos = sum(1 for v in vehiculos if v.get('estado_servicio') != 'disponible')
    return jsonify({
        "total": len(vehiculos),
        "activos": activos,
        "vehiculos": vehiculos
    })


@app.route('/vehicles')
def list_vehicles():
    tipo = request.args.get('type')
    vehiculos = [v.obtener_estado() for v in fleet.obtener_todos().values()]
    if tipo:
        vehiculos = [v for v in vehiculos if v.get('tipo') == tipo]
    return jsonify({"vehicles": vehiculos, "total": len(vehiculos)})


@app.route('/vehicles/<vehicle_id>')
def get_vehicle(vehicle_id):
    veh = fleet.obtener_vehiculo(vehicle_id)
    if not veh:
        return jsonify({"error": "Vehiculo no encontrado"}), 404
    return jsonify(veh.obtener_estado())


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
    vehiculos = [v.obtener_estado() for v in fleet.obtener_todos().values()]
    flota_resumen = {
        "total": len(vehiculos),
        "por_tipo": {
            "policia": sum(1 for v in vehiculos if v.get('tipo') == 'policia'),
            "ambulancia": sum(1 for v in vehiculos if v.get('tipo') == 'ambulancia'),
            "bomberos": sum(1 for v in vehiculos if v.get('tipo') == 'bomberos'),
            "dron": sum(1 for v in vehiculos if v.get('tipo') == 'dron')
        }
    }

    return {
        "clima": contexto_entorno.get('clima'),
        "eventos": contexto_entorno.get('eventos', []),
        "alertas": contexto_entorno.get('alertas_entorno', []),
        "flota": flota_resumen
    }


if __name__ == '__main__':
    socketio.run(app, debug=DEPURACION_FLASK, host=HOST_SERVIDOR, port=PUERTO_SERVIDOR)
