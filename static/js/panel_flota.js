

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
  unico: 'Unico',
};

function etiquetaTipo(tipo) {
  return TIPO_LABELS[tipo] || tipo;
}

function etiquetaEnergia(en) {
  return ENERGIA_LABELS[en] || en;
}

function renderResumenCostes(resumen) {
  if (!resumen) return '';
  const t = resumen.totales || {};
  const d = resumen.desglose_acumulado || {};
  const sla = resumen.sla_respuesta || {};
  const tipos = resumen.por_tipo || [];
  const ultimas = resumen.ultimas_intervenciones || [];
  const cur = (resumen.currency || 'EUR');

  const totalAcum = Number(t.coste_total_eur || 0);
  const partes = [
    { k: 'Personal', v: Number(d.coste_personal_eur || 0), color: '#6366f1' },
    { k: 'Energia', v: Number(d.coste_energia_eur || 0), color: '#22d3ee' },
    { k: 'Desgaste', v: Number(d.coste_desgaste_eur || 0), color: '#f97316' },
    { k: 'Activacion', v: Number(d.coste_activacion_eur || 0), color: '#a855f7' },
    { k: 'Prima respuesta', v: Number(d.prima_respuesta_eur || 0), color: '#ef4444' },
  ];
  const sumaPartes = partes.reduce((a, p) => a + p.v, 0) || 1;

  const desgloseHtml = partes.map((p) => {
    const pct = (100 * p.v) / sumaPartes;
    return `<div class="cost-bar-row" title="${p.k}: ${p.v.toFixed(2)} ${cur} (${pct.toFixed(1)}%)">
      <span class="cost-bar-label">${p.k}</span>
      <span class="cost-bar-track"><span class="cost-bar-fill" style="width:${pct.toFixed(1)}%;background:${p.color}"></span></span>
      <span class="cost-bar-value">${p.v.toFixed(2)}</span>
    </div>`;
  }).join('');

  const tiposHtml = tipos.slice(0, 6).map((row) => `
    <div class="cost-type-row">
      <span class="cost-type-name">${etiquetaTipo(row.tipo)}</span>
      <span class="cost-type-units">${row.unidades || 0} u (${row.intervenciones || 0} int.)</span>
      <span class="cost-type-amount">${Number(row.coste_total_eur || 0).toFixed(2)} ${cur}</span>
    </div>
  `).join('') || '<p class="muted">Sin datos por tipo todavia.</p>';

  const ultimasHtml = ultimas.slice(0, 6).map((h) => {
    const sla = h.sla_cumplido ? '<span class="cost-sla ok">SLA</span>' : '<span class="cost-sla bad">SLA+</span>';
    const tr = h.tiempo_respuesta_seg != null ? fmtSeconds(h.tiempo_respuesta_seg) : '--';
    return `<div class="cost-history-row">
      <div class="row-top">
        <strong>${h.incident_id || '--'}</strong>
        <span class="badge">${h.severity || ''}</span>
        ${sla}
      </div>
      <div class="row-meta">
        ${etiquetaTipo(h.tipo_unidad)}/${etiquetaEnergia(h.propulsion)} ·
        ${Number(h.coste_total_eur || 0).toFixed(2)} ${cur} ·
        respuesta ${tr}
      </div>
    </div>`;
  }).join('') || '<p class="muted">Sin intervenciones cerradas todavia.</p>';

  const slaPct = sla.porcentaje_cumplido != null ? `${sla.porcentaje_cumplido}%` : '--';
  const slaTr = sla.tiempo_respuesta_medio_seg != null ? fmtSeconds(sla.tiempo_respuesta_medio_seg) : '--';

  return `
    <div class="cost-summary">
      <div class="cost-headline">
        <div>
          <span class="cost-label">Coste total acumulado</span>
          <span class="cost-amount">${totalAcum.toFixed(2)} ${cur}</span>
          <span class="cost-class clase-${t.clase || 'bajo'}">${(t.clase || '').toUpperCase()}</span>
        </div>
        <div>
          <span class="cost-label">Intervenciones</span>
          <span class="cost-amount-mini">${t.intervenciones_realizadas || 0} cerradas / ${t.intervenciones_en_curso || 0} en curso</span>
        </div>
        <div>
          <span class="cost-label">Coste medio / intervencion</span>
          <span class="cost-amount-mini">${Number(t.coste_medio_intervencion_eur || 0).toFixed(2)} ${cur}</span>
        </div>
        <div>
          <span class="cost-label">Cumplimiento SLA</span>
          <span class="cost-amount-mini">${slaPct} (medio ${slaTr})</span>
        </div>
      </div>

      <h4>Desglose acumulado</h4>
      <div class="cost-breakdown">${desgloseHtml}</div>

      <h4>Coste por tipo de unidad</h4>
      <div class="cost-types">${tiposHtml}</div>

      <h4>Ultimas intervenciones</h4>
      <div class="cost-history">${ultimasHtml}</div>

      <h4>Tarifas vigentes</h4>
      <div class="cost-rates" id="cost-rates-list">${renderListadoTarifas(resumen.tarifas, cur)}</div>
    </div>
  `;
}

