# Gemelo Digital de Aruba — Servicios de Emergencia

> Plataforma de gemelo digital que modela en tiempo real la flota de emergencias de Aruba (Policía, Ambulancia, Bomberos, Protección Civil y Drones), integrando telemetría propia, eventos de la isla y clima a través de Kafka, interfaces de operador y visualizador, chatbot LLM con formato enriquecido, POIs del inventario Aruba en el mapa, y replay histórico.

**Equipo:** 52Sec · **Convocatoria:** HPE 2026 · **Versión:** 2.1

---

## Características

### Backend y datos

- **Multi-flota** con unidades especializadas: patrullas, ambulancias, bomberos, protección civil y drones.
- **Bus Kafka**: publica telemetría (`KAFKA_TOPIC_TELEMETRIA`) y consume clima (`KAFKA_TOPIC_CLIMA`) y eventos (`KAFKA_TOPIC_EVENTOS`). Los mensajes de eventos se indexan en caché (`kafka_bus.py`) para depuración y trazabilidad.
- **Eventos en tiempo real**: al procesar un evento Kafka válido, el servidor emite de inmediato `actualizacion_flotas` por Socket.IO (mismo criterio que las actualizaciones de clima), de modo que el front no depende solo del ciclo periódico.
- **Demo automática de incidentes** (opcional): si está activa, `generador_incidentes.py` puede inyectar incidentes simulados cuando Kafka lleva un tiempo sin eventos. Parámetros típicos (variables de entorno): `AUTO_DEMO_MIN_S`, `AUTO_DEMO_MAX_S`, `AUTO_DEMO_SILENCIO_S`, `AUTO_DEMO_MAX_ACTIVOS`.
- **Inventario Aruba** (`inventario_aruba.py`): estaciones, POIs, carreteras y tipos; caché configurable (`CACHE_ENTORNO`).
- **POIs operativos en mapa**: endpoint `GET /api/map/pois` devuelve puntos del inventario filtrados por relevancia (salud, bomberos, policía, combustible, puertos/aeropuertos, emergencias, etc.) con coordenadas normalizadas. El front los pinta como marcadores pequeños con iconos SVG uniformes (gris claro).
- **ETA dinámico**: velocidad efectiva modulada por factor de entorno (clima/eventos); recálculo de ETA en vivo.
- **Rutas**: OSRM con fallback a grafo del inventario y línea recta entre landmarks (`rutas.py`, `osrm_client.py`).
- **Costes operativos** (`costos.py`): tarifas, resúmenes y estimaciones vía API REST bajo `/costs/...`.
- **Seguridad / sabotaje** (`sabotaje.py`): rutas bajo `/security/sabotage` para detección y gestión orientada a pruebas de integridad de telemetría.
- **Historial clínico** (ambulancias): rutas relacionadas en `main.py` / módulo `historial_clinico.py`.
- **Replay histórico**: desde el 1-abr-2026, con velocidad, pausa y snapshot aislado (`simulaciones.py`).
- **API REST + OpenAPI 3.1** en `/openapi.yaml`.

### Frontend (dashboard)

- **Leaflet** centrado en Aruba (~12.52, -69.97): tiles Carto **Dark** / **Light** según tema.
- **Marcadores de unidad** con letra por tipo; **ruta** y **pin de destino** solo para la unidad seleccionada (sin círculos genéricos de incidente en el mapa que generaran residuo visual).
- **Tema claro / oscuro**: conmutador en la barra superior, preferencia en `localStorage`, evento `tema-cambiado` para sincronizar capas del mapa.
- **Accesibilidad**: atajos de teclado (Alt+T tema, Alt+U/I/C/M foco en listas/chat/mapa, flechas en listas, `?` ayuda, Esc).
- **Chat asistente**: respuestas del bot renderizadas con **Marked** (markdown: negritas, listas, código, etc.); mensajes de usuario en texto plano.
- **Responsive**: en pantallas estrechas el grid pasa a columna única con mapa arriba; listas con altura máxima y scroll; formulario de replay apilado; botones con área táctil cómoda; menú **hamburguesa** en móvil para navegación (enlaces de consola / usuario).
- **Operador** (`simulador.html`): alta y baja de unidades vía API; sin botón “Mensaje a flota” ni botones de “apoyo” rápido en cabecera (control vía otras vías si aplica en backend).
- **Visualizador** (`comando.html`): LIVE + replay, decisiones de despacho, clima y chat.

