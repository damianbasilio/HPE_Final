

const ARUBA_CENTRO = [12.5211, -69.9683];
const ARUBA_ZOOM = 12;

/* Registro global de instancias para que el toggle de tema las encuentre */
const _panelInstances = [];

const TIPO_META = {
  policia:          { letra: 'P', color: '#3b82f6', etiqueta: 'Policia' },
  ambulancia:       { letra: 'A', color: '#ef4444', etiqueta: 'Ambulancia' },
  bomberos:         { letra: 'B', color: '#f97316', etiqueta: 'Bomberos' },
  proteccion_civil: { letra: 'C', color: '#10b981', etiqueta: 'Proteccion Civil' },
  dron:             { letra: 'D', color: '#a855f7', etiqueta: 'Dron' },
};

const SEVERIDAD_COLOR = {
  low: '#10b981',
  medium: '#f59e0b',
  high: '#f97316',
  critical: '#ef4444',
};

class PanelFlota {
  constructor({ idMapa, modo = 'visualizador', onSeleccion = null } = {}) {
    this.modo = modo;
    this.onSeleccion = onSeleccion;
    this.mapa = null;
    this.tilesLayer = null;
    _panelInstances.push(this);
    this.marcadores = new Map();
    this.rutas = new Map();
    this.destinoMarcadores = new Map();   // pin de destino del incidente
    this.rastros = new Map();
    this.incidenteMarcadores = new Map();
    this.flota = [];
    this.incidentes = [];
    this.seleccionado = null;
    this._inicializarMapa(idMapa);
  }

  _inicializarMapa(idMapa) {
    const el = document.getElementById(idMapa);
    if (!el) {
      console.warn('[PanelFlota] contenedor de mapa no encontrado:', idMapa);
      return;
    }
    if (typeof L === 'undefined') {
      console.warn('[PanelFlota] Leaflet no esta cargado, dashboard funcionara sin mapa.');
      el.innerHTML = '<div style="padding:24px;color:#94a3b8;">Mapa no disponible (Leaflet no cargo). El resto del panel sigue activo.</div>';
      return;
    }
    try {
      this.mapa = L.map(idMapa, {
        zoomControl: true,
        attributionControl: true,
      }).setView(ARUBA_CENTRO, ARUBA_ZOOM);

      this.tilesLayer = L.tileLayer(
        'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
        {
          maxZoom: 19,
          attribution: 'OpenStreetMap, CARTO',
        }
      ).addTo(this.mapa);

      // Click en zona vacia del mapa = deseleccionar unidad y limpiar ruta/destino
      this.mapa.on('click', () => this.seleccionarUnidad(null));
    } catch (err) {
      console.error('[PanelFlota] error inicializando mapa:', err);
      this.mapa = null;
    }
  }

  _construirIcono(unidad) {
    const meta = TIPO_META[unidad.tipo] || { letra: '?', color: '#94a3b8', etiqueta: unidad.tipo };
    const enIntervencion = unidad.escenario && unidad.escenario.en_progreso;
    const animacion = enIntervencion ? 'box-shadow:0 0 16px ' + meta.color : '';
    const html = `<div class="dt-marker" style="background:${meta.color};${animacion}">
      <span>${meta.letra}</span>
    </div>`;
    return L.divIcon({
      className: 'dt-marker-wrapper',
      html,
      iconSize: [28, 28],
      iconAnchor: [14, 14],
    });
  }

  actualizarFlota(payload) {
    if (!payload || !this.mapa) return;
    this.flota = payload.vehiculos || [];
    this.incidentes = payload.incidentes || [];

    const idsFlota = new Set();
    for (const u of this.flota) {
      idsFlota.add(u.id);
      this._dibujarUnidad(u);
    }

    // Limpiar marcadores de vehiculos que ya no existen
    for (const id of [...this.marcadores.keys()]) {
      if (!idsFlota.has(id)) {
        this.mapa.removeLayer(this.marcadores.get(id));
        this.marcadores.delete(id);
        this._limpiarRutaUnidad(id);
      }
    }

    this._dibujarIncidentes();
  }