function renderListadoTarifas(tarifas, cur = 'EUR') {
  if (!tarifas || !tarifas.length) return '<p class="muted">Sin tarifas disponibles.</p>';
  return `
    <table class="cost-rates-table">
      <thead>
        <tr><th>Recurso</th><th>Tipo</th><th>Dotacion</th><th>${cur}/min</th><th>Activacion</th><th>Estado</th></tr>
      </thead>
      <tbody>
        ${tarifas.map((tf) => `
          <tr class="${tf.bloqueada ? 'tarifa-bloqueada' : 'tarifa-personalizada'}">
            <td>${etiquetaTipo(tf.tipo)}</td>
            <td>${etiquetaEnergia(tf.energia)}</td>
            <td>${tf.dotacion ?? '--'}</td>
            <td>${Number(tf.coste_min || 0).toFixed(2)}</td>
            <td>${Number(tf.coste_activacion || 0).toFixed(2)}</td>
            <td>${tf.bloqueada ? 'bloqueada' : 'personalizada'}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
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
        ['Tiempo contencion', `${fmtNum(e.tiempo_contencion_s, 0)} s`],
      ];
    case 'ambulancia':
      const sv = (e.paciente && e.paciente.signos_vitales) || {};
      return [
        ['Soporte', e.nivel_soporte || '--'],
        ['Oxigeno', `${fmtNum(e.oxigeno_pct, 1)} %`],
        ['Paciente a bordo', e.paciente_a_bordo ? 'si' : 'no'],
        ['Triage', e.paciente?.triage || '--'],
        ['FC / SpO2', `${fmtNum(sv.fc, 0)} / ${fmtNum(sv.spo2, 0)}`],
        ['Alertas clinicas', (e.alertas_clinicas || []).join(', ') || '--'],
        ['Reportes', e.reportes_emitidos ?? 0],
      ];
    case 'bomberos':
      return [
        ['Rol', e.rol || '--'],
        ['Agua', `${fmtNum(e.agua_pct, 0)} %`],
        ['Espuma', `${fmtNum(e.espuma_pct, 0)} %`],
        ['Tipo incendio', e.tipo_incendio || '--'],
        ['Escala', e.escala_desplegada ? 'desplegada' : 'recogida'],
        ['Control fuego', fmtNum(e.control_fuego, 2)],
        ['Riesgo reignicion', fmtNum(e.riesgo_reignicion, 2)],
      ];
    case 'proteccion_civil':
      return [
        ['Mision', e.mision_actual || '--'],
        ['Kits disponibles', e.kits_disponibles ?? '--'],
        ['Voluntarios', e.voluntarios_activos ?? '--'],
        ['Evacuados', e.evacuados_total ?? 0],
        ['Centros activos', e.centros_evacuacion_activos ?? 0],
        ['Indice estabilidad', fmtNum(e.indice_estabilidad, 2)],
        ['Alertas emitidas', e.alertas_emitidas ?? 0],
      ];
    case 'dron':
      return [
        ['Modo', e.modo || '--'],
        ['Altitud (m)', fmtNum(e.altitud_m, 0)],
        ['Bateria', `${fmtNum(e.bateria_pct, 0)} %`],
        ['Link', `${fmtNum(e.link_pct, 0)} %`],
        ['Imagenes', e.imagenes_capturadas ?? 0],
        ['Autonomia', `${fmtNum(e.autonomia_restante_min, 1)} min`],
        ['Objetivo', e.objetivo_bloqueado ? 'bloqueado' : 'sin lock'],
        ['Riesgo detectado', e.riesgo_detectado || 'ninguno'],
        ['Eventos detectados', e.eventos_detectados ?? 0],
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
window.fmtSeconds = fmtSeconds;
window.describirEspecializado = describirEspecializado;
window.renderResumenCostes = renderResumenCostes;
window.renderListadoTarifas = renderListadoTarifas;
window.etiquetaTipo = etiquetaTipo;
window.etiquetaEnergia = etiquetaEnergia;
