class MapaAruba {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.bounds = null;
    this.roads = [];
    this.pois = [];
    this.vehicles = [];
    this.scale = 1;
    this.offset = { x: 0, y: 0 };
    this.dragging = false;
    this.last = { x: 0, y: 0 };

    this._resize();
    window.addEventListener('resize', () => this._resize());

    this.canvas.addEventListener('wheel', (e) => this._zoom(e));
    this.canvas.addEventListener('mousedown', (e) => this._startDrag(e));
    this.canvas.addEventListener('mousemove', (e) => this._drag(e));
    this.canvas.addEventListener('mouseup', () => this._endDrag());
    this.canvas.addEventListener('mouseleave', () => this._endDrag());
  }

  setData(data) {
    this.bounds = data.bounds;
    this.roads = data.roads || [];
    this.pois = data.pois || [];
    this.render();
  }

  setVehicles(vehicles) {
    this.vehicles = vehicles || [];
    this.render();
  }

  _resize() {
    this.canvas.width = this.canvas.clientWidth;
    this.canvas.height = this.canvas.clientHeight;
    this.render();
  }

  _zoom(e) {
    e.preventDefault();
    const delta = e.deltaY > 0 ? 0.9 : 1.1;
    this.scale = Math.min(4, Math.max(0.6, this.scale * delta));
    this.render();
  }

  _startDrag(e) {
    this.dragging = true;
    this.last = { x: e.clientX, y: e.clientY };
  }

  _drag(e) {
    if (!this.dragging) return;
    const dx = e.clientX - this.last.x;
    const dy = e.clientY - this.last.y;
    this.offset.x += dx;
    this.offset.y += dy;
    this.last = { x: e.clientX, y: e.clientY };
    this.render();
  }

  _endDrag() {
    this.dragging = false;
  }

  _toScreen(lat, lon) {
    if (!this.bounds) return { x: 0, y: 0 };
    const [latMin, latMax, lonMin, lonMax] = this.bounds;
    const width = this.canvas.width;
    const height = this.canvas.height;

    const xNorm = (lon - lonMin) / (lonMax - lonMin);
    const yNorm = (latMax - lat) / (latMax - latMin);

    const x = xNorm * width * this.scale + this.offset.x;
    const y = yNorm * height * this.scale + this.offset.y;

    return { x, y };
  }

  _drawGrid() {
    const ctx = this.ctx;
    ctx.save();
    ctx.strokeStyle = 'rgba(255,255,255,0.04)';
    ctx.lineWidth = 1;
    const step = 80;
    for (let x = 0; x < this.canvas.width; x += step) {
      ctx.beginPath();
      ctx.moveTo(x + (this.offset.x % step), 0);
      ctx.lineTo(x + (this.offset.x % step), this.canvas.height);
      ctx.stroke();
    }
    for (let y = 0; y < this.canvas.height; y += step) {
      ctx.beginPath();
      ctx.moveTo(0, y + (this.offset.y % step));
      ctx.lineTo(this.canvas.width, y + (this.offset.y % step));
      ctx.stroke();
    }
    ctx.restore();
  }

  _drawRoads() {
    const ctx = this.ctx;
    ctx.save();
    ctx.strokeStyle = 'rgba(0,229,196,0.25)';
    ctx.lineWidth = 1;

    this.roads.forEach((road) => {
      const geometry = road.geometry;
      if (Array.isArray(geometry) && geometry.length > 1) {
        ctx.beginPath();
        geometry.forEach((p, idx) => {
          const point = this._toScreen(p[0], p[1]);
          if (idx === 0) ctx.moveTo(point.x, point.y);
          else ctx.lineTo(point.x, point.y);
        });
        ctx.stroke();
      } else {
        const start = this._toScreen(road.start_lat, road.start_lon);
        const end = this._toScreen(road.end_lat, road.end_lon);
        ctx.beginPath();
        ctx.moveTo(start.x, start.y);
        ctx.lineTo(end.x, end.y);
        ctx.stroke();
      }
    });
    ctx.restore();
  }

  _drawPois() {
    const ctx = this.ctx;
    ctx.save();
    ctx.fillStyle = 'rgba(91,141,255,0.6)';

    this.pois.forEach((poi) => {
      const p = this._toScreen(poi.latitude, poi.longitude);
      ctx.beginPath();
      ctx.arc(p.x, p.y, 2.5, 0, Math.PI * 2);
      ctx.fill();
    });
    ctx.restore();
  }

  _drawVehicles() {
    const ctx = this.ctx;
    this.vehicles.forEach((veh) => {
      const gps = veh.gps || veh.telemetry || {};
      const lat = gps.latitud ?? gps.lat;
      const lon = gps.longitud ?? gps.lon;
      if (lat == null || lon == null) return;

      const p = this._toScreen(lat, lon);
      ctx.save();
      ctx.fillStyle = veh.estado_servicio && veh.estado_servicio !== 'disponible' ? '#f87171' : '#60a5fa';
      ctx.beginPath();
      ctx.arc(p.x, p.y, 4.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    });
  }

  render() {
    if (!this.ctx) return;
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    this.ctx.fillStyle = '#0b0d10';
    this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);

    this._drawGrid();
    this._drawRoads();
    this._drawPois();
    this._drawVehicles();
  }
}

window.MapaAruba = MapaAruba;
