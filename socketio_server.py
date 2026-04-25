from datetime import datetime
import logging
import threading
import time
import uuid

from flask_socketio import SocketIO, emit, join_room

from config import INTERVALO_ACTUALIZACION

logger = logging.getLogger(__name__)

socketio = None

conexiones = {}
lock_conexiones = threading.Lock()

SALA_VISUALIZADORES = "visualizadores"


def inicializar_socketio(app, fleet):
    global socketio

    socketio = SocketIO(
        app,
        cors_allowed_origins='*',
        async_mode='gevent',
        ping_timeout=60,
        ping_interval=25
    )

    registrar_manejadores(fleet)

    threading.Thread(
        target=bucle_difusion,
        args=(fleet,),
        daemon=True
    ).start()

    return socketio


def registrar_manejadores(fleet):

    @socketio.on('connect')
    def conectar():
        from flask import session, request

        sid = request.sid
        rol = session.get('rol', 'visualizador')
        usuario = session.get('usuario_nombre', session.get('usuario'))
        vehiculo_id = session.get('vehiculo_id')

        if rol == 'operador' and not vehiculo_id:
            vehiculo_id = fleet.asignar_vehiculo(session.get('usuario_id', sid))
            session['vehiculo_id'] = vehiculo_id

        with lock_conexiones:
            conexiones[sid] = {
                "sid": sid,
                "rol": rol,
                "usuario": usuario,
                "vehiculo_id": vehiculo_id
            }

        if rol == 'visualizador':
            join_room(SALA_VISUALIZADORES)

        if vehiculo_id:
            veh = fleet.obtener_vehiculo(vehiculo_id)
            if veh:
                emit('estado_inicial', {
                    "vehiculo_id": vehiculo_id,
                    "estado": veh.obtener_estado(),
                    "ruta": veh.gps.ruta
                })

    @socketio.on('disconnect')
    def desconectar():
        from flask import request

        sid = request.sid
        with lock_conexiones:
            conexiones.pop(sid, None)

    @socketio.on('control_incidente')
    def control_incidente(data):
        from flask import session

        vehiculo_id = session.get('vehiculo_id')
        veh = fleet.obtener_vehiculo(vehiculo_id) if vehiculo_id else None
        if not veh:
            return

        accion = data.get('accion')
        if accion == 'cerrar':
            veh.cerrar_incidente()
        elif accion == 'apoyo':
            tipo = data.get('tipo') or 'policia'
            evento = {
                "id": f"APOYO-{uuid.uuid4().hex[:6]}",
                "type": _mapear_tipo_apoyo(tipo),
                "severity": "high",
                "title": f"Apoyo solicitado ({tipo})",
                "description": "Solicitud de apoyo directo",
                "latitude": veh.gps.latitud,
                "longitude": veh.gps.longitud,
                "started_at": datetime.now().isoformat()
            }
            fleet.manejar_evento(evento)

        emit('estado_vehiculo', {
            "vehiculo_id": vehiculo_id,
            "estado": veh.obtener_estado(),
            "timestamp": datetime.now().isoformat()
        })



def _mapear_tipo_apoyo(tipo: str) -> str:
    mapa = {
        "bomberos": "fire",
        "ambulancia": "medical_emergency",
        "policia": "accident"
    }
    return mapa.get(tipo, "accident")



def bucle_difusion(fleet):
    while True:
        try:
            time.sleep(INTERVALO_ACTUALIZACION)
            if not socketio:
                continue

            with lock_conexiones:
                conexiones_actuales = dict(conexiones)

            if not conexiones_actuales:
                continue

            for sid, info in conexiones_actuales.items():
                if info.get('rol') != 'operador':
                    continue
                vehiculo_id = info.get('vehiculo_id')
                veh = fleet.obtener_vehiculo(vehiculo_id) if vehiculo_id else None
                if not veh:
                    continue

                payload = {
                    "vehiculo_id": vehiculo_id,
                    "estado": veh.obtener_estado_broadcast(),
                    "timestamp": datetime.now().isoformat(),
                    "rastro": veh.rastro
                }

                socketio.emit('estado_vehiculo', payload, to=sid)

            # Visualizadores: resumen completo cada 1s
            vehiculos = [v.obtener_estado_broadcast() for v in fleet.obtener_todos().values()]
            socketio.emit('actualizacion_flotas', {
                "vehiculos": vehiculos,
                "timestamp": datetime.now().isoformat()
            }, room=SALA_VISUALIZADORES)

        except Exception as exc:
            logger.warning("Error difusion: %s", exc)
            time.sleep(1)