  _dibujarUnidad(unidad) {
    const gps = unidad.gps || {};
    if (gps.latitud == null || gps.longitud == null) return;
    const pos = [gps.latitud, gps.longitud];

    let marcador = this.marcadores.get(unidad.id);
    if (!marcador) {
      marcador = L.marker(pos, { icon: this._construirIcono(unidad) }).addTo(this.mapa);
      marcador.bindTooltip(unidad.nombre || unidad.id, { permanent: false, direction: 'top' });
      marcador.on('click', () => this.seleccionarUnidad(unidad.id));
      this.marcadores.set(unidad.id, marcador);
    } else {
      marcador.setLatLng(pos);
      marcador.setIcon(this._construirIcono(unidad));
    }

    this._dibujarRastro(unidad);
    this._dibujarRutaIncidente(unidad);
  }

  _dibujarRastro(unidad) {
    if (!unidad.rastro || unidad.rastro.length < 2) {
      const linea = this.rastros.get(unidad.id);
      if (linea) {
        this.mapa.removeLayer(linea);
        this.rastros.delete(unidad.id);
      }
      return;
    }
    const meta = TIPO_META[unidad.tipo] || { color: '#94a3b8' };
    let linea = this.rastros.get(unidad.id);
    if (!linea) {
      linea = L.polyline(unidad.rastro, {
        color: meta.color,
        opacity: 0.45,
        weight: 2.5,
        smoothFactor: 1.6,
      }).addTo(this.mapa);
      this.rastros.set(unidad.id, linea);
    } else {
      linea.setLatLngs(unidad.rastro);
    }
  }

  _limpiarRutaUnidad(id) {
    const r = this.rutas.get(id);
    if (r) { this.mapa.removeLayer(r); this.rutas.delete(id); }
    const d = this.destinoMarcadores.get(id);
    if (d) { this.mapa.removeLayer(d); this.destinoMarcadores.delete(id); }
  }

  _dibujarRutaIncidente(unidad) {
    const esSeleccionado = (unidad.id === this.seleccionado);
    const inc = unidad.incidente;
    const enCamino = unidad.escenario && unidad.escenario.en_camino;

    // Ocultar ruta y destino si la unidad no esta seleccionada o no va hacia un incidente
    if (!esSeleccionado || !inc || inc.lat == null || inc.lon == null || !enCamino) {
      this._limpiarRutaUnidad(unidad.id);
      return;
    }

    const color = SEVERIDAD_COLOR[inc.severity] || '#f59e0b';
    const esDron = (unidad.tipo === 'dron');

    // --- Polyline de ruta ---
    const puntosRuta = unidad.ruta_restante;
    let trazo;
    if (puntosRuta && puntosRuta.length >= 2) {
      trazo = puntosRuta;
    } else {
      // Fallback linea recta si no llega ruta del backend
      const gps = unidad.gps || {};
      if (gps.latitud == null) return;
      trazo = [[gps.latitud, gps.longitud], [inc.lat, inc.lon]];
    }

    let rutaLayer = this.rutas.get(unidad.id);
    const estiloRuta = esDron
      ? { color, weight: 2, opacity: 0.75, dashArray: '4, 10' }
      : { color, weight: 3, opacity: 0.85, dashArray: null };

    if (!rutaLayer) {
      rutaLayer = L.polyline(trazo, estiloRuta).addTo(this.mapa);
      this.rutas.set(unidad.id, rutaLayer);
    } else {
      rutaLayer.setLatLngs(trazo);
      rutaLayer.setStyle(estiloRuta);
    }

    // --- Pin de destino ---
    const destPos = [inc.lat, inc.lon];
    const iconoDestino = L.divIcon({
      className: '',
      html: `<div style="width:14px;height:14px;border-radius:50%;background:${color};
             border:2px solid #fff;box-shadow:0 0 8px ${color};opacity:0.95"></div>`,
      iconSize: [14, 14],
      iconAnchor: [7, 7],
    });
    let destMarker = this.destinoMarcadores.get(unidad.id);
    if (!destMarker) {
      destMarker = L.marker(destPos, { icon: iconoDestino, zIndexOffset: 100 })
        .bindTooltip(`${inc.title || inc.incident_type || 'Incidente'} · ${inc.severity || 'medium'}`,
                     { direction: 'top', offset: [0, -8] })
        .addTo(this.mapa);
      this.destinoMarcadores.set(unidad.id, destMarker);
    } else {
      destMarker.setLatLng(destPos);
      destMarker.setIcon(iconoDestino);
    }
  }

