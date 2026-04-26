

from datetime import datetime
import logging
import threading
import time
import uuid

from flask_socketio import SocketIO, emit, join_room

from config import CENTRO_ARUBA, INTERVALO_ACTUALIZACION

logger = logging.getLogger(__name__)

socketio: "SocketIO" = None  

conexiones = {}
lock_conexiones = threading.Lock()

SALA_OPERADORES = "operadores"
SALA_VISUALIZADORES = "visualizadores"
SALA_TODOS = "flota"

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
        rol = session.get('rol') or session.get('usuario_rol') or 'visualizador'
        usuario = session.get('usuario_nombre', session.get('usuario_id'))

        with lock_conexiones:
            conexiones[sid] = {
                "sid": sid,
                "rol": rol,
                "usuario": usuario,
                "conectado_en": datetime.now().isoformat()
            }

        join_room(SALA_TODOS)
        if rol == 'operador':
            join_room(SALA_OPERADORES)
        else:
            join_room(SALA_VISUALIZADORES)

        emit('estado_inicial', {
            "rol": rol,
            "usuario": usuario,
            "vehiculos": fleet.estado_resumen(),
            "incidentes": fleet.listado_incidentes(),
            "timestamp": datetime.now().isoformat()
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
        rol = session.get('rol') or session.get('usuario_rol')
        if rol != 'operador':
            emit('error_control', {"error": "No autorizado"})
            return

        accion = (data or {}).get('accion')
        vehiculo_id = (data or {}).get('vehiculo_id')

        if accion == 'asignar':
            incidente_data = (data or {}).get('incidente') or {}
            inc_id = fleet.asignar_manual(vehiculo_id, incidente_data)
            emit('control_resultado', {
                "accion": accion,
                "vehiculo_id": vehiculo_id,
                "ok": bool(inc_id),
                "incident_id": inc_id
            })

        elif accion == 'cerrar':
            ok = fleet.cerrar_incidente_manual(vehiculo_id)
            emit('control_resultado', {
                "accion": accion,
                "vehiculo_id": vehiculo_id,
                "ok": ok
            })

        elif accion == 'apoyo':
            tipo = (data or {}).get('tipo') or 'policia'
            veh = fleet.obtener_vehiculo(vehiculo_id) if vehiculo_id else None

            lat_solicitada = _to_float((data or {}).get('lat'))
            lon_solicitada = _to_float((data or {}).get('lon'))

            ref_lat = lat_solicitada if lat_solicitada is not None else (veh.gps.latitud if veh else None)
            ref_lon = lon_solicitada if lon_solicitada is not None else (veh.gps.longitud if veh else None)

            if ref_lat is None or ref_lon is None:
                incidente_ref = next(
                    (
                        inc for inc in fleet.listado_incidentes()
                        if inc.get('status') in ('assigned', 'en_route', 'on_scene', 'queued')
                        and inc.get('lat') is not None and inc.get('lon') is not None
                    ),
                    None,
                )
                if incidente_ref:
                    ref_lat = incidente_ref.get('lat')
                    ref_lon = incidente_ref.get('lon')

            if ref_lat is None or ref_lon is None:
                ref_lat, ref_lon = CENTRO_ARUBA

            evento = {
                "id": f"APOYO-{uuid.uuid4().hex[:6]}",
                "type": _mapear_tipo_apoyo(tipo),
                "severity": (data or {}).get('severity', 'high'),
                "title": (data or {}).get('title') or f"Apoyo solicitado ({tipo})",
                "description": (data or {}).get('description', 'Solicitud de apoyo desde operador'),
                "latitude": float(ref_lat),
                "longitude": float(ref_lon),
                "started_at": datetime.now().isoformat(),
                "origen": "operador"
            }
            inc_id = fleet.manejar_evento(evento)
            emit('control_resultado', {
                "accion": accion,
                "ok": bool(inc_id),
                "incident_id": inc_id
            })

        elif accion == 'mensaje':
            socketio.emit('mensaje_central', {
                "mensaje": (data or {}).get('mensaje', ''),
                "remitente": (data or {}).get('usuario', 'Operador'),
                "timestamp": datetime.now().isoformat()
            }, room=SALA_TODOS)
            emit('control_resultado', {"accion": accion, "ok": True})

        else:
            emit('control_resultado', {"accion": accion, "ok": False, "error": "Accion desconocida"})


def _to_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None

def _mapear_tipo_apoyo(tipo: str) -> str:
    mapa = {
        "bomberos": "fire",
        "ambulancia": "medical_emergency",
        "policia": "accident",
        "proteccion_civil": "storm",
        "dron": "marine_rescue"
    }
    return mapa.get(tipo, "accident")

def bucle_difusion(fleet):
    while True:
        try:
            time.sleep(max(INTERVALO_ACTUALIZACION, 0.5))
            if not socketio:
                continue

            with lock_conexiones:
                hay_clientes = bool(conexiones)
            if not hay_clientes:
                continue

            payload = {
                "vehiculos": fleet.estado_broadcast(),
                "incidentes": fleet.listado_incidentes(),
                "timestamp": datetime.now().isoformat()
            }
            socketio.emit('actualizacion_flotas', payload, room=SALA_TODOS)

        except Exception as exc:
            logger.warning("Error difusion socketio: %s", exc)
            time.sleep(1)
