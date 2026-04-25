# Gemelo Digital de Vehículo Policial

> Prototipo funcional de un Gemelo Digital que modela en tiempo real un vehículo policial, permite simular escenarios tácticos mediante lenguaje natural y aporta inteligencia operativa a través de IA.

**Equipo:** 52Sec · **Fase:** HPE 2026 · **Versión:** 1.0

---

## Descripción

El sistema replica el comportamiento completo de un vehículo policial — combustible, motor, frenos, neumáticos, posición GPS — y permite crear escenarios tácticos descritos en lenguaje natural. Un motor de IA (Claude Sonnet 4) analiza viabilidad, riesgos e impacto en recursos antes de cada operación.

### Capacidades principales

- **Telemetría en tiempo real** del estado del vehículo con actualización a 10 Hz.
- **Simulación de escenarios tácticos** descritos en lenguaje natural.
- **Análisis de IA** con evaluación de viabilidad, riesgos e impacto en recursos.
- **Rutas reales** sobre calles de Madrid con datos de clima y tráfico en vivo.
- **Centro de Comando** para coordinación de múltiples unidades simultáneamente.

---

## Arquitectura

```
┌─────────────────────── APIs Externas ───────────────────────┐
│  Open-Meteo  │  TomTom Traffic  │  OSRM  │  Nominatim  │  Anthropic IA  │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                SERVIDOR FLASK + GEVENT                       │
│                                                              │
│  ┌─────────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │ Motor Simulación│  │  Motor de IA │  │ WebSocket Server│  │
│  │ (Hilo 10 Hz)    │  │ Claude Sonnet│  │ Flask-SocketIO  │  │
│  └─────────────────┘  └──────────────┘  └────────────────┘  │
│  ┌─────────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │VehiculoPolicial │  │ SimuladorGPS │  │    Entorno      │  │
│  │Telemetría,fases │  │ OSRM,Haversine│ │ Clima,Tráfico  │  │
│  └─────────────────┘  └──────────────┘  └────────────────┘  │
└──────────┬───────────────────────────────────┬──────────────┘
           │          WebSocket (Socket.IO)     │
    ┌──────▼──────┐                     ┌──────▼──────┐
    │  Simulador  │                     │  Centro de  │
    │ (Operador)  │                     │   Comando   │
    │             │                     │(Despachador)│
    └─────────────┘                     └─────────────┘
```

El sistema sigue una arquitectura cliente-servidor con comunicación bidireccional en tiempo real. El servidor ejecuta la simulación completa y los clientes renderizan el estado recibido, garantizando consistencia entre múltiples usuarios.

---

## Stack Tecnológico

### Backend

| Tecnología | Uso |
|---|---|
| Python 3.x | Lenguaje del servidor |
| Flask | Framework web (rutas HTTP, sesiones, templates) |
| Flask-SocketIO | Servidor WebSocket con soporte de salas y eventos |
| Gevent | Servidor WSGI asíncrono para concurrencia |
| Gunicorn | Servidor de producción (worker class gevent) |
| Anthropic SDK | Integración con la API de Claude |
| Werkzeug | Hash seguro de contraseñas |
| python-dotenv | Carga de variables de entorno desde `.env` |

### Frontend

| Tecnología | Uso |
|---|---|
| HTML5 / CSS3 / JavaScript ES6+ | Interfaz sin frameworks externos |
| Leaflet 1.9.4 | Mapas interactivos con tiles CartoDB Dark |
| Socket.IO Client 4.7.2 | Comunicación WebSocket bidireccional |

### APIs Externas

| API | Dato | Autenticación |
|---|---|---|
| Anthropic | Análisis de escenarios con Claude Sonnet 4 | API Key |
| Open-Meteo | Clima actual en Madrid | Pública |
| TomTom Traffic | Estado del tráfico en tiempo real | API Key |
| OSRM | Rutas reales entre coordenadas | Pública |
| Nominatim (OSM) | Geocodificación de direcciones | Pública (User-Agent) |

---

## Estructura del Proyecto

```
HPE/
├── main.py                 # Servidor principal Flask + hilo de simulación
├── config.py               # Configuración centralizada (variables de entorno)
├── vehiculo.py             # Clase VehiculoPolicial (telemetría y escenarios)
├── ia.py                   # Motor de IA (integración con Anthropic Claude)
├── prompts.py              # Prompts estructurados para la IA
├── gps.py                  # Simulador GPS (interpolación, Haversine)
├── rutas.py                # Generación de rutas (OSRM, Nominatim)
├── entorno.py              # Datos de entorno (clima Open-Meteo, tráfico TomTom)
├── socketio_server.py      # Servidor WebSocket (difusión en tiempo real)
├── auth.py                 # Autenticación y gestión de sesiones
├── helpers.py              # Funciones auxiliares
├── gunicorn.ctl            # Configuración de Gunicorn para producción
├── users.json.example      # Ejemplo de archivo de usuarios
├── .env                    # Variables de entorno (no incluido en repo)
├── users.json              # Credenciales de usuarios (no incluido en repo)
├── static/
│   ├── css/style.css       # Estilos (diseño oscuro profesional)
│   └── js/
│       ├── simulador.js    # Lógica del simulador (telemetría, mapa, escenarios)
│       └── socketClient.js # Cliente WebSocket (reconexión automática)
└── templates/
    ├── base.html           # Template base con navegación
    ├── landing.html        # Página de inicio
    ├── login.html          # Inicio de sesión
    ├── simulador.html      # Vista del operador
    └── comando.html        # Vista del centro de comando
```