  _dibujarIncidentes() {
    const ids = new Set();
    for (const inc of this.incidentes) {
      if (inc.status === 'resolved' || inc.status === 'cancelled') continue;
      if (inc.lat == null || inc.lon == null) continue;
      ids.add(inc.incident_id);

      const color = SEVERIDAD_COLOR[inc.severity] || '#f59e0b';
      let marker = this.incidenteMarcadores.get(inc.incident_id);
      if (!marker) {
        marker = L.circleMarker([inc.lat, inc.lon], {
          radius: 9,
          color,
          weight: 2,
          fillColor: color,
          fillOpacity: 0.25,
        }).addTo(this.mapa);
        marker.bindTooltip(`${inc.title || inc.incident_type} (${inc.severity || 'medium'})`,
                           { direction: 'top' });
        this.incidenteMarcadores.set(inc.incident_id, marker);
      } else {
        marker.setLatLng([inc.lat, inc.lon]);
        marker.setStyle({ color, fillColor: color });
      }
    }

    for (const id of [...this.incidenteMarcadores.keys()]) {
      if (!ids.has(id)) {
        this.mapa.removeLayer(this.incidenteMarcadores.get(id));
        this.incidenteMarcadores.delete(id);
      }
    }
  }

  seleccionarUnidad(id) {
    const anterior = this.seleccionado;

    // Toggle: clic en la misma unidad = deseleccionar
    const nuevoId = (id && id === anterior) ? null : id;
    this.seleccionado = nuevoId;

    // Limpiar ruta de la unidad previamente seleccionada (si la habia)
    if (anterior && anterior !== nuevoId) {
      const antUnidad = this.flota.find((v) => v.id === anterior);
      if (antUnidad) {
        this._dibujarRutaIncidente(antUnidad);
      } else {
        this._limpiarRutaUnidad(anterior);
      }
    }

    // Pintar ruta de la nueva unidad seleccionada
    if (nuevoId) {
      const nuevaUnidad = this.flota.find((v) => v.id === nuevoId);
      if (nuevaUnidad) this._dibujarRutaIncidente(nuevaUnidad);
    }

    if (typeof this.onSeleccion === 'function') {
      this.onSeleccion(nuevoId);
    }
  }

  unidadActual() {
    if (!this.seleccionado) return null;
    return this.flota.find((v) => v.id === this.seleccionado) || null;
  }

  setTema(tema) {
    if (!this.mapa || !this.tilesLayer) return;
    const url = tema === 'light'
      ? 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png'
      : 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
    this.tilesLayer.setUrl(url);
  }

  centrarEn(id) {
    const v = this.flota.find((x) => x.id === id);
    if (!v) return;
    const gps = v.gps || {};
    if (gps.latitud != null && gps.longitud != null && this.mapa) {
      this.mapa.setView([gps.latitud, gps.longitud], Math.max(this.mapa.getZoom(), 13));
    }
  }
}

function fmtNum(v, dec = 1) {
  if (v == null || isNaN(v)) return '--';
  return Number(v).toFixed(dec);
}

function fmtETA(seg) {
  if (!seg || seg <= 0) return '--';
  if (seg < 60) return `${seg}s`;
  const m = Math.floor(seg / 60);
  const s = seg % 60;
  return s ? `${m}m ${s}s` : `${m}m`;
}

function fmtEUR(v) {
  if (v == null) return '--';
  return Number(v).toFixed(2) + ' EUR';
}

