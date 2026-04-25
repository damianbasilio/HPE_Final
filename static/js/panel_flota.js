

const ARUBA_CENTRO = [12.5211, -69.9683];
const ARUBA_ZOOM = 12;

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
    this.marcadores = new Map();          
    this.rutas = new Map();               
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

    for (const id of [...this.marcadores.keys()]) {
      if (!idsFlota.has(id)) {
        this.mapa.removeLayer(this.marcadores.get(id));
        this.marcadores.delete(id);
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

  _dibujarRutaIncidente(unidad) {
    const inc = unidad.incidente;
    if (!inc || inc.lat == null || inc.lon == null) {
      const ruta = this.rutas.get(unidad.id);
      if (ruta) {
        this.mapa.removeLayer(ruta);
        this.rutas.delete(unidad.id);
      }
      return;
    }
    const gps = unidad.gps || {};
    if (gps.latitud == null || gps.longitud == null) return;
    const trazo = [[gps.latitud, gps.longitud], [inc.lat, inc.lon]];
    let ruta = this.rutas.get(unidad.id);
    if (!ruta) {
      ruta = L.polyline(trazo, {
        color: SEVERIDAD_COLOR[inc.severity] || '#f59e0b',
        weight: 2,
        opacity: 0.65,
        dashArray: '6, 8',
      }).addTo(this.mapa);
      this.rutas.set(unidad.id, ruta);
    } else {
      ruta.setLatLngs(trazo);
      ruta.setStyle({ color: SEVERIDAD_COLOR[inc.severity] || '#f59e0b' });
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
    this.seleccionado = id;
    if (typeof this.onSeleccion === 'function') {
      this.onSeleccion(id);
    }
  }

  unidadActual() {
    if (!this.seleccionado) return null;
    return this.flota.find((v) => v.id === this.seleccionado) || null;
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

function describirEspecializado(unidad) {
  const e = unidad.especializado || {};
  switch (unidad.tipo) {
    case 'policia':
      return [
        ['Riesgo dinamico', fmtNum(e.riesgo_dinamico, 2)],
        ['Protocolo', e.protocolo_contencion || '--'],
        ['Agentes', e.agentes_operativos ?? '--'],
        ['Detenidos', e.detenidos ?? 0],
      ];
    case 'ambulancia':
      const sv = (e.paciente && e.paciente.signos_vitales) || {};
      return [
        ['Soporte', e.nivel_soporte || '--'],
        ['Oxigeno', `${fmtNum(e.oxigeno_pct, 1)} %`],
        ['Paciente a bordo', e.paciente_a_bordo ? 'si' : 'no'],
        ['Triage', e.paciente?.triage || '--'],
        ['FC / SpO2', `${fmtNum(sv.fc, 0)} / ${fmtNum(sv.spo2, 0)}`],
      ];
    case 'bomberos':
      return [
        ['Rol', e.rol || '--'],
        ['Agua', `${fmtNum(e.agua_pct, 0)} %`],
        ['Espuma', `${fmtNum(e.espuma_pct, 0)} %`],
        ['Tipo incendio', e.tipo_incendio || '--'],
        ['Escala', e.escala_desplegada ? 'desplegada' : 'recogida'],
      ];
    case 'proteccion_civil':
      return [
        ['Mision', e.mision_actual || '--'],
        ['Kits disponibles', e.kits_disponibles ?? '--'],
        ['Voluntarios', e.voluntarios_activos ?? '--'],
        ['Evacuados', e.evacuados_total ?? 0],
      ];
    case 'dron':
      return [
        ['Modo', e.modo || '--'],
        ['Altitud (m)', fmtNum(e.altitud_m, 0)],
        ['Bateria', `${fmtNum(e.bateria_pct, 0)} %`],
        ['Link', `${fmtNum(e.link_pct, 0)} %`],
        ['Imagenes', e.imagenes_capturadas ?? 0],
        ['Autonomia', `${fmtNum(e.autonomia_restante_min, 1)} min`],
      ];
    default:
      return [];
  }
}

window.PanelFlota = PanelFlota;
window.TIPO_META = TIPO_META;
window.fmtNum = fmtNum;
window.fmtETA = fmtETA;
window.fmtEUR = fmtEUR;
window.describirEspecializado = describirEspecializado;
