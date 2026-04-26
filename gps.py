

import random
import math
from config import ARUBA_LANDMARKS, CENTRO_ARUBA


def _spawn_en_carretera():
    """Return an initial position on the road network.

    Imports rutas lazily to avoid circular imports at module load time.
    Falls back to a random landmark (no jitter) if the road graph is not
    available yet.
    """
    try:
        from rutas import spawn_en_carretera
        return spawn_en_carretera()
    except Exception:
        pass
    # Fallback: landmark without jitter
    if not ARUBA_LANDMARKS:
        return CENTRO_ARUBA
    nombre, lat, lon = random.choice(ARUBA_LANDMARKS)
    return (lat, lon)


class SimuladorGPS:

    def __init__(self, lat=None, lon=None):
        if lat is None or lon is None:
            spawn = _spawn_en_carretera()
            self.latitud = lat if lat is not None else spawn[0]
            self.longitud = lon if lon is not None else spawn[1]
        else:
            self.latitud = lat
            self.longitud = lon

        self.ruta = None
        self.indice_ruta = 0
        self.progreso_ruta = 0.0
        self.distancia_total = 0.0

    def establecer_ruta(self, ruta, distancia_total=None):
        self.ruta = ruta
        self.indice_ruta = 0
        self.progreso_ruta = 0.0

        if distancia_total:
            self.distancia_total = distancia_total
        elif ruta and len(ruta) >= 2:
            self.distancia_total = self._calcular_distancia_ruta()
        else:
            self.distancia_total = 0

        if ruta and len(ruta) > 0:
            self.latitud = ruta[0][0]
            self.longitud = ruta[0][1]

    def _calcular_distancia_ruta(self):
        if not self.ruta or len(self.ruta) < 2:
            return 0

        total = 0
        for i in range(len(self.ruta) - 1):
            total += self._haversine(self.ruta[i], self.ruta[i + 1])
        return total

    def _haversine(self, punto1, punto2):

        lat1, lon1 = math.radians(punto1[0]), math.radians(punto1[1])
        lat2, lon2 = math.radians(punto2[0]), math.radians(punto2[1])

        dlat = lat2 - lat1
        dlon = lon2 - lon1

        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        c = 2 * math.asin(math.sqrt(a))

        return 6371 * c

    def actualizar(self, velocidad_kmh, dt):
        if velocidad_kmh <= 0:
            return

        if self.ruta and len(self.ruta) >= 2:
            self._actualizar_por_ruta(velocidad_kmh, dt)

    def _actualizar_por_ruta(self, velocidad_kmh, dt):
        if self.distancia_total <= 0:
            return

        distancia_km = (velocidad_kmh / 3600) * dt
        incremento = distancia_km / self.distancia_total
        self.progreso_ruta = min(1.0, self.progreso_ruta + incremento)

        lat, lon, idx = self._interpolar_posicion()
        self.latitud = lat
        self.longitud = lon
        self.indice_ruta = idx

    def _interpolar_posicion(self):
        if not self.ruta or len(self.ruta) < 2:
            return self.latitud, self.longitud, 0

        distancias = []
        total = 0
        for i in range(len(self.ruta) - 1):
            d = self._haversine(self.ruta[i], self.ruta[i + 1])
            distancias.append(d)
            total += d

        if total == 0:
            return self.ruta[0][0], self.ruta[0][1], 0

        dist_objetivo = self.progreso_ruta * total
        dist_acumulada = 0

        for i, d in enumerate(distancias):
            if dist_acumulada + d >= dist_objetivo:
                if d > 0:
                    t = (dist_objetivo - dist_acumulada) / d
                else:
                    t = 0

                lat = self.ruta[i][0] + t * (self.ruta[i + 1][0] - self.ruta[i][0])
                lon = self.ruta[i][1] + t * (self.ruta[i + 1][1] - self.ruta[i][1])
                return lat, lon, i

            dist_acumulada += d

        return self.ruta[-1][0], self.ruta[-1][1], len(self.ruta) - 1

    def obtener_coordenadas(self):
        return {
            'latitud': round(self.latitud, 6),
            'longitud': round(self.longitud, 6),
            'tiene_ruta': self.ruta is not None,
            'progreso_ruta': round(self.progreso_ruta, 4)
        }

    def obtener_coordenadas_ligero(self):
        return {
            'latitud': round(self.latitud, 6),
            'longitud': round(self.longitud, 6),
            'progreso_ruta': round(self.progreso_ruta, 4),
            'indice_ruta': self.indice_ruta
        }

    def ruta_restante_comprimida(self, max_puntos: int = 60) -> list:
        """Devuelve los puntos restantes de la ruta desde la posicion actual.

        Antepone la posicion GPS real como primer punto para que la linea
        en el mapa arranque exactamente donde esta el vehiculo.
        Decima si hay mas de `max_puntos` para no saturar el WebSocket.
        """
        if not self.ruta or len(self.ruta) < 2:
            return []

        # Puntos desde el indice actual hasta el final
        restantes = self.ruta[self.indice_ruta:]
        if not restantes:
            return []

        # Primero la posicion real interpolada del vehiculo
        inicio = [round(self.latitud, 5), round(self.longitud, 5)]
        puntos = [inicio] + [[round(p[0], 5), round(p[1], 5)] for p in restantes]

        # Decimacion uniforme si superamos el tope
        if len(puntos) > max_puntos:
            paso = len(puntos) / max_puntos
            puntos = [puntos[int(i * paso)] for i in range(max_puntos - 1)] + [puntos[-1]]

        return puntos