function fmtSeconds(seg) {
  if (seg == null || isNaN(seg)) return '--';
  const s = Math.max(0, Math.round(Number(seg)));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r ? `${m}m ${r}s` : `${m}m`;
}

const TIPO_LABELS = {
  policia: 'Policia',
  ambulancia: 'Ambulancia',
  bomberos: 'Bomberos',
  proteccion_civil: 'Proteccion Civil',
  dron: 'Dron',
};

const ENERGIA_LABELS = {
  combustion: 'Combustion',
  electrico: 'Electrico',
  unico: 'Bateria',
};

const ESTADO_VEHICULO_LABELS = {
  patrulla: 'En patrulla',
  aterrizado: 'En base',
  estacionario: 'En base',
  reabasteciendo: 'Repostando',
  'cambio de bateria': 'Cambio de bateria',
};

const SEVERIDAD_LABELS = {
  low: 'Baja',
  medium: 'Media',
  high: 'Alta',
  critical: 'Critica',
};

const INCIDENT_STATUS_LABELS = {
  assigned: 'Asignado',
  en_route: 'En camino',
  at_scene: 'En escena',
  on_scene: 'En escena',
  resolved: 'Resuelto',
  cancelled: 'Cancelado',
  pending: 'Pendiente',
};

const INCIDENT_TYPE_LABELS = {
  medical_emergency: 'Emergencia medica',
  accident: 'Accidente de trafico',
  traffic_jam: 'Atasco',
  fire: 'Incendio',
  hazmat_spill: 'Derrame quimico',
  flood: 'Inundacion',
  storm: 'Tormenta',
  power_outage: 'Apagon',
  earthquake: 'Terremoto',
  marine_rescue: 'Rescate maritimo',
  public_event: 'Evento publico',
  crime: 'Delito',
  manual: 'Asignacion manual',
  incidente: 'Incidente',
};

const TRIAGE_LABELS = {
  rojo: 'Rojo (critico)',
  amarillo: 'Amarillo (urgente)',
  verde: 'Verde (estable)',
  negro: 'Negro (no recuperable)',
};

const DRONE_MODE_LABELS = {
  scout: 'Reconocimiento',
  seguimiento: 'Seguimiento',
  mapeo: 'Mapeo aereo',
  termico: 'Camara termica',
};

const DRONE_RIESGO_LABELS = {
  ninguno: 'Ninguno',
  bateria_critica: 'Bateria critica',
  enlace_debil: 'Enlace debil',
  hotspot_termico: 'Foco termico',
};

const PROTOCOLO_LABELS = {
  armado: 'Intervencion armada',
  perimetro: 'Perimetro de seguridad',
  contencion: 'Contencion',
};

const MISION_PC_LABELS = {
  evacuacion: 'Evacuacion',
  balizamiento: 'Balizamiento',
  apoyo: 'Apoyo logistico',
  logistica: 'Logistica',
};

const TIPO_INCENDIO_LABELS = {
  estructural: 'Estructural',
  derrame: 'Derrame / Hazmat',
  forestal: 'Forestal',
  vehiculo: 'Vehiculo',
  otro: 'Otro',
};

function _humanizarKey(valor) {
  if (valor == null || valor === '') return '--';
  const txt = String(valor).replace(/_/g, ' ').trim();
  return txt.charAt(0).toUpperCase() + txt.slice(1);
}

function humanizar(diccionario, valor, fallback = null) {
  if (valor == null || valor === '') return fallback != null ? fallback : '--';
  const key = String(valor).toLowerCase();
  if (diccionario[key]) return diccionario[key];
  return _humanizarKey(valor);
}

function etiquetaTipo(tipo) { return humanizar(TIPO_LABELS, tipo); }
function etiquetaEnergia(en) { return humanizar(ENERGIA_LABELS, en); }
function etiquetaSeveridad(s) { return humanizar(SEVERIDAD_LABELS, s); }
function etiquetaIncidentStatus(s) { return humanizar(INCIDENT_STATUS_LABELS, s); }
function etiquetaIncidentType(t) { return humanizar(INCIDENT_TYPE_LABELS, t); }
function etiquetaEstadoVehiculo(s) { return humanizar(ESTADO_VEHICULO_LABELS, s, _humanizarKey(s)); }

