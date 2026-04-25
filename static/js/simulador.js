// Simulador - Renderizador (simulacion en servidor)
// Solo renderiza el estado recibido del servidor.
// Toda la logica de simulacion corre en el servidor.

const CONFIGURACION = {
  CENTRO_MADRID: [40.4168, -3.7038],
  INTERVALO_SERVIDOR: 100
};

class SimuladorRenderer {
  constructor() {
    // Objetos del mapa
    this.mapa = null;
    this.marcadorVehiculo = null;
    this.lineaRuta = null;
    this.capaRastro = null;

    // Posicion actual (interpolada para suavidad)
    this.posActual = [...CONFIGURACION.CENTRO_MADRID];

    // Estado de interpolacion
    this.posAnterior = [...CONFIGURACION.CENTRO_MADRID];
    this.posObjetivo = [...CONFIGURACION.CENTRO_MADRID];
    this.ultimaActualizacion = 0;

    // Estado del vehiculo (recibido del servidor)
    this.velocidad = 0;
    this.combustible = 75;
    this.temperatura = 70;
    this.kilometraje = 15000;
    this.aceite = 90;
    this.desgasteFreno = 30;
    this.desgasteLlanta = 35;

    // Estado del escenario
    this.escenarioActivo = null;
    this.nombreEscenario = null;
    this.escenarioEnProgreso = false;
    this.distanciaRecorrida = 0;

    // Rastro (recibido del servidor)
    this.rastro = [];

    // Ruta
    this.ruta = [];
    this.progresoRuta = 0;
    this.indiceRuta = 0;

    // Animacion
    this.animacionId = null;
    this.ultimoRender = 0;
  }

