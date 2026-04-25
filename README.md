# Gemelo Digital de Aruba — Servicios de Emergencia

> Plataforma de gemelo digital que modela en tiempo real la flota de emergencias de Aruba (Policia, Ambulancia, Bomberos, Proteccion Civil y Drones), integrando telemetria propia, eventos de la isla y clima a traves de buses Kafka, con interfaces de operador y visualizador, chatbot LLM y replay historico.

**Equipo:** 52Sec · **Convocatoria:** HPE 2026 · **Version:** 2.0

---

## Caracteristicas

- **Multi-flota** con factory de unidades especializadas: patrullas policiales, ambulancias, camiones de bomberos, vehiculos de proteccion civil y drones de reconocimiento.
- **Bus de eventos Kafka**: publica telemetria propia (`aruba.team.<id>`) y consume eventos (`aruba.events`) y clima (`aruba.weather`) de la isla.
- **ETA dinamico**: la velocidad efectiva de cada unidad se modula con el factor de entorno (clima/eventos), recalculando el tiempo de llegada en tiempo real.
- **Reconocimiento aereo opcional**: ante eventos de alto impacto se despliega automaticamente un dron scout sobre el incidente.
- **Replay historico desde 1-abr**: el visualizador puede iniciar simulaciones reproduciendo eventos pasados con control de velocidad y pausa.
- **Dashboard Leaflet** centrado en Aruba (12.52, -69.97) con tiles CartoDB Dark, marcadores por unidad, polilineas de ruta y trazas de movimiento.
- **Chatbot LLM** (Qwen / Gemma compatible OpenAI) con contexto operativo en vivo (flota, incidentes, clima).
- **API REST + OpenAPI 3.1** publicada en `/openapi.yaml` y disponible para los demas equipos.
- **Roles diferenciados**: Operador (control multi-unidad y asignacion manual) y Visualizador (lectura global + replay).

---

## Arquitectura

```
                           Aruba Buses (Kafka)
                                  |
        +---------------+---------+---------+---------------+
        |               |                   |               |
   aruba.events    aruba.weather      aruba.team.52sec   ...otros equipos
        |               |                   ^
        v               v                   |
+---------------------------------------------------------+
|                Servidor Flask + Gevent                   |
|                                                          |
|  KafkaBus  --> Entorno + FleetManager                    |
|                  |              |                        |
|                  v              v                        |
|              Vehiculos     Incidentes / Asignaciones     |
|                  |              |                        |
|                  +--> SocketIO bucle_difusion -----+     |
|                                                    |     |
|  IA (LLM) <-- /ask  /api/context     /openapi.yaml |     |
|                                                    |     |
|  GestorSimulaciones (live + replay 1-abr)          |     |
+---------------------------------------------------------+
                                                    |
                              Socket.IO + REST       |
                                                    v
                        +---------------+    +---------------+
                        |   Operador    |    |  Visualizador |
                        | (multi-unidad)|    |  (replay/RO)  |
                        +---------------+    +---------------+
```

---

## Stack tecnologico

| Capa | Tecnologia |
|---|---|
| Servidor | Python 3.11, Flask 3, Flask-Session, Flask-SocketIO + gevent |
| Mensajeria | kafka-python (SASL_PLAINTEXT) |
| Frontend | HTML/CSS/JS vanilla + Leaflet 1.9 + Socket.IO 4.7 |
| Mapas/rutas | Tiles CartoDB Dark, OSRM (rutas), Haversine (interpolacion) |
| LLM | API OpenAI-compatible (Qwen3-235B-A22B / Gemma) |
| Empaquetado | Docker, docker-compose |

Todas las dependencias son **Open Source**. Los buses externos (Aruba inventory, Kafka, LLM) son los proporcionados por la organizacion HPE 2026.

---

## Estructura del proyecto

```
HPE_Final/
|-- main.py                  # Servidor Flask + bucle de simulacion + telemetria Kafka
|-- config.py                # Configuracion centralizada (load_dotenv)
|-- costos.py                # Tarifas operativas por unidad/propulsion
|-- flota.py                 # FleetManager (factory + incidentes + scout aereo)
|-- vehiculo_base.py         # Clase base con telemetria, GPS y escenarios
|-- vehiculo_factory.py      # Factory que instancia la unidad correcta
|-- vehiculo_policia.py
|-- vehiculo_ambulancia.py
|-- vehiculo_bomberos.py
|-- vehiculo_proteccion_civil.py
|-- vehiculo_dron.py
|-- gps.py                   # SimuladorGPS (interpolacion sobre rutas)
|-- rutas.py                 # OSRM + patrullas + clusters
|-- entorno.py               # Cache de clima/eventos publicados por Aruba
|-- kafka_bus.py             # Productor/consumidor Kafka
|-- inventario_aruba.py      # Cliente del inventario de la isla
|-- ia.py / llm_client.py    # Chat con LLM
|-- prompts.py               # Plantillas de prompt
|-- socketio_server.py       # Salas, broadcast y controles del operador
|-- simulaciones.py          # Replay historico + simulacion en vivo
|-- auth.py                  # Login y gestion de sesiones
|-- helpers.py
|-- apis/
|   `-- team-api.yaml        # OpenAPI 3.1 publicado en /openapi.yaml
|-- static/
|   |-- css/{style.css, dashboard.css}
|   `-- js/{panel_flota.js, operador.js, visualizador.js}
|-- templates/
|   |-- base.html  landing.html  login.html
|   |-- simulador.html        # Vista del operador
|   `-- comando.html          # Vista del visualizador
|-- users.json.example
|-- requirements.txt
|-- Dockerfile
|-- docker-compose.yml
`-- .env.example
```

