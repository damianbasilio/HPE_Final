

document.addEventListener('DOMContentLoaded', () => {
  let panel;
  try {
    panel = new window.PanelFlota({
      idMapa: 'mapa-flota',
      modo: 'operador',
      onSeleccion: (id) => renderDetalle(id),
    });
  } catch (err) {
    console.error('[operador] PanelFlota fallo, sigo sin mapa:', err);
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
    console.error('[operador] Socket.IO no disponible:', err);
    socket = { on: () => {}, emit: () => {} };
  }

  let estadoActual = { vehiculos: [], incidentes: [], costes: null };
  let datosRecibidos = false;

  socket.on('connect', () => actualizarConexion(true));
  socket.on('disconnect', () => actualizarConexion(false));

  setTimeout(() => {
    if (!datosRecibidos) {
      console.warn('[operador] Socket.IO sin datos en 3s, recurriendo a REST');
      cargarDatosRest();
      setInterval(cargarDatosRest, 5000);
    }
  }, 3000);

  async function cargarDatosRest() {
    try {
      const [resV, resI, resC] = await Promise.all([
        fetch('/_internal/vehicles'),
        fetch('/_internal/incidents'),
        fetch('/costs/summary'),
      ]);
      if (!resV.ok || !resI.ok) return;
      const dV = await resV.json();
      const dI = await resI.json();
      const dC = resC.ok ? await resC.json() : null;
      estadoActual = {
        vehiculos: Array.isArray(dV) ? dV : (dV.vehicles || dV.vehiculos || []),
        incidentes: Array.isArray(dI) ? dI : (dI.incidents || dI.incidentes || []),
        costes: dC,
      };
      panel.actualizarFlota(estadoActual);
      renderListaUnidades();
      renderListaIncidentes();
      renderResumen();
      renderCostes();
    } catch (err) {
      console.warn('[operador] cargarDatosRest fallo:', err);
    }
  }

  socket.on('estado_inicial', (data) => {
    datosRecibidos = true;
    estadoActual = {
      vehiculos: data.vehiculos || [],
      incidentes: data.incidentes || [],
      costes: data.costes || null,
    };
    panel.actualizarFlota(estadoActual);
    renderListaUnidades();
    renderListaIncidentes();
    renderResumen();
    renderCostes();
  });

  socket.on('actualizacion_flotas', (data) => {
    datosRecibidos = true;
    estadoActual = {
      vehiculos: data.vehiculos || [],
      incidentes: data.incidentes || [],
      costes: data.costes || null,
    };
    panel.actualizarFlota(estadoActual);
    renderListaUnidades();
    renderListaIncidentes();
    renderResumen();
    renderCostes();
    if (panel.seleccionado) renderDetalle(panel.seleccionado);
  });

  socket.on('control_resultado', (res) => {
    if (!res || res.ok === false) {
      mostrarFlash(`No se pudo ejecutar ${res?.accion || 'accion'}`, 'error');
    } else {
      mostrarFlash(`Accion ${res.accion} OK`, 'ok');
    }
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
    const totales = (estadoActual.costes && estadoActual.costes.totales) || {};
    const coste = totales.coste_total_eur != null
      ? Number(totales.coste_total_eur)
      : v.reduce((acc, u) => acc + ((u.costes && u.costes.coste_total_eur) || 0), 0);

    setText('flota-total', total);
    setText('flota-activos', activos);
    setText('flota-coste', coste.toFixed(2));
  }

  function renderCostes() {
    const cont = document.getElementById('panel-costes');
    if (!cont) return;
    if (!estadoActual.costes) {
      cont.innerHTML = '<p class="muted">Calculando coste operativo...</p>';
      return;
    }
    cont.innerHTML = window.renderResumenCostes(estadoActual.costes);
  }

  const formTarifa = document.getElementById('cost-rate-form');
  if (formTarifa) {
    formTarifa.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const feedback = document.getElementById('cost-rate-feedback');
      const datos = Object.fromEntries(new FormData(formTarifa).entries());
      const payload = {
        tipo: (datos.tipo || '').trim(),
        energia: (datos.energia || '').trim(),
        dotacion: parseInt(datos.dotacion, 10) || 0,
        coste_min: parseFloat(datos.coste_min) || 0,
        coste_activacion: parseFloat(datos.coste_activacion) || 0,
        velocidad_max: datos.velocidad_max ? parseInt(datos.velocidad_max, 10) : null,
      };
      try {
        const res = await fetch('/costs/rates', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const body = await res.json();
        if (!res.ok || body.error) {
          if (feedback) {
            feedback.textContent = body.error || 'Error registrando tarifa';
            feedback.className = 'cost-feedback error';
          }
          return;
        }
        if (feedback) {
          feedback.textContent = `Tarifa ${payload.tipo}/${payload.energia} guardada.`;
          feedback.className = 'cost-feedback ok';
        }
        formTarifa.reset();
        cargarDatosRest();
      } catch (err) {
        if (feedback) {
          feedback.textContent = 'Error de red al registrar la tarifa';
          feedback.className = 'cost-feedback error';
        }
      }
    });
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
      <h4>Costes</h4>
      ${renderCostesUnidad(u)}
      <h4>Especializado</h4>
      ${esp}
      ${incHtml}
      <div class="action-row">
        <button class="btn btn-primary" id="btn-cerrar-${u.id}" ${u.incidente ? '' : 'disabled'}>Cerrar incidente</button>
        <button class="btn btn-ghost" id="btn-asignar-${u.id}">Asignar manual</button>
      </div>
    `;

    const cerrar = document.getElementById(`btn-cerrar-${u.id}`);
    if (cerrar) cerrar.addEventListener('click', () => {
      socket.emit('control_incidente', { accion: 'cerrar', vehiculo_id: u.id });
    });
    const asignar = document.getElementById(`btn-asignar-${u.id}`);
    if (asignar) asignar.addEventListener('click', () => abrirDialogoAsignacion(u));
  }

  function renderCostesUnidad(u) {
    const c = u.costes || {};
    const actual = c.desglose_actual || {};
    const acum = c.desglose_acumulado || {};
    const filaTrSeg = actual.tiempo_respuesta_seg != null
      ? `${window.fmtSeconds(actual.tiempo_respuesta_seg)} ${actual.sla_cumplido ? '(SLA OK)' : '(SLA superado)'}`
      : '--';
    return `
      <div class="detail-row"><span class="label">Tarifa</span><span class="value">${Number(c.coste_min_eur || 0).toFixed(2)} EUR/min · ${Number(c.coste_activacion_eur || 0).toFixed(2)} EUR activacion</span></div>
      <div class="detail-row"><span class="label">Dotacion</span><span class="value">${c.dotacion ?? '--'} personas</span></div>
      <div class="detail-row"><span class="label">Minutos facturados</span><span class="value">${Number(actual.minutos_facturados || 0).toFixed(2)} min</span></div>
      <div class="detail-row"><span class="label">Coste actual</span><span class="value">${window.fmtEUR(c.coste_intervencion_eur)}</span></div>
      <div class="detail-row"><span class="label">Coste total acumulado</span><span class="value">${window.fmtEUR(c.coste_total_eur)}</span></div>
      <div class="detail-row"><span class="label">Tiempo respuesta</span><span class="value">${filaTrSeg}</span></div>
      <div class="detail-row"><span class="label">Personal (acum)</span><span class="value">${window.fmtEUR(acum.coste_personal_eur)}</span></div>
      <div class="detail-row"><span class="label">Energia (acum)</span><span class="value">${window.fmtEUR(acum.coste_energia_eur)}</span></div>
      <div class="detail-row"><span class="label">Desgaste (acum)</span><span class="value">${window.fmtEUR(acum.coste_desgaste_eur)}</span></div>
      <div class="detail-row"><span class="label">Activaciones (acum)</span><span class="value">${window.fmtEUR(acum.coste_activacion_eur)}</span></div>
      <div class="detail-row"><span class="label">Prima respuesta (acum)</span><span class="value">${window.fmtEUR(acum.prima_respuesta_eur)}</span></div>
    `;
  }

  function abrirDialogoAsignacion(unidad) {
    const titulo = prompt(`Titulo del incidente para ${unidad.nombre || unidad.id}:`, 'Aviso ciudadano');
    if (!titulo) return;
    const lat = parseFloat(prompt('Latitud destino:', '12.5211'));
    const lon = parseFloat(prompt('Longitud destino:', '-69.9683'));
    const sev = prompt('Severidad (low/medium/high/critical):', 'medium') || 'medium';
    if (isNaN(lat) || isNaN(lon)) {
      mostrarFlash('Coordenadas invalidas', 'error');
      return;
    }
    socket.emit('control_incidente', {
      accion: 'asignar',
      vehiculo_id: unidad.id,
      incidente: {
        title: titulo, lat, lon, severity: sev,
        incident_type: 'manual', description: 'Asignacion manual del operador'
      }
    });
  }

  

  document.querySelectorAll('[data-apoyo]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const tipo = btn.dataset.apoyo;
      const seleccion = panel.unidadActual();
      socket.emit('control_incidente', {
        accion: 'apoyo',
        tipo,
        vehiculo_id: seleccion ? seleccion.id : null,
      });
    });
  });

  const botonMensaje = document.getElementById('btn-mensaje-central');
  if (botonMensaje) {
    botonMensaje.addEventListener('click', () => {
      const mensaje = prompt('Mensaje a difundir a la flota:');
      if (!mensaje) return;
      socket.emit('control_incidente', { accion: 'mensaje', mensaje });
    });
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
