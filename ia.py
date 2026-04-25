from llm_client import chat_completion
from prompts import formatear_prompt_chat


def responder_chat(pregunta: str, rol: str, contexto: dict) -> str:
    prompt = formatear_prompt_chat(rol, pregunta, contexto)
    mensajes = [
        {"role": "system", "content": "Eres un asistente operativo experto para Aruba."},
        {"role": "user", "content": prompt}
    ]
    return chat_completion(mensajes)
