# Gemelo Digital: Mapas y Simulación

Documentación relacionada con la representación de mapas, topología y buenas prácticas de simulación para el desafío técnico de CDS.

## Documentación de OSMnx

### Descripción

OSMnx es una biblioteca de Python de código abierto para representar la topología de cualquier ciudad. Está basada en un proyecto similar, NetworkX, pero se comunica con la API pública de OpenStreetMap para obtener información actualizada.

OSMnx utiliza principalmente la estructura de datos `networkx.MultiDiGraph` para representar calles, permitiendo múltiples aristas entre los mismos vértices.

[Documentación de OSMnx](https://osmnx.readthedocs.io/en/stable/user-reference.html)

### Instalación

Instala OSMnx mediante pip:

```shell
pip install osmnx
```

Requiere **Python 3.11** o superior. Para gestores de dependencias como Poetry, consulta la documentación oficial.

### Funciones útiles

* **`osmnx.graph.graph_from_place`**

  ```python
  osmnx.graph.graph_from_place(query, *, network_type='all', simplify=True)
  ```

  Utiliza la API Overpass para descargar un grafo del área correspondiente. `query` suele ser una cadena con el nombre del lugar. Si OSM no tiene polígonos definidos para el sitio, usa `graph_from_address`.
  *Ejemplo de uso en `mapping.py:19`.*

* **`osmnx.io.save_graphml`**

  ```python
  osmnx.io.save_graphml(G, filepath=None, *, gephi=False, encoding='utf-8')
  ```

  Guarda un objeto `networkx.MultiDiGraph` como archivo [GraphML](https://en.wikipedia.org/wiki/GraphML). **Nota:** `save_graph_xml` puede perder datos del grafo; se recomienda GraphML.

* **`osmnx.plot.plot_graph`**

  ```python
  osmnx.plot.plot_graph(G, *, ax=None, figsize=(8, 8), bgcolor='#111111', node_color='w', node_size=15)
  ```

  Representa visualmente el grafo.

* **`osmnx.distance.nearest_nodes`**

  ```python
  osmnx.distance.nearest_nodes(G, X, Y, *, return_dist=False)
  ```

  Encuentra los nodos más cercanos al punto definido por `X` e `Y`.

### Ejemplo de implementación en Aruba

```python
import osmnx as ox
graph = ox.graph_from_place("Aruba", network_type='drive')

# Guardar en GraphML
ox.save_graphml(graph, "aruba.graphml")

# Cargar de nuevo a MultiDiGraph
graph = ox.load_graphml("aruba.graphml")

# Encontrar nodo más cercano
coords = ("12.52", "-70.03") # Coordenadas de ejemplo
node = ox.distance.nearest_nodes(graph, coords[0], coords[1])
```

## Recomendaciones generales

### Multithreading y asincronía

Actualizar posiciones de vehículos puede volverse costoso a medida que aumenta el número de elementos.

* **Asyncio:** Úsalo para tareas limitadas por I/O en un solo núcleo. Comprende `coroutines`, `async/await` y `gather`.
* **Threading:** Úsalo para tareas intensivas de CPU o procesamiento en múltiples núcleos. Ten cuidado con `Locks` y `Semaphores` para el acceso a datos compartidos.

### API y reutilización de datos

Considera la sobrecarga de llamadas a APIs en tiempo real. Implementa caché local o simulación de datos durante el desarrollo cuando se requieran actualizaciones frecuentes y consistentes.

### Optimización del frontend

Mantén el frontend ligero para evitar cuellos de botella en la visualización:

* Optimiza formatos de imagen y compresión.
* Minimiza los elementos renderizados simultáneamente.
* Implementa **lazy loading**.
* Evita cargar recursos pesados innecesarios.
