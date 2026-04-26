

document.addEventListener('DOMContentLoaded', () => {
  let panel;
  try {
    panel = new window.PanelFlota({
      idMapa: 'mapa-flota',
      modo: 'operador',
      onSeleccion: (id) => {
        renderDetalle(id);
        renderListaUnidades();
      },
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

  let estadoActual = { vehiculos: [], incidentes: [] };
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
      const [resV, resI] = await Promise.all([
        fetch('/_internal/vehicles'),
        fetch('/_internal/incidents'),
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
      if (panel.seleccionado) renderDetalle(panel.seleccionado);
    } catch (err) {
      console.warn('[operador] cargarDatosRest fallo:', err);
    }
  }

  socket.on('estado_inicial', (data) => {
    datosRecibidos = true;
    estadoActual = {
      vehiculos: data.vehiculos || [],
      incidentes: data.incidentes || [],
    };
    panel.actualizarFlota(estadoActual);
    renderListaUnidades();
    renderListaIncidentes();
    renderResumen();
  });

  socket.on('actualizacion_flotas', (data) => {
    datosRecibidos = true;
    estadoActual = {
      vehiculos: data.vehiculos || [],
      incidentes: data.incidentes || [],
      factor_clima: data.factor_clima != null ? data.factor_clima : (estadoActual.factor_clima ?? 1.0),
      clima_actual: data.clima_actual || estadoActual.clima_actual || {},
    };
    panel.actualizarFlota(estadoActual);
    renderListaUnidades();
    renderListaIncidentes();
    renderResumen();
    if (panel.seleccionado) renderDetalle(panel.seleccionado);
    _renderClimaOperador();
  });

  // Actualizar tarjeta de clima del operador cuando llega nueva lectura Kafka
  socket.on('clima_actualizado', (data) => {
    estadoActual.factor_clima = data.factor_clima ?? estadoActual.factor_clima;
    estadoActual.clima_actual = data.clima_actual || estadoActual.clima_actual;
    _renderClimaOperador();
  });

  function _renderClimaOperador() {
    const elFactor = document.getElementById('factor-clima');
    const elDesc = document.getElementById('clima-desc');
    if (elFactor) elFactor.textContent = `x${Number(estadoActual.factor_clima ?? 1).toFixed(2)}`;
    if (elDesc) {
      const c = estadoActual.clima_actual || {};
      const cond = c.condicion || {};
      elDesc.textContent = cond.descripcion ? `${cond.descripcion} (${cond.condiciones_conduccion || ''})` : '--';
    }
  }

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
    setText('flota-total', total);
    setText('flota-activos', activos);
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
      <div class="action-row">
        <button class="btn btn-primary" id="btn-cerrar-${u.id}" ${u.incidente ? '' : 'disabled'}>Cerrar incidente</button>
        <button class="btn btn-ghost" id="btn-asignar-${u.id}">Asignar manual</button>
        <button class="btn btn-danger" id="btn-eliminar-${u.id}" title="Retirar esta unidad de la flota">Eliminar unidad</button>
      </div>
    `;

    const cerrar = document.getElementById(`btn-cerrar-${u.id}`);
    if (cerrar) cerrar.addEventListener('click', () => {
      socket.emit('control_incidente', { accion: 'cerrar', vehiculo_id: u.id });
    });
    const asignar = document.getElementById(`btn-asignar-${u.id}`);
    if (asignar) asignar.addEventListener('click', () => abrirDialogoAsignacion(u));
    const eliminar = document.getElementById(`btn-eliminar-${u.id}`);
    if (eliminar) eliminar.addEventListener('click', () => eliminarUnidad(u));
  }

  async function eliminarUnidad(unidad) {
    const confirma = confirm(`Eliminar la unidad ${unidad.nombre || unidad.id}? Esta accion la retira de la flota.`);
    if (!confirma) return;
    try {
      const res = await fetch(`/fleet/units/${encodeURIComponent(unidad.id)}`, {
        method: 'DELETE',
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        mostrarFlash(data.error || 'No se pudo eliminar la unidad', 'error');
        return;
      }
      mostrarFlash(`Unidad ${unidad.nombre || unidad.id} eliminada`, 'ok');
      panel.seleccionarUnidad(null);
      cargarDatosRest();
    } catch (err) {
      mostrarFlash('Error de red eliminando unidad', 'error');
    }
  }

  // Boton "Eliminar seleccionada" de la barra superior
  const botonRemoveUnidad = document.getElementById('btn-remove-unit');
  if (botonRemoveUnidad) {
    botonRemoveUnidad.addEventListener('click', () => {
      const seleccion = panel.unidadActual();
      if (!seleccion) {
        mostrarFlash('Selecciona primero una unidad en la lista o el mapa', 'info');
        return;
      }
      eliminarUnidad(seleccion);
    });
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

  

  const botonMensaje = document.getElementById('btn-mensaje-central');
  if (botonMensaje) {
    botonMensaje.addEventListener('click', () => {
      const mensaje = prompt('Mensaje a difundir a la flota:');
      if (!mensaje) return;
      socket.emit('control_incidente', { accion: 'mensaje', mensaje });
    });
  }

  // Boton "+ unidad": agrega un vehiculo permanente a la flota (sin limite).
  // Usa la ruta REST /fleet/units (protegida con requerir_operador).
  const botonAddUnidad = document.getElementById('btn-add-unit');
  if (botonAddUnidad) {
    botonAddUnidad.addEventListener('click', async () => {
      const tipos = ['policia', 'ambulancia', 'bomberos', 'proteccion_civil', 'dron'];
      const tipo = (prompt(`Tipo de unidad nueva (${tipos.join(' / ')}):`, 'policia') || '').trim().toLowerCase();
      if (!tipo) return;
      if (!tipos.includes(tipo)) {
        mostrarFlash(`Tipo no valido: ${tipo}`, 'error');
        return;
      }
      const propulsionDef = tipo === 'dron' ? 'unico' : 'combustion';
      const propulsion = (prompt('Propulsion (combustion / electrico / unico):', propulsionDef) || propulsionDef).trim().toLowerCase();
      const nombre = (prompt('Nombre identificativo (opcional, deja vacio para autogenerar):', '') || '').trim() || null;
      try {
        const res = await fetch('/fleet/units', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tipo, propulsion, nombre }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          mostrarFlash(data.error || 'Error agregando unidad', 'error');
          return;
        }
        const unidadInfo = data.unit || data;
        mostrarFlash(`Unidad agregada: ${unidadInfo.nombre || unidadInfo.id}`, 'ok');
        cargarDatosRest();
      } catch (err) {
        mostrarFlash('Error de red agregando unidad', 'error');
      }
    });
  }

  

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
