

document.addEventListener('DOMContentLoaded', () => {
  let panel;
  try {
    panel = new window.PanelFlota({
      idMapa: 'mapa-flota',
      modo: 'visualizador',
      onSeleccion: (id) => renderDetalle(id),
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

  let estadoActual = { vehiculos: [], incidentes: [] };
  let simActiva = null;
  let pollSimTimer = null;
  let datosRecibidos = false;

  socket.on('connect', () => actualizarConexion(true));
  socket.on('disconnect', () => actualizarConexion(false));

  setTimeout(() => {
    if (!datosRecibidos) {
      console.warn('[visualizador] Socket.IO sin datos en 3s, recurriendo a REST');
      cargarDatosRest();
      setInterval(cargarDatosRest, 5000);
    }
  }, 3000);

  async function cargarDatosRest() {
    try {
      const [resV, resI] = await Promise.all([
        fetch('/vehicles'),
        fetch('/incidents'),
      ]);
      if (!resV.ok || !resI.ok) return;
      const dV = await resV.json();
      const dI = await resI.json();
      estadoActual = {
        vehiculos: Array.isArray(dV) ? dV : (dV.vehicles || dV.vehiculos || []),
        incidentes: Array.isArray(dI) ? dI : (dI.incidents || dI.incidentes || []),
      };
      panel.actualizarFlota(estadoActual);
      renderListaUnidades();
      renderListaIncidentes();
      renderResumen();
    } catch (err) {
      console.warn('[visualizador] cargarDatosRest fallo:', err);
    }
  }

  socket.on('estado_inicial', (data) => {
    datosRecibidos = true;
    estadoActual = { vehiculos: data.vehiculos || [], incidentes: data.incidentes || [] };
    panel.actualizarFlota(estadoActual);
    renderListaUnidades();
    renderListaIncidentes();
    renderResumen();
  });

  socket.on('actualizacion_flotas', (data) => {
    datosRecibidos = true;
    estadoActual = { vehiculos: data.vehiculos || [], incidentes: data.incidentes || [] };
    panel.actualizarFlota(estadoActual);
    renderListaUnidades();
    renderListaIncidentes();
    renderResumen();
    if (panel.seleccionado) renderDetalle(panel.seleccionado);
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
    const coste = v.reduce((acc, u) => acc + ((u.costes && u.costes.coste_total_eur) || 0), 0);
    setText('flota-total', total);
    setText('flota-activos', activos);
    setText('flota-coste', coste.toFixed(2));
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
          <span class="status ${activo ? 'busy' : 'free'}">${u.escenario?.activo || '--'}</span>
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
        panel.centrarEn(card.dataset.id);
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
          <strong>${i.title || i.incident_type}</strong>
          <span class="badge">${i.severity || 'medium'}</span>
        </div>
        <div class="incident-meta">
          ${i.unidad_nombre || '--'} | ETA ${window.fmtETA(i.eta_seg)} | ${i.distancia_km || 0} km
        </div>
        <div class="incident-status">${i.incident_status || i.status || '--'}</div>
      </div>
    `).join('');
  }

  function renderDetalle(id) {
    const cont = document.getElementById('detalle-unidad');
    if (!cont) return;
    const u = estadoActual.vehiculos.find((v) => v.id === id);
    if (!u) {
      cont.innerHTML = '<p>Selecciona una unidad para ver detalles.</p>';
      return;
    }
    const esp = window.describirEspecializado(u).map(([k, v]) =>
      `<div class="detail-row"><span class="label">${k}</span><span class="value">${v}</span></div>`
    ).join('');

    const incHtml = u.incidente ? `
      <h4>Incidente</h4>
      <div class="detail-row"><span class="label">Tipo</span><span class="value">${u.incidente.incident_type || '--'}</span></div>
      <div class="detail-row"><span class="label">Estado</span><span class="value">${u.incidente.incident_status}</span></div>
      <div class="detail-row"><span class="label">ETA</span><span class="value">${window.fmtETA(u.incidente.eta_seg)}</span></div>
      <div class="detail-row"><span class="label">Severidad</span><span class="value">${u.incidente.severity}</span></div>
    ` : '';

    cont.innerHTML = `
      <h3>${u.nombre || u.id}</h3>
      <div class="detail-row"><span class="label">Tipo</span><span class="value">${u.tipo}/${u.propulsion}</span></div>
      <div class="detail-row"><span class="label">Velocidad</span><span class="value">${window.fmtNum(u.velocidad, 0)} km/h</span></div>
      <div class="detail-row"><span class="label">Combustible</span><span class="value">${window.fmtNum(u.combustible, 1)} %</span></div>
      <div class="detail-row"><span class="label">Temperatura</span><span class="value">${window.fmtNum(u.temperatura_motor, 0)} C</span></div>
      <div class="detail-row"><span class="label">Km totales</span><span class="value">${window.fmtNum(u.km_totales, 0)}</span></div>
      <div class="detail-row"><span class="label">Factor entorno</span><span class="value">x${window.fmtNum(u.factor_entorno || 1, 2)}</span></div>
      <div class="detail-row"><span class="label">Coste total</span><span class="value">${window.fmtEUR(u.costes?.coste_total_eur)}</span></div>
      <h4>Especializado</h4>
      ${esp}
      ${incHtml}
    `;
  }

  

  const formReplay = document.getElementById('replay-form');
  if (formReplay) {
    formReplay.addEventListener('submit', async (e) => {
      e.preventDefault();
      const ts = document.getElementById('replay-ts').value;
      const sp = parseFloat(document.getElementById('replay-speed').value || '5');
      try {
        const res = await fetch('/simulations/replay', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ started_at: ts, speed: sp }),
        });
        const data = await res.json();
        if (data.error) {
          mostrarFlash(`Replay: ${data.error}`, 'error');
          return;
        }
        simActiva = data;
        renderEstadoSim();
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
      const res = await fetch(`/simulations/${simActiva.sim_id}/pause`, { method: 'POST' });
      simActiva = await res.json();
      renderEstadoSim();
    });
  }

  const inputSpeed = document.getElementById('replay-set-speed');
  if (inputSpeed) {
    inputSpeed.addEventListener('change', async () => {
      if (!simActiva) return;
      const speed = parseFloat(inputSpeed.value);
      if (isNaN(speed)) return;
      const res = await fetch(`/simulations/${simActiva.sim_id}/speed`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ speed }),
      });
      simActiva = await res.json();
      renderEstadoSim();
    });
  }

  function startPollSim() {
    if (pollSimTimer) clearInterval(pollSimTimer);
    pollSimTimer = setInterval(async () => {
      if (!simActiva) return;
      const r = await fetch(`/simulations/${simActiva.sim_id}/state`);
      if (!r.ok) return;
      simActiva = await r.json();
      renderEstadoSim();
      if (simActiva.estado === 'finished') {
        clearInterval(pollSimTimer);
        pollSimTimer = null;
      }
    }, 2000);
  }

  function renderEstadoSim() {
    const cont = document.getElementById('replay-estado');
    if (!cont) return;
    if (!simActiva) {
      cont.innerHTML = '<p class="muted">Sin replay activo</p>';
      return;
    }
    cont.innerHTML = `
      <div><strong>${simActiva.sim_id}</strong> · ${simActiva.modo}</div>
      <div>Estado: <strong>${simActiva.estado}</strong></div>
      <div>Velocidad: x${simActiva.velocidad}</div>
      <div>Cursor: ${simActiva.cursor || '--'}</div>
      <div>Eventos: ${simActiva.eventos_procesados || 0}</div>
    `;
  }

  

  const chatForm = document.getElementById('chat-form');
  const chatInput = document.getElementById('chat-input');
  const chatBox = document.getElementById('chat-mensajes');

  function addChat(role, text) {
    if (!chatBox) return;
    const div = document.createElement('div');
    div.className = `chat-msg ${role}`;
    div.textContent = text;
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
