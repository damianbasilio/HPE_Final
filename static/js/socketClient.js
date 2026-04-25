// Cliente WebSocket - Recepcion de estado del servidor
// Recibe el estado del vehiculo del servidor y lo envia
// al renderizador. Envia comandos del usuario al servidor.

class SocketClient {
  constructor() {
    this.socket = null;
    this.conectado = false;
    this.simulador = null;
  }

  inicializar(simuladorInstancia) {
    this.simulador = simuladorInstancia;
    this.conectar();
  }

  conectar() {
    try {
      this.socket = io({
        transports: ['websocket', 'polling'],
        reconnection: true,
        reconnectionDelay: 1000,
        reconnectionDelayMax: 5000
      });

      this.registrarEventos();
    } catch (error) {
    }
  }

  registrarEventos() {
    this.socket.on('connect', () => {
      this.conectado = true;
      this.actualizarIndicadorUI(true);
    });

    this.socket.on('disconnect', (razon) => {
      this.conectado = false;
      this.actualizarIndicadorUI(false);
    });

    this.socket.on('connect_error', (error) => {
    });

    // Recibir estado del vehiculo del servidor (10Hz)
    this.socket.on('estado_vehiculo', (data) => {
      if (this.simulador) {
        this.simulador.actualizarDesdeServidor(data);
      }
    });

    // Estado inicial al conectar
    this.socket.on('estado_inicial', (data) => {
      if (this.simulador && data.estado) {
        this.simulador.actualizarDesdeServidor(data);
      }
      if (data.ruta && this.simulador) {
        this.simulador.actualizarRuta(data.ruta);
      }
    });

    // Comandos del despachador
    this.socket.on('comando', (data) => {
      this.procesarComando(data);
    });

    // Ping-pong para mantener conexion
    this.socket.on('ping', () => {
      this.socket.emit('pong');
    });
  }

  enviarControl(accion) {
    if (!this.conectado) return;
    this.socket.emit('control_simulacion', {
      accion,
      timestamp: Date.now()
    });
  }

  procesarComando(data) {
    if (!this.simulador) return;

    switch (data.tipo) {
      case 'terminar_escenario':
        this.enviarControl('terminar');
        break;
      case 'mensaje':
        this.mostrarMensajeDespachador(data.mensaje);
        break;
      default:
        break;
    }
  }

  mostrarMensajeDespachador(mensaje) {
    const notif = document.createElement('div');
    notif.className = 'notificacion-despachador';
    notif.innerHTML = `
      <div class="notif-header">📡 Mensaje de Central</div>
      <div class="notif-body">${mensaje}</div>
    `;
    notif.style.cssText = `
      position: fixed; top: 20px; right: 20px; background: #1a1a25;
      border: 1px solid #3b82f6; border-radius: 10px; padding: 16px;
      color: #f8fafc; z-index: 10000; animation: slideIn 0.3s ease; max-width: 300px;
    `;
    document.body.appendChild(notif);
    setTimeout(() => {
      notif.style.animation = 'fadeOut 0.3s ease';
      setTimeout(() => notif.remove(), 300);
    }, 5000);
  }

  actualizarIndicadorUI(conectado) {
    let indicador = document.getElementById('ws-status-dot');
    if (!indicador) return;

    indicador.classList.remove('connected', 'disconnected');
    if (conectado) {
      indicador.classList.add('connected');
      indicador.title = 'Conectado al servidor';
    } else {
      indicador.classList.add('disconnected');
      indicador.title = 'Desconectado';
    }
  }

  desconectar() {
    if (this.socket) {
      this.socket.disconnect();
    }
    this.conectado = false;
  }
}

// Instancia global
const socketClient = new SocketClient();

// Inicializar cuando el DOM este listo
document.addEventListener('DOMContentLoaded', () => {
  setTimeout(() => {
    if (typeof simulador !== 'undefined') {
      socketClient.inicializar(simulador);
    }
  }, 500);
});

// Desconectar al cerrar la pagina
window.addEventListener('beforeunload', () => {
  socketClient.desconectar();
});