---

## Instalacion local

```bash
git clone <repo>
cd HPE_Final

python -m venv venv
source venv/bin/activate          # o .\venv\Scripts\activate en Windows

pip install -r requirements.txt

cp .env.example .env               # rellenar credenciales
cp users.json.example users.json   # crear usuarios reales

python main.py
```

Servidor disponible en `http://localhost:8080`.

## Ejecucion en Docker

```bash
cp .env.example .env
docker compose up --build -d
```

El compose monta `users.json`, `flask_session/` y `logs/` como volumenes y expone el puerto 8080. El healthcheck consulta `/health`.

---

## Variables de entorno principales

| Variable | Proposito |
|---|---|
| `TEAM_ID` | Identificador del equipo (se usa para componer el topic de telemetria). |
| `KAFKA_BROKER`, `KAFKA_USERNAME`, `KAFKA_PASSWORD` | Credenciales SASL_PLAINTEXT del bus Kafka de Aruba. |
| `KAFKA_TOPIC_TELEMETRIA` | Topic donde el equipo publica su telemetria. |
| `KAFKA_TOPIC_CLIMA`, `KAFKA_TOPIC_EVENTOS` | Buses oficiales de Aruba que se consumen. |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | Endpoint OpenAI-compatible para el chatbot. |
| `HOST_SERVIDOR`, `PUERTO_SERVIDOR` | Bind del servidor Flask. |
| `INTERVALO_SIM`, `INTERVALO_TELEMETRIA` | Frecuencia del bucle de simulacion y la publicacion Kafka. |

Todos los valores estan documentados en `.env.example`.

---

## API REST

| Metodo | Ruta | Descripcion |
|---|---|---|
| GET | `/health` | Healthcheck. |
| GET | `/openapi.yaml` | Contrato OpenAPI 3.1 del equipo. |
| GET | `/vehicles` | Estado completo de la flota. |
| GET | `/vehicles/status` | Resumen agregado (totales, costes, ocupacion). |
| GET | `/vehicles/{id}` | Detalle de una unidad. |
| GET | `/incidents` | Incidentes activos y resueltos. |
| GET | `/simulations` | Lista de simulaciones (live + replays). |
| POST | `/simulations/replay` | Lanza un replay con `started_at` y `speed`. |
| GET | `/simulations/{sim_id}/state` | Estado del replay. |
| POST | `/simulations/{sim_id}/pause` | Pausa o reanuda. |
| POST | `/simulations/{sim_id}/speed` | Ajusta velocidad. |
| POST | `/ask` | Chatbot con LLM. |
| GET | `/api/context` | Contexto (flota + entorno) para integraciones. |

Eventos Socket.IO clave:

| Evento | Direccion |
|---|---|
| `estado_inicial` | Servidor -> cliente al conectar |
| `actualizacion_flotas` | Servidor -> sala (operadores+visualizadores) cada `INTERVALO_ACTUALIZACION` |
| `control_incidente` | Operador -> servidor (`asignar` / `cerrar` / `apoyo` / `mensaje`) |
| `mensaje_central` | Servidor -> sala global |

---

## Roles

| Rol | Vista | Capacidades |
|---|---|---|
| **Operador** | `/operador` | Ve toda la flota, asigna manualmente unidades, cierra incidentes, solicita apoyo y emite mensajes a la flota. Multiples operadores pueden trabajar simultaneamente. |
| **Visualizador** | `/visualizador` | Solo lectura. Visualiza la flota completa, los incidentes y dispone de controles para lanzar replays historicos a partir del 1 de abril. |

---

## Replay historico

Desde el visualizador (o por API):

```bash
curl -X POST http://localhost:8080/simulations/replay \
     -H 'Content-Type: application/json' \
     -d '{"started_at": "2026-04-01T08:00:00Z", "speed": 4.0}'
```

El gestor crea un `KafkaConsumer` con `auto_offset_reset=earliest` y reinyecta los eventos pasados al `FleetManager` respetando el delta de tiempo escalado por la velocidad solicitada.

---

## Demo

URL: por confirmar

## Licencia

Proyecto realizado por el equipo **52Sec** para HPE 2026.