---

## Instalación

### Requisitos previos

- Python 3.8 o superior
- Clave API de Anthropic (para análisis con IA)
- Clave API de TomTom (opcional, para datos de tráfico)

### Pasos

```bash
# Clonar el repositorio
git clone <repositorio>
cd HPE

# Crear entorno virtual
python -m venv venv
source venv/bin/activate

# Instalar dependencias
pip install flask flask-socketio flask-session gevent gunicorn \
    anthropic requests python-dotenv werkzeug
```

---

## Configuración

### 1. Variables de entorno

Crear un archivo `.env` en la raíz del proyecto:

```env
FLASK_SECRET_KEY=clave_secreta_aleatoria
FLASK_DEBUG=false
HOST_SERVIDOR=0.0.0.0
PUERTO_SERVIDOR=5000
ANTHROPIC_API_KEY=sk-ant-...
TOMTOM_API_KEY=clave_tomtom
```

### 2. Usuarios

Crear el archivo `users.json` basándose en `users.json.example`:

```json
[
  {
    "username": "operador1",
    "password": "contrasena_segura",
    "rol": "operador",
    "nombre": "Operador 1"
  },
  {
    "username": "dispatch",
    "password": "contrasena_segura",
    "rol": "despachador",
    "nombre": "Central"
  }
]
```

---

## Ejecución

```bash
# Desarrollo
python main.py

# Producción con Gunicorn
gunicorn --worker-class gevent -w 1 --bind 0.0.0.0:5000 main:app
```

---

## Roles de Usuario

| Rol | Vista | Capacidades |
|---|---|---|
| **Operador** | Simulador | Tiene su propio vehículo policial. Crea escenarios en lenguaje natural, monitorea telemetría en vivo, ve posición GPS y controla la simulación. |
| **Despachador** | Centro de Comando | Sin vehículo propio. Ve todas las unidades conectadas en un mapa unificado. Selecciona cualquier unidad para ver sus detalles. Recibe actualizaciones de todos los vehículos. |

---

## Simulación de Escenarios con IA

1. **Entrada del usuario** — El operador describe un escenario en lenguaje natural (ej: *"Acudir a un accidente en Gran Vía con heridos"*).
2. **Enriquecimiento con contexto real** — Se obtiene clima actual (Open-Meteo), tráfico en tiempo real (TomTom) y estado completo del vehículo.
3. **Análisis con IA** — Claude Sonnet 4 recibe todo el contexto y devuelve: detección del escenario (fases, duración, perfil de velocidad) y análisis operativo (riesgos, impacto, viabilidad).
4. **Generación de ruta** — Si hay ubicación, se geocodifica con Nominatim y se calcula la ruta con OSRM por calles reales de Madrid.
5. **Aplicación al vehículo** — Se establecen velocidad objetivo, modificadores de consumo/temperatura/desgaste y ruta GPS.
6. **Ejecución en tiempo real** — La simulación avanza a 10 Hz. El operador observa telemetría actualizada y el vehículo moviéndose en el mapa.

---

## Telemetría del Vehículo

| Sistema | Variable | Comportamiento |
|---|---|---|
| Motor | Temperatura | Sube con la velocidad, baja cuando está parado. Máx: 120°C |
| Combustible | Nivel (%) | Consumo proporcional a velocidad y factor del escenario |
| Frenos | Desgaste (%) | Proporcional a velocidad y factor de desgaste |
| Neumáticos | Desgaste (%) | Similar a frenos, con mayor factor |
| Aceite | Nivel (%) | Valor fijo con inicialización aleatoria (80-100%) |
| Velocidad | km/h | Aceleración/frenado gradual con perfil configurable |
| GPS | lat/lon | Interpolación sobre rutas reales (OSRM + Haversine) |

---

## Comunicación en Tiempo Real

| Evento | Dirección | Frecuencia |
|---|---|---|
| `estado_vehiculo` | Servidor → Operador | Cada 100ms |
| `actualizacion_vehiculo` | Servidor → Despachadores | Cada 500ms |
| `todos_vehiculos` | Servidor → Despachador | Al conectarse |
| `control_simulacion` | Operador → Servidor | Bajo demanda |
| `vehiculo_desconectado` | Servidor → Despachadores | Al desconectarse |

La ruta solo se envía cuando cambia (nueva ruta de patrulla o escenario) mediante un sistema de versionado.

---

## Demo en Vivo

**URL:** [https://hpe.52sec.org](https://hpe.52sec.org)

---

## Licencia

Proyecto desarrollado por el equipo **52Sec** para HPE 2026.