function renderCosteUnidad(unidad) {
  if (!unidad) return '<p class="muted">Selecciona una unidad para ver su coste.</p>';
  const c = unidad.costes || {};
  const actual = c.desglose_actual || {};
  const acum = c.desglose_acumulado || {};
  const cur = 'EUR';
  const enIntervencion = (c.coste_intervencion_eur || 0) > 0
    || (actual.minutos_facturados || 0) > 0;

  const trSeg = actual.tiempo_respuesta_seg;
  const trTxt = trSeg != null
    ? `${fmtSeconds(trSeg)} ${actual.sla_cumplido ? '(SLA OK)' : '(SLA superado)'}`
    : '--';

  const partes = [
    { k: 'Personal', v: Number(acum.coste_personal_eur || 0), color: '#6366f1' },
    { k: 'Energia', v: Number(acum.coste_energia_eur || 0), color: '#22d3ee' },
    { k: 'Desgaste', v: Number(acum.coste_desgaste_eur || 0), color: '#f97316' },
    { k: 'Activacion', v: Number(acum.coste_activacion_eur || 0), color: '#a855f7' },
    { k: 'Prima respuesta', v: Number(acum.prima_respuesta_eur || 0), color: '#ef4444' },
  ];
  const suma = partes.reduce((a, p) => a + p.v, 0) || 1;
  const desgloseHtml = partes.map((p) => {
    const pct = (100 * p.v) / suma;
    return `<div class="cost-bar-row" title="${p.k}: ${p.v.toFixed(2)} ${cur}">
      <span class="cost-bar-label">${p.k}</span>
      <span class="cost-bar-track"><span class="cost-bar-fill" style="width:${pct.toFixed(1)}%;background:${p.color}"></span></span>
      <span class="cost-bar-value">${p.v.toFixed(2)}</span>
    </div>`;
  }).join('');

  const filaActual = enIntervencion ? `
    <div class="detail-row"><span class="label">Coste intervencion en curso</span>
      <span class="value">${Number(c.coste_intervencion_eur || 0).toFixed(2)} ${cur}</span></div>
    <div class="detail-row"><span class="label">Minutos facturados</span>
      <span class="value">${Number(actual.minutos_facturados || 0).toFixed(2)} min</span></div>
    <div class="detail-row"><span class="label">Tiempo respuesta</span>
      <span class="value">${trTxt}</span></div>
  ` : '';

  return `
    <div class="detail-row"><span class="label">Tarifa</span>
      <span class="value">${Number(c.coste_min_eur || 0).toFixed(2)} ${cur}/min · ${Number(c.coste_activacion_eur || 0).toFixed(2)} ${cur} activacion</span></div>
    <div class="detail-row"><span class="label">Dotacion</span>
      <span class="value">${c.dotacion ?? '--'} personas</span></div>
    ${filaActual}
    <div class="detail-row"><span class="label">Intervenciones cerradas</span>
      <span class="value">${c.intervenciones_realizadas ?? 0}</span></div>
    <div class="detail-row"><span class="label">Coste total acumulado (unidad)</span>
      <span class="value"><strong>${Number(c.coste_total_eur || 0).toFixed(2)} ${cur}</strong></span></div>
    <div class="cost-breakdown unit-breakdown">${desgloseHtml}</div>
  `;
}

