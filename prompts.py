PROMPT_CHATBOT = """
Eres un asistente operativo del Gemelo Digital de Aruba.
Responde en espanol con claridad y datos concretos, sin inventar.
Si falta informacion, dilo y sugiere donde obtenerla.
Cuando te pregunten por costes operativos, usa exclusivamente la seccion COSTES
(EUR, dotaciones y desgloses por personal/energia/desgaste/activacion/prima).

ROL USUARIO: {rol}

CONTEXTO ACTUAL:
- Clima: {clima}
- Eventos activos: {eventos}
- Flota: {flota}
- Alertas: {alertas}
- Costes: {costes}

PREGUNTA:
{pregunta}
"""

def formatear_prompt_chat(rol: str, pregunta: str, contexto: dict) -> str:
    return PROMPT_CHATBOT.format(
        rol=rol,
        pregunta=pregunta,
        clima=contexto.get("clima", "sin datos"),
        eventos=contexto.get("eventos", "sin datos"),
        flota=contexto.get("flota", "sin datos"),
        alertas=contexto.get("alertas", "sin alertas"),
        costes=contexto.get("costes", "sin datos"),
    )
