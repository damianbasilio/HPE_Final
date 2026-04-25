

def limitar(valor, minimo, maximo):
    return max(minimo, min(maximo, valor))

def a_decimal(valor, defecto=0.0):
    if valor is None:
        return defecto
    try:
        return float(valor)
    except (ValueError, TypeError):
        return defecto

def a_entero(valor, defecto=0):
    if valor is None:
        return defecto
    try:
        return int(valor)
    except (ValueError, TypeError):
        return defecto

def extraer_json(texto):

    if "```json" in texto:
        texto = texto.split("```json")[1].split("```")[0]
    elif "```" in texto:
        texto = texto.split("```")[1].split("```")[0]
    return texto.strip()