function describirEspecializado(unidad) {
  const e = unidad.especializado || {};
  switch (unidad.tipo) {
    case 'policia':
      return [
        ['Riesgo dinamico', fmtNum(e.riesgo_dinamico, 2)],
        ['Protocolo', humanizar(PROTOCOLO_LABELS, e.protocolo_contencion)],
        ['Agentes operativos', e.agentes_operativos ?? '--'],
        ['Detenidos', e.detenidos ?? 0],
        ['Tiempo de contencion', `${fmtNum(e.tiempo_contencion_s, 0)} s`],
      ];
    case 'ambulancia': {
      const sv = (e.paciente && e.paciente.signos_vitales) || {};
      return [
        ['Nivel de soporte', _humanizarKey(e.nivel_soporte)],
        ['Oxigeno disponible', `${fmtNum(e.oxigeno_pct, 1)} %`],
        ['Paciente a bordo', e.paciente_a_bordo ? 'Si' : 'No'],
        ['Triage', humanizar(TRIAGE_LABELS, e.paciente?.triage)],
        ['Frecuencia cardiaca / SpO2', `${fmtNum(sv.fc, 0)} lpm / ${fmtNum(sv.spo2, 0)} %`],
        ['Alertas clinicas', (e.alertas_clinicas || []).map(_humanizarKey).join(', ') || 'Ninguna'],
        ['Reportes emitidos', e.reportes_emitidos ?? 0],
      ];
    }
    case 'bomberos':
      return [
        ['Rol', _humanizarKey(e.rol)],
        ['Agua disponible', `${fmtNum(e.agua_pct, 0)} %`],
        ['Espuma disponible', `${fmtNum(e.espuma_pct, 0)} %`],
        ['Tipo de incendio', humanizar(TIPO_INCENDIO_LABELS, e.tipo_incendio)],
        ['Escala', e.escala_desplegada ? 'Desplegada' : 'Recogida'],
        ['Control del fuego', fmtNum(e.control_fuego, 2)],
        ['Riesgo de reignicion', fmtNum(e.riesgo_reignicion, 2)],
      ];
    case 'proteccion_civil':
      return [
        ['Mision actual', humanizar(MISION_PC_LABELS, e.mision_actual)],
        ['Kits disponibles', e.kits_disponibles ?? '--'],
        ['Voluntarios activos', e.voluntarios_activos ?? '--'],
        ['Evacuados', e.evacuados_total ?? 0],
        ['Centros activos', e.centros_evacuacion_activos ?? 0],
        ['Indice de estabilidad', fmtNum(e.indice_estabilidad, 2)],
        ['Alertas emitidas', e.alertas_emitidas ?? 0],
      ];
    case 'dron':
      return [
        ['Modo de vuelo', humanizar(DRONE_MODE_LABELS, e.modo)],
        ['Altitud', `${fmtNum(e.altitud_m, 0)} m`],
        ['Bateria', `${fmtNum(e.bateria_pct, 0)} %`],
        ['Calidad del enlace', `${fmtNum(e.link_pct, 0)} %`],
        ['Imagenes capturadas', e.imagenes_capturadas ?? 0],
        ['Autonomia restante', `${fmtNum(e.autonomia_restante_min, 1)} min`],
        ['Objetivo', e.objetivo_bloqueado ? 'Bloqueado' : 'Sin objetivo'],
        ['Riesgo detectado', humanizar(DRONE_RIESGO_LABELS, e.riesgo_detectado, 'Ninguno')],
        ['Eventos detectados', e.eventos_detectados ?? 0],
      ];
    default:
      return [];
  }
}

window.PanelFlota = PanelFlota;
window.TIPO_META = TIPO_META;

/* Propaga el cambio de tema a todos los mapas abiertos */
window.addEventListener('tema-cambiado', (e) => {
  _panelInstances.forEach((p) => p.setTema && p.setTema(e.detail.tema));
});
window.fmtNum = fmtNum;
window.fmtETA = fmtETA;
window.fmtEUR = fmtEUR;
window.fmtSeconds = fmtSeconds;
window.describirEspecializado = describirEspecializado;
window.renderCosteUnidad = renderCosteUnidad;
window.etiquetaTipo = etiquetaTipo;
window.etiquetaEnergia = etiquetaEnergia;
window.etiquetaSeveridad = etiquetaSeveridad;
window.etiquetaIncidentStatus = etiquetaIncidentStatus;
window.etiquetaIncidentType = etiquetaIncidentType;
window.etiquetaEstadoVehiculo = etiquetaEstadoVehiculo;
