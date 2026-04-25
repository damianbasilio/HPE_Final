# Servicio de LLMs

A continuación se explica **cómo interactuar con el servicio de IA**.

## Modelos disponibles: La estrategia Flagship vs. Flash

Al trabajar con inteligencia artificial, no todas las tareas requieren la misma potencia de cálculo. Para optimizar recursos y tiempos de respuesta, este servicio se divide en dos enfoques estratégicos: un modelo **Flagship** (pesado, analítico y potente) y un modelo **Flash** (ligero, rápido y eficiente).

A continuación te detallamos cuáles son en esta integración y por qué se clasifican así.

### 1) Qwen/Qwen3-235B-A22B (El modelo *Flagship*)

Este es el "buque insignia" del servicio. Con una arquitectura masiva, está diseñado para enfrentarse al "trabajo duro" cognitivo. Prioriza la profundidad analítica, el razonamiento estructurado y la calidad exhaustiva de la respuesta, aunque esto implique un mayor tiempo de procesamiento.

**Uso recomendado:**
* Generación de respuestas altamente elaboradas o técnicas.
* Resolución de tareas complejas que requieren activar el razonamiento lógico.
* Mantenimiento de conversaciones largas donde el contexto denso es crucial.

**Limitaciones relevantes:**
* **No admite entrada con imagen** en esta integración.

### 2) google/gemma-4-31b-it (El modelo *Flash*)

Este modelo es la alternativa rápida. Al ser mucho más compacto en tamaño, sacrifica la capacidad de realizar reflexiones profundas a cambio de ofrecer una velocidad de respuesta excelente y una latencia mínima. Es la herramienta ideal para la agilidad del día a día.

**Uso recomendado:**
* Mantenimiento de conversaciones generales y fluidas.
* Peticiones donde se necesitan respuestas rápidas e inmediatas.
* Creación de flujos simples de interacción directa.

**Limitaciones relevantes:**
* **No soporta razonamiento activado (thinking)** en esta integración.

Limitaciones relevantes:

* **no soporta razonamiento activado** en esta integración.

## Estructura general de una petición

Toda petición al servicio se construye alrededor de estos conceptos:

* **prompt**: texto que se quiere enviar
* **model**: modelo a usar
* **system**: instrucciones generales de comportamiento
* **max_tokens**: tamaño máximo de la respuesta
* **temperature**: grado de creatividad
* **top_p**: control adicional de diversidad
* **seed**: semilla para hacer resultados más reproducibles
* **stream**: respuesta en tiempo real por fragmentos
* **enable_thinking**: activa el razonamiento cuando el modelo lo permite
* **image_url**: URL pública de una imagen, cuando aplique

## Implementación base del cliente

Este es el núcleo del sistema que se usa para realizar peticiones a los modelos. No es necesario modificarlo durante el hackatón; únicamente sirve como capa de comunicación con el backend.

La función `ask()` es la encargada de:

* construir el mensaje según el tipo de entrada (texto, historial o imagen);
* seleccionar el modelo (Qwen o Gemma);
* aplicar parámetros como temperatura, top_p o max_tokens;
* gestionar streaming o respuesta normal;
* devolver la salida final del modelo.

```python
from openai import OpenAI
import asyncio, json, re

models = {
    "qwen": {
        "base_url": "http://10.10.48.10:8001/v1",
        "name": "Qwen/Qwen3-235B-A22B",
    },
    "gemma": {
        "base_url": "http://10.10.48.10:8000/v1",
        "name": "google/gemma-4-31b-it"
    }
}

def ask(
    prompt: str | list,
    model: str = "qwen",
    system: str = "Eres un asistente experto.",
    max_tokens: int = 1024,
    temperature: float = 0.6,
    top_p: float = 0.95,
    seed: int = None,
    stream: bool = False,
    enable_thinking: bool = False,
    image_url: str = None,
):
    client = OpenAI(base_url=models[model]["base_url"], api_key="dummy")

    if isinstance(prompt, list):
        messages = prompt
    elif image_url:
        messages = [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_url}},
                {"type": "text", "text": prompt},
            ],
        }]
        if model == "qwen":
            print("El modelo Qwen no soporta inputs con imagen.")
            return None
    else:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]

    if model == "gemma":
        print("El modelo Gemma no soporta razonamiento.")
    else:
        extra_body = {
            "chat_template_kwargs": {"enable_thinking": enable_thinking},
        }

    resp = client.chat.completions.create(
        model=models[model]["name"],
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        seed=seed,
        stream=stream,
        extra_body=extra_body,
    )

    if stream:
        def _generator():
            for chunk in resp:
                delta = chunk.choices[0].delta.content
                if delta:
                    print(delta, end="", flush=True)
                    yield delta
            print()
        return _generator()

    choice = resp.choices[0]
    content = choice.message.content or ""
    thinking = getattr(choice.message, "reasoning_content", None) or ""

    return {"thinking": thinking, "content": content} if thinking else content
```

## Ejemplos de uso (equivalente al main)

A continuación se muestran los mismos casos que aparecen en el `main`, explicados como ejemplos de uso directo de la función `ask()`.

### 1. Petición simple

```python
ask("¿Qué es la fusión nuclear?", model="gemma")
```

---

### 2. Thinking activado (solo Qwen)

```python
ask("Resuelve: ¿cuánto es 17 × 23?", enable_thinking=True, model="qwen")
```

---

### 3. Streaming

```python
for chunk in ask("Escribe un poema sobre el cosmos.", stream=True, model="gemma"):
    pass  # ya imprime en tiempo real
```

---

### 4. Uso con imagen

```python
ask(
    "¿Qué ves en esta imagen?",
    image_url="https://cdn.pixabay.com/photo/2022/12/31/08/44/bird-7688239_1280.jpg",
    model="gemma"
)
```

---

### 5. Historial multi-turno

```python
historial = [
    {"role": "system", "content": "Eres un tutor de física."},
    {"role": "user", "content": "Hola, me llamo Ana."},
    {"role": "assistant", "content": "¡Hola Ana! ¿En qué te puedo ayudar?"},
    {"role": "user", "content": "¿Recuerdas mi nombre?"},
]
ask(historial, model="gemma")
```