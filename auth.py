

from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from flask import session, redirect, url_for, request, jsonify
import os
import json

ARCHIVO_USUARIOS = os.path.join(os.path.dirname(__file__), 'users.json')

def cargar_usuarios():
    if not os.path.exists(ARCHIVO_USUARIOS):
        raise ValueError(f"users.json no encontrado en {ARCHIVO_USUARIOS}")

    with open(ARCHIVO_USUARIOS, 'r', encoding='utf-8') as f:
        lista = json.load(f)

    usuarios = {}
    for usr in lista:
        nombre_usr = usr.get('username', '').strip().lower()
        clave = usr.get('password', '').strip()

        if not nombre_usr or not clave:
            continue

        usuarios[nombre_usr] = {
            'hash_contrasena': generate_password_hash(clave),
            'rol': usr.get('rol', 'operador').strip(),
            'nombre': usr.get('nombre', nombre_usr.capitalize()).strip()
        }

    if not usuarios:
        raise ValueError("No hay usuarios validos en users.json")

    return usuarios

USUARIOS = cargar_usuarios()

def autenticar_usuario(usuario, contrasena):
    if usuario not in USUARIOS:
        return None

    datos_usr = USUARIOS[usuario]
    if not check_password_hash(datos_usr['hash_contrasena'], contrasena):
        return None

    return {
        'usuario': usuario,
        'rol': datos_usr['rol'],
        'nombre': datos_usr['nombre']
    }

def registrar_sesion(datos_usuario):
    session['usuario_id'] = datos_usuario['usuario']
    session['usuario_nombre'] = datos_usuario['nombre']
    session['usuario_rol'] = datos_usuario['rol']
    session['autenticado'] = True

def cerrar_sesion():
    session.clear()

def requerir_login(f):
    @wraps(f)
    def funcion_protegida(*args, **kwargs):
        if not session.get('autenticado'):
            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.path.startswith('/api/'):
                return jsonify({'error': 'No autenticado'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return funcion_protegida

def obtener_usuario_actual():
    if session.get('autenticado'):
        return {
            'usuario': session.get('usuario_id'),
            'nombre': session.get('usuario_nombre'),
            'rol': session.get('usuario_rol')
        }
    return None