---

## Arquitectura

```
                           Aruba Buses (Kafka)
                                  |
        +---------------+---------+---------+---------------+
        |               |                   |               |
   aruba.events    aruba.weather      aruba.team.<id>   ...
        |               |                   ^
        v               v                   |
+---------------------------------------------------------+
|                Servidor Flask + Gevent                   |
|                                                          |
|  KafkaBus  --> hooks (clima / eventos)                  |
|       |                                                  |
|       +--> FleetManager (flota.py) + entorno            |
|                  |              |                        |
|                  v              v                        |
|              Vehículos     Incidentes / asignaciones     |
|                  |              |                        |
|                  +--> Socket.IO (actualizacion_flotas,   |
|                        clima_actualizado, mensaje_central)|
|                                                          |
|  InventarioAruba --> /api/map/pois, rutas, health       |
|  IA (LLM) <-- /ask     GestorSimulaciones (replay)      |
+---------------------------------------------------------+
                                                    |
                              Socket.IO + REST       |
                                                    v
                        +---------------+    +---------------+
                        |   Operador    |    |  Visualizador |
                        +---------------+    +---------------+
```

---

## Stack tecnológico

| Capa | Tecnología |
|------|------------|
| Servidor | Python 3.11+, Flask 3, Flask-Session, Flask-SocketIO + gevent |
| Mensajería | kafka-python (SASL según configuración) |
| Frontend | HTML/CSS/JS, Leaflet 1.9, Socket.IO, **marked** (CDN) para markdown en chat |
| Mapas / rutas | Carto basemaps (dark/light), OSRM, Haversine |
| LLM | API compatible OpenAI (Qwen / Gemma, etc.) |
| Empaquetado | Docker, docker-compose |

---

## Estructura del proyecto

```
HPE_Final/
|-- main.py                  # Flask, rutas, hooks Kafka, integración flota
|-- config.py                # Variables de entorno centralizadas
|-- kafka_bus.py             # Productor/consumidor Kafka y caché clima/eventos
|-- flota.py                 # FleetManager, factory, incidentes, auto-demo
|-- generador_incidentes.py  # Demo de incidentes y envoltorio de callbacks Kafka
|-- inventario_aruba.py      # Cliente API inventario (POIs, roads, stations)
|-- socketio_server.py       # Salas, broadcast, clima para difusión
|-- simulaciones.py          # Live + replay histórico
|-- entorno.py               # Clima ambiente para simulación de motores
|-- rutas.py / osrm_client.py
|-- gps.py
|-- costos.py
|-- sabotaje.py              # Endpoints de detección de sabotaje
|-- historial_clinico.py     # Historial asociado a ambulancias
|-- vehiculo_*.py            # Especializaciones por tipo
|-- auth.py, helpers.py
|-- ia.py / llm_client.py / prompts.py
|-- static/
|   |-- css/dashboard.css    # Dashboard + temas + responsive + Leaflet/POI
|   `-- js/
|       |-- panel_flota.js   # Mapa, POIs, tema tiles, rutas
|       |-- operador.js
|       `-- visualizador.js
|-- templates/
|   |-- base.html            # Tema, teclado, hamburguesa móvil
|   |-- simulador.html         # Operador
|   |-- comando.html           # Visualizador
|-- apis/                    # Contratos JSON de referencia
|-- requirements.txt
|-- Dockerfile
|-- docker-compose.yml
`-- .env.example
```

> **Nota:** `base.html` puede referenciar `static/css/style.css` para layout global del sitio; si no está presente, el dashboard sigue funcionando con `dashboard.css` y estilos inline en `base.html`.

---

## Instalación local

```bash
git clone <repo>
cd HPE_Final

python -m venv venv
source venv/bin/activate          # Windows: .\venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env               # credenciales
cp users.json.example users.json   # usuarios (si aplica)

python main.py
```

Servidor por defecto: `http://localhost:8080` (ajustar `PUERTO_SERVIDOR` en `.env`).

### Docker

```bash
cp .env.example .env
docker compose up --build -d
```

