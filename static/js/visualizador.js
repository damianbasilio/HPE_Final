

document.addEventListener('DOMContentLoaded', () => {
  let panel;
  try {
    panel = new window.PanelFlota({
      idMapa: 'mapa-flota',
      modo: 'visualizador',
      onSeleccion: (id) => {
        renderDetalle(id);
        renderListaUnidades();
      },
    });
  } catch (err) {
    console.error('[visualizador] PanelFlota fallo, sigo sin mapa:', err);
    panel = {
      seleccionado: null,
      actualizarFlota: () => {},
      seleccionarUnidad: (id) => { panel.seleccionado = id; renderDetalle(id); },
      unidadActual: () => null,
      centrarEn: () => {},
    };
  }

  let socket;
  try {
    socket = io({ transports: ['websocket', 'polling'] });
  } catch (err) {
    console.error('[visualizador] Socket.IO no disponible:', err);
    socket = { on: () => {}, emit: () => {} };
  }

  // -------------------------------------------------------------------
  // Fuente activa: 'live' (estado real via socket/REST) o un sim_id replay.
  // Cuando se inicia un replay, dejamos de pintar el live y empezamos a
  // tirar del snapshot de la simulacion replay aislada.
  // -------------------------------------------------------------------
  let fuenteActiva = 'live';
  let simActiva = null;
  let pollSimTimer = null;
  let estadoActual = { vehiculos: [], incidentes: [], decisiones: [], factor_clima: 1.0, clima_actual: null };
  let datosRecibidos = false;

  socket.on('connect', () => actualizarConexion(true));
  socket.on('disconnect', () => actualizarConexion(false));

  setTimeout(() => {
    if (!datosRecibidos && fuenteActiva === 'live') {
      console.warn('[visualizador] Socket.IO sin datos en 3s, recurriendo a REST');
      cargarDatosRest();
      setInterval(() => { if (fuenteActiva === 'live') cargarDatosRest(); }, 5000);
    }
  }, 3000);

  async function cargarDatosRest() {
    try {
      const res = await fetch('/simulations/live/snapshot');
      if (!res.ok) return;
      const data = await res.json();
      aplicarSnapshot(data);
    } catch (err) {
      console.warn('[visualizador] cargarDatosRest fallo:', err);
    }
  }

  function aplicarSnapshot(data) {
    estadoActual = {
      vehiculos: data.vehiculos || [],
      incidentes: data.incidentes || [],
      decisiones: data.decisiones || [],
      factor_clima: data.factor_clima ?? 1.0,
      clima_actual: data.clima_actual || null,
      modo: data.modo,
      virtual_now: data.virtual_now,
      sim_id: data.sim_id,
    };
    panel.actualizarFlota(estadoActual);
    renderListaUnidades();
    renderListaIncidentes();
    renderResumen();
    renderDecisiones();
    renderClima();
    renderModo();
    if (panel.seleccionado) renderDetalle(panel.seleccionado);
  }

  socket.on('estado_inicial', (data) => {
    if (fuenteActiva !== 'live') return;
    datosRecibidos = true;
    aplicarSnapshot({
      vehiculos: data.vehiculos || [],
      incidentes: data.incidentes || [],
      decisiones: [],
      modo: 'tiempo_real',
      sim_id: 'live',
    });
  });

  socket.on('actualizacion_flotas', (data) => {
    if (fuenteActiva !== 'live') return;
    datosRecibidos = true;
    aplicarSnapshot({
      vehiculos: data.vehiculos || [],
      incidentes: data.incidentes || [],
      decisiones: estadoActual.decisiones,
      modo: 'tiempo_real',
      sim_id: 'live',
      // El broadcast ahora incluye factor_clima y clima_actual directamente
      factor_clima: data.factor_clima != null ? data.factor_clima : estadoActual.factor_clima,
      clima_actual: data.clima_actual || estadoActual.clima_actual,
    });
  });

  // Evento puntual cuando llega nueva lectura de clima desde Kafka
  socket.on('clima_actualizado', (data) => {
    if (fuenteActiva !== 'live') return;
    estadoActual.factor_clima = data.factor_clima ?? estadoActual.factor_clima;
    estadoActual.clima_actual = data.clima_actual || estadoActual.clima_actual;
    renderClima();
  });

  socket.on('mensaje_central', (data) => {
    mostrarFlash(`[${data.remitente}] ${data.mensaje}`, 'info', 6000);
  });

  function actualizarConexion(ok) {
    const dot = document.getElementById('ws-status-dot');
    if (!dot) return;
    dot.classList.toggle('connected', !!ok);
    dot.classList.toggle('disconnected', !ok);
    dot.title = ok ? 'Conectado' : 'Sin conexion';
  }

  function renderResumen() {
    const v = estadoActual.vehiculos;
    const total = v.length;
    const activos = v.filter((u) => u.escenario && u.escenario.en_progreso).length;
    setText('flota-total', total);
    setText('flota-activos', activos);
  }

  function renderModo() {
    const modo = (estadoActual.modo || 'tiempo_real').toUpperCase();
    setText('modo-actual', modo === 'TIEMPO_REAL' ? 'LIVE' : 'REPLAY');
    const cursor = estadoActual.virtual_now ? new Date(estadoActual.virtual_now).toLocaleString() : '--';
    setText('modo-cursor', cursor);
  }

  function renderClima() {
    const factor = estadoActual.factor_clima ?? 1.0;
    setText('factor-clima', `x${Number(factor).toFixed(2)}`);
    const clima = estadoActual.clima_actual;
    let desc = '--';
    if (clima && clima.condicion) {
      desc = `${clima.condicion.descripcion || ''} (${clima.condicion.condiciones_conduccion || ''})`;
    }
    setText('clima-desc', desc);
  }

  function renderDecisiones() {
    const cont = document.getElementById('decisiones-lista');
    if (!cont) return;
    const decisiones = (estadoActual.decisiones || []).slice(-30).reverse();
    if (!decisiones.length) {
      cont.innerHTML = '<p class="muted">Sin decisiones aun.</p>';
      return;
    }
    const decisionLabels = {
      asignado: 'Asignado',
      cola: 'En cola',
      descartado: 'Descartado',
      error: 'Error',
      ignorado: 'Ignorado',
    };
    cont.innerHTML = decisiones.map((d) => {
      const ts = d.ts ? new Date(d.ts).toLocaleTimeString() : '';
      const decision = (d.decision || '').toLowerCase();
      const cls = decision === 'asignado' ? 'ok'
        : decision === 'cola' ? 'warn'
        : decision === 'descartado' ? 'muted'
        : decision === 'error' ? 'error'
        : 'info';
      const tipoLabel = window.etiquetaIncidentType(d.tipo);
      const decisionLabel = decisionLabels[decision] || (d.decision ? d.decision.charAt(0).toUpperCase() + d.decision.slice(1) : '--');
      return `<div class="decision-row decision-${cls}">
        <div class="decision-head">
          <span class="decision-tipo">${tipoLabel}</span>
          <span class="decision-action">${decisionLabel}</span>
          <span class="decision-ts">${ts}</span>
        </div>
        <div class="decision-meta">${(d.motivo || '').slice(0, 160)}</div>
      </div>`;
    }).join('');
  }

  function _estadoUnidadTexto(u) {
    const esc = u.escenario || {};
    if (esc.en_camino) return 'En ruta a incidente';
    if (esc.en_escena) return 'Atendiendo en escena';
    if (esc.en_progreso) return u.incidente?.title || 'En intervencion';
    return window.etiquetaEstadoVehiculo(esc.activo);
  }

  function renderListaUnidades() {
    const cont = document.getElementById('unidades-lista');
    if (!cont) return;
    if (!estadoActual.vehiculos.length) {
      cont.innerHTML = '<div class="no-vehicles">Esperando flota...</div>';
      return;
    }
    cont.innerHTML = estadoActual.vehiculos.map((u) => {
      const meta = window.TIPO_META[u.tipo] || { letra: '?', color: '#94a3b8' };
      const activo = u.escenario && u.escenario.en_progreso;
      const sel = panel.seleccionado === u.id ? 'selected' : '';
      const eta = u.escenario?.en_camino ? window.fmtETA(u.escenario.eta_seg) : '';
      const incidente = u.incidente?.title ? `<span class="inc">${u.incidente.title}</span>` : '';
      return `<div class="vehicle-card ${sel}" data-id="${u.id}">
        <div class="header">
          <span class="dot" style="background:${meta.color}"></span>
          <span class="operador">${u.nombre || u.id}</span>
          <span class="status ${activo ? 'busy' : 'free'}">${_estadoUnidadTexto(u)}</span>
        </div>
        <div class="info">
          <span>${(u.velocidad || 0).toFixed(0)} km/h</span>
          <span>${(u.combustible || 0).toFixed(0)} %</span>
          <span>${eta}</span>
          ${incidente}
        </div>
      </div>`;
    }).join('');
    cont.querySelectorAll('.vehicle-card').forEach((card) => {
      card.addEventListener('click', () => {
        panel.seleccionarUnidad(card.dataset.id);
        if (panel.seleccionado) panel.centrarEn(card.dataset.id);
        renderListaUnidades();
      });
    });
  }

  function renderListaIncidentes() {
    const cont = document.getElementById('incidentes-lista');
    if (!cont) return;
    const inc = (estadoActual.incidentes || []).filter((i) =>
      i.status !== 'resolved' && i.status !== 'cancelled');
    if (!inc.length) {
      cont.innerHTML = '<div class="no-vehicles">Sin incidentes activos</div>';
      return;
    }
    cont.innerHTML = inc.map((i) => `
      <div class="incident-card sev-${i.severity || 'medium'}">
        <div class="incident-head">
          <strong>${i.title || window.etiquetaIncidentType(i.incident_type)}</strong>
          <span class="badge">${window.etiquetaSeveridad(i.severity || 'medium')}</span>
        </div>
        <div class="incident-meta">
          ${i.unidad_nombre || 'Sin unidad'} &middot; ETA ${window.fmtETA(i.eta_seg)} &middot; ${i.distancia_km || 0} km
        </div>
        <div class="incident-status">${window.etiquetaIncidentStatus(i.incident_status || i.status)}</div>
      </div>
    `).join('');
  }

  function renderDetalle(id) {
    const cont = document.getElementById('detalle-unidad');
    if (!cont) return;
    const u = id ? estadoActual.vehiculos.find((v) => v.id === id) : null;
    if (!u) {
      cont.innerHTML = '<p>Selecciona una unidad para ver detalles.</p>';
      return;
    }
    const esp = window.describirEspecializado(u).map(([k, v]) =>
      `<div class="detail-row"><span class="label">${k}</span><span class="value">${v}</span></div>`
    ).join('');

    const incHtml = u.incidente ? `
      <h4>Incidente asignado</h4>
      <div class="detail-row"><span class="label">Tipo</span><span class="value">${window.etiquetaIncidentType(u.incidente.incident_type)}</span></div>
      <div class="detail-row"><span class="label">Estado</span><span class="value">${window.etiquetaIncidentStatus(u.incidente.incident_status)}</span></div>
      <div class="detail-row"><span class="label">Tiempo estimado</span><span class="value">${window.fmtETA(u.incidente.eta_seg)}</span></div>
      <div class="detail-row"><span class="label">Severidad</span><span class="value">${window.etiquetaSeveridad(u.incidente.severity)}</span></div>
    ` : '';

    cont.innerHTML = `
      <h3>${u.nombre || u.id}</h3>
      <div class="detail-row"><span class="label">Tipo de unidad</span><span class="value">${window.etiquetaTipo(u.tipo)} &middot; ${window.etiquetaEnergia(u.propulsion)}</span></div>
      <div class="detail-row"><span class="label">Estado</span><span class="value">${_estadoUnidadTexto(u)}</span></div>
      <div class="detail-row"><span class="label">Velocidad</span><span class="value">${window.fmtNum(u.velocidad, 0)} km/h</span></div>
      <div class="detail-row"><span class="label">Combustible / Bateria</span><span class="value">${window.fmtNum(u.combustible, 1)} %</span></div>
      <div class="detail-row"><span class="label">Temperatura del motor</span><span class="value">${window.fmtNum(u.temperatura_motor, 0)} &deg;C</span></div>
      <div class="detail-row"><span class="label">Kilometros totales</span><span class="value">${window.fmtNum(u.km_totales, 0)} km</span></div>
      <div class="detail-row"><span class="label">Factor de entorno</span><span class="value">x${window.fmtNum(u.factor_entorno || 1, 2)}</span></div>
      <h4>Coste operativo</h4>
      ${window.renderCosteUnidad(u)}
      <h4>Telemetria especializada</h4>
      ${esp}
      ${incHtml}
    `;
  }

  // -------------------------------------------------------------------
  // Replay controls
  // -------------------------------------------------------------------

  const formReplay = document.getElementById('replay-form');
  if (formReplay) {
    formReplay.addEventListener('submit', async (e) => {
      e.preventDefault();
      const ts = document.getElementById('replay-ts').value;
      const tsEnd = document.getElementById('replay-end').value;
      const sp = parseFloat(document.getElementById('replay-speed').value || '5');
      const body = { started_at: ts, speed: sp };
      if (tsEnd) body.end_at = tsEnd;
      try {
        const res = await fetch('/simulations/replay', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await res.json();
        if (data.error) {
          mostrarFlash(`Replay: ${data.error}`, 'error');
          return;
        }
        simActiva = data;
        fuenteActiva = data.sim_id;
        habilitarControlesReplay(true);
        mostrarFlash(`Replay iniciado: ${data.sim_id}`, 'ok');
        startPollSim();
      } catch (err) {
        mostrarFlash('Error iniciando replay', 'error');
      }
    });
  }

  const botonPause = document.getElementById('replay-pause');
  if (botonPause) {
    botonPause.addEventListener('click', async () => {
      if (!simActiva) return;
      try {
        const res = await fetch(`/simulations/${simActiva.sim_id}/pause`, { method: 'POST' });
        simActiva = await res.json();
        renderEstadoSim();
        botonPause.textContent = simActiva.estado === 'paused' ? 'Reanudar' : 'Pausa';
      } catch (err) {
        mostrarFlash('Error al pausar/reanudar', 'error');
      }
    });
  }

  const botonStop = document.getElementById('replay-stop');
  if (botonStop) {
    botonStop.addEventListener('click', async () => {
      if (!simActiva) return;
      try {
        const res = await fetch(`/simulations/${simActiva.sim_id}/stop`, { method: 'POST' });
        simActiva = await res.json();
        renderEstadoSim();
        mostrarFlash('Replay detenida. Sigue pintandose hasta que vuelvas a LIVE.', 'info');
      } catch (err) {
        mostrarFlash('Error al detener replay', 'error');
      }
    });
  }

  const botonLive = document.getElementById('replay-live');
  if (botonLive) {
    botonLive.addEventListener('click', () => {
      if (simActiva) {
        // Limpia estado del replay (lo deja en backend para historico)
        fetch(`/simulations/${simActiva.sim_id}`, { method: 'DELETE' }).catch(() => {});
      }
      simActiva = null;
      fuenteActiva = 'live';
      habilitarControlesReplay(false);
      stopPollSim();
      mostrarFlash('Volviendo a tiempo real (LIVE)', 'ok');
      cargarDatosRest();
      const cont = document.getElementById('replay-estado');
      if (cont) cont.innerHTML = '<p class="muted">Mostrando estado en tiempo real (LIVE)</p>';
    });
  }

  const inputSpeed = document.getElementById('replay-set-speed');
  if (inputSpeed) {
    inputSpeed.addEventListener('change', async () => {
      if (!simActiva) return;
      const speed = parseFloat(inputSpeed.value);
      if (isNaN(speed)) return;
      try {
        const res = await fetch(`/simulations/${simActiva.sim_id}/speed`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ speed }),
        });
        simActiva = await res.json();
        renderEstadoSim();
      } catch (err) {
        mostrarFlash('Error cambiando velocidad', 'error');
      }
    });
  }

  function habilitarControlesReplay(activo) {
    const ids = ['replay-pause', 'replay-stop', 'replay-live'];
    ids.forEach((id) => {
      const btn = document.getElementById(id);
      if (btn) btn.disabled = !activo;
    });
  }

  function startPollSim() {
    stopPollSim();
    // Tick rapido para que la UI siga el reloj virtual del replay
    pollSimTimer = setInterval(async () => {
      if (!simActiva) return;
      try {
        const r = await fetch(`/simulations/${simActiva.sim_id}/snapshot?decisiones=80&eventos=20`);
        if (!r.ok) return;
        const snap = await r.json();
        simActiva = snap;
        if (fuenteActiva === simActiva.sim_id) {
          aplicarSnapshot(snap);
        }
        renderEstadoSim();
        if (snap.estado === 'finished' || snap.estado === 'error') {
          stopPollSim();
        }
      } catch (err) {
        // silencioso: la red puede tener fallos puntuales
      }
    }, 1000);
  }

  function stopPollSim() {
    if (pollSimTimer) {
      clearInterval(pollSimTimer);
      pollSimTimer = null;
    }
  }

  function renderEstadoSim() {
    const cont = document.getElementById('replay-estado');
    if (!cont) return;
    if (!simActiva) {
      cont.innerHTML = '<p class="muted">Mostrando estado en tiempo real (LIVE)</p>';
      return;
    }
    const cursor = simActiva.virtual_now ? new Date(simActiva.virtual_now).toLocaleString() : '--';
    const desde = simActiva.started_at ? new Date(simActiva.started_at).toLocaleString() : '--';
    const hasta = simActiva.end_at ? new Date(simActiva.end_at).toLocaleString() : '(presente)';
    const errorHtml = simActiva.consumer_error
      ? `<div class="replay-error">Consumer error: ${simActiva.consumer_error}</div>`
      : '';
    cont.innerHTML = `
      <div class="replay-row">
        <strong>${simActiva.sim_id}</strong>
        <span class="badge replay-badge-${simActiva.estado || ''}">${simActiva.estado || '--'}</span>
        <span>x${(simActiva.velocidad || 1).toFixed(2)}</span>
      </div>
      <div class="replay-row muted">
        ${desde} &rarr; ${hasta}
      </div>
      <div class="replay-row">
        Reloj virtual: <strong>${cursor}</strong>
      </div>
      <div class="replay-row muted">
        Eventos ${simActiva.eventos_procesados || 0} ·
        Clima ${simActiva.weather_procesados || 0} ·
        Buffer ${simActiva.buffer_pendiente || 0}
        ${simActiva.consumer_done ? '· consumer:done' : ''}
      </div>
      ${errorHtml}
    `;
  }

  // -------------------------------------------------------------------
  // Chat asistente
  // -------------------------------------------------------------------

  const chatForm = document.getElementById('chat-form');
  const chatInput = document.getElementById('chat-input');
  const chatBox = document.getElementById('chat-mensajes');

  function addChat(role, text) {
    if (!chatBox) return;
    const div = document.createElement('div');
    div.className = `chat-msg ${role}`;
    if (role === 'bot' && typeof marked !== 'undefined') {
      div.innerHTML = marked.parse(text);
    } else {
      div.textContent = text;
    }
    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;
  }

  if (chatForm && chatInput) {
    chatForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const pregunta = chatInput.value.trim();
      if (!pregunta) return;
      addChat('user', pregunta);
      chatInput.value = '';
      try {
        const res = await fetch(`/ask?q=${encodeURIComponent(pregunta)}`);
        const data = await res.json();
        addChat('bot', data.answer || 'No pude responder en este momento.');
      } catch (err) {
        addChat('bot', 'Error de conexion con el asistente.');
      }
    });
  }

  function setText(id, valor) {
    const el = document.getElementById(id);
    if (el) el.textContent = valor;
  }

  function mostrarFlash(texto, nivel = 'info', ms = 3500) {
    const div = document.createElement('div');
    div.className = `flash-msg flash-${nivel}`;
    div.textContent = texto;
    div.style.cssText = `position:fixed;top:80px;right:20px;background:#1a1a25;color:#f8fafc;padding:10px 16px;border-radius:8px;border-left:3px solid ${
      nivel === 'error' ? '#ef4444' : nivel === 'ok' ? '#10b981' : '#3b82f6'
    };z-index:10000;`;
    document.body.appendChild(div);
    setTimeout(() => div.remove(), ms);
  }
});
