document.addEventListener('DOMContentLoaded', () => {
  const ctxEl = document.getElementById('contexto-ciudadano');
  const eventosEl = document.getElementById('eventos-ciudadano');

  async function cargarContexto() {
    try {
      const res = await fetch('/api/context');
      const data = await res.json();
      if (ctxEl) {
        const clima = data.clima?.condicion?.descripcion || 'sin datos';
        ctxEl.textContent = `Clima: ${clima} | Eventos: ${(data.eventos || []).length}`;
      }
      if (eventosEl) {
        eventosEl.innerHTML = (data.eventos || []).slice(0, 6).map((e) => {
          return `<div class="event-item">
            <strong>${e.title || e.type}</strong>
            <span>${e.severity || ''}</span>
          </div>`;
        }).join('') || '<div class="event-item">Sin eventos activos</div>';
      }
    } catch (err) {
    }
  }

  cargarContexto();
  setInterval(cargarContexto, 15000);

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
