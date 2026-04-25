document.addEventListener('DOMContentLoaded', () => {
  const canvas = document.getElementById('mapa-visualizador');
  const mapa = canvas ? new window.MapaAruba(canvas) : null;
  let flota = [];
  let seleccionado = null;

  if (mapa) {
    fetch('/api/map')
      .then((r) => r.json())
      .then((data) => mapa.setData(data))
      .catch(() => {});
  }

  const socket = io();

  socket.on('actualizacion_flotas', (data) => {
    flota = data.vehiculos || [];
    if (mapa) mapa.setVehicles(flota);
    renderLista();
    if (seleccionado) {
      const v = flota.find((x) => x.id === seleccionado);
      if (v) renderDetalle(v);
    }
  });

  function renderLista() {
    const cont = document.getElementById('vehicle-list');
    if (!cont) return;
    if (!flota.length) {
      cont.innerHTML = '<div class="no-vehicles">Esperando unidades...</div>';
      return;
    }
    cont.innerHTML = flota.map((v) => {
      const active = seleccionado === v.id ? 'selected' : '';
      return `<div class="vehicle-card ${active}" data-id="${v.id}">
        <div class="header">
          <span class="operador">${v.nombre || v.id}</span>
          <span class="status">${v.estado_servicio || 'disponible'}</span>
        </div>
        <div class="info">
          <span>${(v.velocidad || 0).toFixed(0)} km/h</span>
          <span>${(v.combustible || 0).toFixed(0)}%</span>
        </div>
      </div>`;
    }).join('');

    cont.querySelectorAll('.vehicle-card').forEach((card) => {
      card.addEventListener('click', () => {
        seleccionado = card.dataset.id;
        const v = flota.find((x) => x.id === seleccionado);
        if (v) renderDetalle(v);
        renderLista();
      });
    });
  }

  function renderDetalle(v) {
    const cont = document.getElementById('details-content');
    if (!cont) return;
    const gps = v.gps || {};
    cont.innerHTML = `
      <div class="detail-section">
        <h3>${v.nombre || v.id}</h3>
        <div class="detail-row"><span class="label">Tipo</span><span class="value">${v.tipo}</span></div>
        <div class="detail-row"><span class="label">Estado</span><span class="value">${v.estado_servicio}</span></div>
        <div class="detail-row"><span class="label">Velocidad</span><span class="value">${(v.velocidad || 0).toFixed(1)} km/h</span></div>
        <div class="detail-row"><span class="label">Energia</span><span class="value">${(v.combustible || 0).toFixed(1)}%</span></div>
        <div class="detail-row"><span class="label">GPS</span><span class="value">${(gps.latitud || 0).toFixed(5)}, ${(gps.longitud || 0).toFixed(5)}</span></div>
      </div>
    `;
  }
});
