document.addEventListener('DOMContentLoaded', () => {
  const canvas = document.getElementById('mapa-operador');
  const mapa = canvas ? new window.MapaAruba(canvas) : null;

  if (mapa) {
    fetch('/api/map')
      .then((r) => r.json())
      .then((data) => mapa.setData(data))
      .catch(() => {});
  }

  const socket = io();

  function actualizarUI(estado) {
    if (!estado) return;
    const gps = estado.gps || {};
    const energiaEl = document.getElementById('energia-valor');
    const velEl = document.getElementById('velocidad-valor');
    const tempEl = document.getElementById('temperatura-valor');
    const servEl = document.getElementById('estado-servicio');
    const incEl = document.getElementById('incidente-titulo');
    const costEl = document.getElementById('coste-acumulado');

    if (energiaEl) energiaEl.textContent = (estado.combustible ?? 0).toFixed(1);
    if (velEl) velEl.textContent = Math.round(estado.velocidad ?? 0);
    if (tempEl) tempEl.textContent = Math.round(estado.temperatura_motor ?? 0);
    if (servEl) servEl.textContent = estado.estado_servicio || 'disponible';
    if (incEl) incEl.textContent = estado.incidente?.title || 'Sin incidente';
    if (costEl) costEl.textContent = (estado.costos?.acumulado ?? 0).toFixed(2);

    if (mapa) {
      mapa.setVehicles([{ gps: { latitud: gps.latitud, longitud: gps.longitud }, estado_servicio: estado.estado_servicio }]);
    }
  }

  socket.on('estado_inicial', (data) => {
    actualizarUI(data.estado || data);
  });

  socket.on('estado_vehiculo', (data) => {
    actualizarUI(data.estado || data);
  });

  const cerrarBtn = document.getElementById('btn-cerrar-incidente');
  if (cerrarBtn) {
    cerrarBtn.addEventListener('click', () => {
      socket.emit('control_incidente', { accion: 'cerrar' });
    });
  }

  const apoyoBtns = document.querySelectorAll('[data-apoyo]');
  apoyoBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      socket.emit('control_incidente', { accion: 'apoyo', tipo: btn.dataset.apoyo });
    });
  });

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
        if (data.answer) {
          addChat('bot', data.answer);
        } else {
          addChat('bot', 'No pude responder en este momento.');
        }
      } catch (err) {
        addChat('bot', 'Error de conexion con el asistente.');
      }
    });
  }
});