Healthcheck: `GET /health`.

---

## Variables de entorno principales

| Variable | Propósito |
|----------|-----------|
| `TEAM_ID` | Identificador del equipo (topic de telemetría). |
| `KAFKA_BROKER`, `KAFKA_USERNAME`, `KAFKA_PASSWORD`, `KAFKA_SECURITY_PROTOCOL`, `KAFKA_SASL_MECHANISM` | Conexión al clúster Kafka. |
| `KAFKA_TOPIC_TELEMETRIA`, `KAFKA_TOPIC_CLIMA`, `KAFKA_TOPIC_EVENTOS` | Topics de publicación/consumo. |
| `KAFKA_OFFSET_CLIMA`, `KAFKA_OFFSET_EVENTOS` | `earliest` / `latest` por consumidor. |
| `ARUBA_INVENTORY_API` | URL base del inventario Aruba (POIs, roads, stations). |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | Chatbot OpenAI-compatible. |
| `HOST_SERVIDOR`, `PUERTO_SERVIDOR` | Bind del servidor. |
| `INTERVALO_SIM`, `INTERVALO_TELEMETRIA` | Bucles de simulación y publicación. |
| `CACHE_ENTORNO` | TTL caché HTTP inventario (segundos). |
| `AUTO_DEMO_MIN_S`, `AUTO_DEMO_MAX_S`, `AUTO_DEMO_SILENCIO_S`, `AUTO_DEMO_MAX_ACTIVOS` | (Opcional) ritmo de la demo automática de incidentes cuando está habilitada en código. |

Detalle adicional en `.env.example`.

---

## API REST (selección)

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/health`, `/health/kafka`, `/health/inventory`, `/health/fleet`, `/health/osrm` | Salud del sistema y dependencias. |
| GET | `/api/map/pois` | POIs del inventario filtrados por relevancia operativa (JSON para el mapa). |
| GET | `/openapi.yaml` | OpenAPI 3.1. |
| GET | `/vehicles`, `/vehicles/status`, `/vehicles/{id}` | Flota. |
| GET | `/incidents` | Incidentes. |
| POST | `/fleet/units` | Alta de unidad (operador). |
| DELETE | `/fleet/units/{id}` | Baja de unidad (operador). |
| GET/POST | `/costs/...` | Costes y tarifas. |
| GET | `/security/sabotage`, `/security/sabotage/{id}` | Flujos de sabotaje/detección. |
| GET | `/simulations`, `/simulations/replay` (POST), `/simulations/{id}/snapshot`, etc. | Replay y estado. |
| GET | `/ask` | Chatbot (query `q`). |
| GET | `/weather-stations`, `/weather-stations/{id}/reading` | Estaciones meteorológicas del inventario. |

Lista completa en `main.py` y en OpenAPI.

---

## Socket.IO

| Evento | Dirección | Notas |
|--------|-----------|--------|
| `estado_inicial` | Servidor → cliente | Al conectar. |
| `actualizacion_flotas` | Servidor → sala | Vehículos, incidentes, factor de clima, etc.; también tras eventos Kafka procesados. |
| `clima_actualizado` | Servidor → cliente | Lectura nueva de clima. |
| `mensaje_central` | Servidor → sala | Difusión de mensajes (si se usa desde backend). |
| `control_incidente` | Cliente → servidor | Acciones de operador (asignar, cerrar, etc. según implementación en `socketio_server.py`). |

---

## Roles

| Rol | Vista | Capacidades |
|-----|-------|-------------|
| **Operador** | `/operador` | Flota en vivo, mapa, detalle, chat, alta/baja de unidades. |
| **Visualizador** | `/visualizador` | Solo lectura + replay, decisiones, clima, chat. |
| **Ciudadano** | `/ciudadano` | Vista pública según rutas definidas. |

---

## Replay histórico

Ejemplo:

```bash
curl -X POST http://localhost:8080/simulations/replay \
     -H 'Content-Type: application/json' \
     -d '{"started_at": "2026-04-01T08:00:00Z", "speed": 4.0}'
```

El gestor consume Kafka desde el offset adecuado y reinyecta eventos al `FleetManager` con escala temporal configurable.

---

## Licencia

Proyecto realizado por el equipo **52Sec** para HPE 2026.