  // Inicializar el mapa de Leaflet
  inicializarMapa(idElemento) {
    this.mapa = L.map(idElemento, {
      zoomControl: true,
      attributionControl: false
    }).setView(CONFIGURACION.CENTRO_MADRID, 14);

    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      maxZoom: 19
    }).addTo(this.mapa);

    const iconoVehiculo = L.divIcon({
      className: 'icono-vehiculo-punto',
      html: '<div class="punto-vehiculo"><div class="punto-interior"></div></div>',
      iconSize: [24, 24],
      iconAnchor: [12, 12]
    });

    this.marcadorVehiculo = L.marker(CONFIGURACION.CENTRO_MADRID, { icon: iconoVehiculo }).addTo(this.mapa);
  }

  // Llamado por SocketClient cuando el servidor envia estado
  actualizarDesdeServidor(data) {
    const estado = data.estado;
    if (!estado) return;

    const gps = estado.gps;
    if (!gps) return;

    // Interpolacion: guardar posicion anterior
    this.posAnterior = [...this.posObjetivo];
    this.posObjetivo = [gps.latitud, gps.longitud];
    this.ultimaActualizacion = performance.now();

    // Actualizar estado del vehiculo
    this.velocidad = estado.velocidad || 0;
    this.combustible = estado.combustible || 0;
    this.temperatura = estado.temperatura_motor || 0;
    this.kilometraje = estado.km_totales || 0;
    this.aceite = estado.nivel_aceite || 0;
    this.desgasteFreno = estado.desgaste_frenos || 0;
    this.desgasteLlanta = estado.desgaste_neumaticos || 0;

    // Estado del escenario
    const esc = estado.escenario;
    if (esc) {
      const activo = esc.activo;
      this.escenarioActivo = (activo && activo.toLowerCase() !== 'patrulla') ? activo : null;
      this.escenarioEnProgreso = esc.en_progreso || false;
      this.distanciaRecorrida = esc.distancia_recorrida || 0;
    }

    // Progreso de ruta
    if (gps.progreso_ruta !== undefined) {
      this.progresoRuta = gps.progreso_ruta;
    }
    if (gps.indice_ruta !== undefined) {
      this.indiceRuta = gps.indice_ruta;
    }

    // Ruta (solo se envia cuando cambia)
    if (data.ruta !== undefined) {
      // Solo mostrar ruta durante escenarios, no patrulla
      const enEscenario = this.escenarioActivo && this.escenarioEnProgreso;
      this.ruta = data.ruta || [];
      if (enEscenario && this.ruta.length > 1) {
        this.dibujarRuta();
      } else if (!enEscenario) {
        this.ocultarRuta();
      }
    }

    // Si el escenario termino, ocultar la ruta
    if (!this.escenarioEnProgreso) {
      this.ocultarRuta();
    }

    // Rastro del servidor
    if (data.rastro && data.rastro.length > 0) {
      this.rastro = data.rastro;
    }
  }

  // Actualizar ruta en el mapa (solo durante escenarios activos)
  actualizarRuta(ruta, forzarMostrar = false) {
    this.ruta = ruta || [];
    if (forzarMostrar || this.escenarioEnProgreso) {
      if (this.ruta.length > 1) {
        this.dibujarRuta();
      } else {
        this.ocultarRuta();
      }
    } else {
      this.ocultarRuta();
    }
  }

  // Iniciar loop de renderizado (60fps)
  iniciarLoop() {
    this.ultimoRender = performance.now();
    this.render();
  }

  render() {
    const ahora = performance.now();

    // Interpolar posicion entre actualizaciones del servidor
    const transcurrido = ahora - this.ultimaActualizacion;
    const t = Math.min(1, transcurrido / CONFIGURACION.INTERVALO_SERVIDOR);

    this.posActual[0] = this.posAnterior[0] + (this.posObjetivo[0] - this.posAnterior[0]) * t;
    this.posActual[1] = this.posAnterior[1] + (this.posObjetivo[1] - this.posAnterior[1]) * t;

    // Solo actualizar visuales si la pestana esta visible
    if (!document.hidden) {
      this.actualizarMapa();

      // Limitar UI a ~10fps
      if (ahora - this.ultimoRender > 100) {
        this.actualizarUI();
        this.ultimoRender = ahora;
      }
    }

    this.animacionId = requestAnimationFrame(() => this.render());
  }

  // Actualizar mapa
  actualizarMapa() {
    if (!this.mapa || !this.marcadorVehiculo) return;

    const pos = this.posActual;
    this.marcadorVehiculo.setLatLng(pos);

    // Rastro
    this.actualizarRastroVisual();

    // Ruta restante
    this.actualizarLineaRuta();

    // Centrar camara
    this.mapa.panTo(pos, { animate: false });
  }

  actualizarRastroVisual() {
    if (this.rastro.length < 2) {
      if (this.capaRastro) {
        this.mapa.removeLayer(this.capaRastro);
        this.capaRastro = null;
      }
      return;
    }

    const color = this.obtenerColorRastro();

    if (this.capaRastro) {
      this.capaRastro.setLatLngs(this.rastro);
      this.capaRastro.setStyle({ color });
    } else {
      this.capaRastro = L.polyline(this.rastro, {
        color,
        weight: 3,
        opacity: 0.7,
        lineCap: 'round',
        lineJoin: 'round',
        smoothFactor: 1.5
      }).addTo(this.mapa);
    }

    this.actualizarMarcadorEscenario();
  }

  obtenerColorRastro() {
    return (this.escenarioActivo && this.escenarioEnProgreso) ? '#f87171' : '#60a5fa';
  }

  actualizarMarcadorEscenario() {
    if (!this.marcadorVehiculo) return;
    const iconEl = this.marcadorVehiculo.getElement();
    if (!iconEl) return;
    const punto = iconEl.querySelector('.punto-interior');
    if (!punto) return;
    punto.className = (this.escenarioActivo && this.escenarioEnProgreso)
      ? 'punto-interior activo'
      : 'punto-interior';
  }

  dibujarRuta() {
    if (!this.mapa || !this.ruta || this.ruta.length < 2) return;

    if (this.lineaRuta) {
      this.mapa.removeLayer(this.lineaRuta);
    }

    this.lineaRuta = L.polyline(this.ruta, {
      color: '#00e5c4',
      weight: 2,
      opacity: 0.4,
      dashArray: '5, 10',
      smoothFactor: 1.5
    }).addTo(this.mapa);

    this.mapa.fitBounds(this.lineaRuta.getBounds(), { padding: [50, 50], maxZoom: 14 });
  }

  actualizarLineaRuta() {
    if (!this.lineaRuta || !this.ruta || this.ruta.length < 2) return;

    if (this.progresoRuta >= 0.99) {
      this.ocultarRuta();
      return;
    }

    // Usar el indice de segmento del servidor (exacto por distancia)
    const idx = this.indiceRuta;

    if (idx >= this.ruta.length - 1) {
      this.ocultarRuta();
      return;
    }

    // La ruta restante empieza en la posicion actual del vehiculo
    const rutaRestante = [this.posActual, ...this.ruta.slice(idx + 1)];
    this.lineaRuta.setLatLngs(rutaRestante);
  }

  ocultarRuta() {
    if (this.lineaRuta && this.mapa) {
      this.mapa.removeLayer(this.lineaRuta);
      this.lineaRuta = null;
    }
  }

  // Actualizacion de UI
  actualizarUI() {
    // Velocidad
    const velEl = document.getElementById('velocidad-valor');
    if (velEl) {
      velEl.textContent = Math.round(this.velocidad);
      if (this.velocidad === 0) {
        velEl.style.color = 'var(--text-muted)';
      } else if (this.velocidad < 40) {
        velEl.style.color = 'var(--warn)';
      } else if (this.velocidad < 80) {
        velEl.style.color = 'var(--accent)';
      } else {
        velEl.style.color = 'var(--danger)';
      }
    }

    // GPS
    const gpsEl = document.getElementById('gps-coordenadas');
    if (gpsEl) {
      gpsEl.textContent = this.posActual[0].toFixed(6) + ', ' + this.posActual[1].toFixed(6);
    }

    // Combustible
    const combEl = document.getElementById('combustible-tiempo-real');
    if (combEl) {
      combEl.textContent = this.combustible.toFixed(1);
      combEl.style.color = this.combustible < 20 ? 'var(--danger)'
                         : this.combustible < 40 ? 'var(--warn)'
                         : 'var(--accent)';
    }

    // Temperatura
    const tempEl = document.getElementById('temperatura-tiempo-real');
    const tempAlerta = document.getElementById('temperatura-alerta');
    if (tempEl) {
      tempEl.textContent = this.temperatura.toFixed(0);
      if (this.temperatura > 100) {
        tempEl.style.color = 'var(--danger)';
        if (tempAlerta) { tempAlerta.textContent = '⚠ CRÍTICO'; tempAlerta.style.color = 'var(--danger)'; }
      } else if (this.temperatura > 90) {
        tempEl.style.color = 'var(--warn)';
        if (tempAlerta) { tempAlerta.textContent = '⚠ Elevado'; tempAlerta.style.color = 'var(--warn)'; }
      } else {
        tempEl.style.color = 'var(--accent)';
        if (tempAlerta) { tempAlerta.textContent = '✓ Normal'; tempAlerta.style.color = 'var(--accent)'; }
      }
    }

    // Kilometraje
    const kmEl = document.getElementById('kilometraje-valor');
    if (kmEl) {
      kmEl.textContent = (this.kilometraje / 1000).toFixed(1) + 'k';
    }

    // Aceite Motor
    const aceiteEl = document.getElementById('aceite-valor');
    const aceiteAlerta = document.getElementById('aceite-alerta');
    if (aceiteEl) {
      aceiteEl.textContent = this.aceite.toFixed(0);
      if (this.aceite < 30) {
        aceiteEl.style.color = 'var(--danger)';
        if (aceiteAlerta) { aceiteAlerta.textContent = '⚠ Bajo'; aceiteAlerta.style.color = 'var(--danger)'; }
      } else if (this.aceite < 50) {
        aceiteEl.style.color = 'var(--warn)';
        if (aceiteAlerta) { aceiteAlerta.textContent = '⚠ Medio'; aceiteAlerta.style.color = 'var(--warn)'; }
      } else {
        aceiteEl.style.color = 'var(--accent)';
        if (aceiteAlerta) { aceiteAlerta.textContent = '✓ Normal'; aceiteAlerta.style.color = 'var(--accent)'; }
      }
    }

    // Distancia
    const distEl = document.getElementById('distancia-recorrida');
    if (distEl) distEl.textContent = this.distanciaRecorrida.toFixed(1);

    // Escenario
    const escNomEl = document.getElementById('escenario-nombre');
    if (escNomEl) {
      if (this.escenarioActivo) {
        const nombre = this.nombreEscenario || this.escenarioActivo;
        escNomEl.textContent = nombre.charAt(0).toUpperCase() + nombre.slice(1);
        escNomEl.style.color = 'var(--warn)';
      } else {
        escNomEl.textContent = 'Patrullaje';
        escNomEl.style.color = 'var(--accent)';
      }
    }
  }

  // Establece el nombre descriptivo del escenario (de la IA)
  setNombreEscenario(nombre) {
    this.nombreEscenario = nombre;
  }

}

// Instancia global
const simulador = new SimuladorRenderer();
