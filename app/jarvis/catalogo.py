"""Catalogo de operaciones del asistente de aula (Rol A).

El catalogo no es una lista de funcionalidades: es el diseno de la latencia.
La caracterizacion del 27 y 28 de octubre sobre Raspberry Pi 4 establecio que
el tiempo de respuesta del modelo de decision es lineal en el tamano del
prompt, a 18,5 tokens por segundo. Cada criterio del catalogo se paga en cada
consulta, de modo que anadir una operacion encarece todas las ordenes, no solo
las que la usan.

PRESUPUESTO DE TOKENS
---------------------
    t = n / V        V = 18,5 tok/s   (medido, cinco corridas independientes)
    t_max = 15 s  ->  n <= 277 tokens

Mediciones del 28 de octubre sobre el hardware objetivo:

    configuracion                                 tokens   tiempo
    --------------------------------------------  ------   ------
    tres preguntas (accion + zona + riesgo)          533    22,5 s
    accion sola, nueve criterios                     277    15,0 s
    accion con zona plegada, trece criterios         377    20,4 s
    zona sola, tres criterios                        144     7,6 s

De ahi las dos decisiones que gobiernan este modulo:

1. Nueve operaciones con descripciones de dos palabras. El limite duro de la
   API es de 24 criterios por pregunta; el limite real es el presupuesto.

2. La zona se pregunta solo en las operaciones de iluminacion. Preguntarla
   siempre cuesta 7,5 s en las 23 de cada 27 ordenes que no la usan. Sumarla
   a las de luz cuesta 7,6 s adicionales solo en esas, y plegarla dentro de
   las acciones resulto peor (377 tokens) que preguntarla aparte.

El costo fijo de plantilla ronda los 120 tokens por pregunta: es lo que hace
que dos llamadas cortas sean peores que una sola mas larga, y es la misma
razon por la que se elimino el enrutamiento jerarquico de dos etapas.
"""

import unicodedata
from typing import Dict, Any

from .hal_rooms import Room

# ---------------------------------------------------------------------------
# Operaciones
# ---------------------------------------------------------------------------
# Las descripciones son de dos palabras por diseno. Cada palabra adicional
# multiplica por nueve el costo en el prompt, y el prompt gobierna la latencia.
#
# NADA se describe como "Fuera de alcance" y no como "Ninguna anterior": en el
# banco del 28 de octubre, la formulacion generica actuaba como atractor y
# absorbia clasificaciones correctas (26 % de probabilidad en un caso de libro,
# y las tres fallas de LUZ_MODO).

ACCIONES: Dict[str, str] = {
    "LUZ_ON":      "Encender iluminacion",
    "LUZ_OFF":     "Apagar iluminacion",
    "LUZ_MODO":    "Cambiar escena",
    "AMBIENTE":    "Consultar ambiente",
    "ENTREGA_REG": "Registrar entrega",
    "ENTREGA_VER": "Consultar entregas",
    "AGENDA":      "Ver agenda",
    "RESUMEN":     "Resumir sesion",
    "NADA":        "Fuera de alcance",
}

# Operaciones que necesitan saber sobre que zona del aula actuar.
ACCIONES_CON_ZONA = frozenset({"LUZ_ON", "LUZ_OFF", "LUZ_MODO"})

# Operaciones que modifican registros de forma no trivial y exigen
# confirmacion explicita del usuario antes de ejecutarse (RF-09).
ACCIONES_IRREVERSIBLES = frozenset({"ENTREGA_REG"})

# Operaciones reversibles que, sin zona, afectan el aula entera y por lo
# tanto se confirman por alcance y no por irreversibilidad. Encender no esta
# aqui: encender de mas molesta mucho menos que apagar de mas.
ACCIONES_DE_ALCANCE_TOTAL = frozenset({"LUZ_OFF"})

# Operaciones que requieren redactar lenguaje natural y por lo tanto pasan
# por el carril generativo (RF-07). El resto se resuelve con la decision y
# una respuesta construida por la aplicacion.
ACCIONES_GENERATIVAS = frozenset({"RESUMEN"})

# ---------------------------------------------------------------------------
# Zonas
# ---------------------------------------------------------------------------
# Los criterios se derivan del enum de la HAL para que catalogo y hardware no
# se desincronicen. Solo se ofrecen las zonas que la implementacion soporta;
# las demas estan declaradas en Room como diseno objetivo.

ZONAS: Dict[str, str] = {
    Room.FRENTE.name: "Zona proyeccion",
    Room.FONDO.name:  "Zona trabajo",
    "AMBAS":          "Todo el aula",
    "NINGUNA":        "Sin especificar",
}

# Escenas disponibles, para la operacion LUZ_MODO. No se preguntan al modelo
# de decision: se resuelven por coincidencia de texto en la aplicacion, que
# es gratis, en lugar de pagar una cuarta pregunta.
ESCENAS_CONOCIDAS = ("proyeccion", "lectura", "grupos", "examen", "salida")

# ---------------------------------------------------------------------------
# Construccion de la solicitud de decision
# ---------------------------------------------------------------------------

UMBRAL_CONFIANZA = 0.50
"""Umbral de abstencion (RF-02).

Calibrado sobre el banco del 28 de octubre: con 0,50 se rechazan cuatro de
las seis clasificaciones erroneas al costo de un acierto. El criterio es la
curva de intercambio entre aciertos perdidos y fallas evitadas, no el minimo
de los aciertos: con una precision base publicada de 63,5 %, un umbral que no
descarta nada deja RF-02 sin efecto.

Requiere recalibracion cuando cambie el catalogo o el modelo.
"""


def preguntas(orden: str, con_zona: bool = False) -> Dict[str, Any]:
    """Arma el bloque de preguntas para /v1/systemone.

    Por defecto pregunta solo la accion: 277 tokens, 15,0 s medidos. Con
    con_zona anade la pregunta de zona, que cuesta 144 tokens adicionales.

    El llamador normalmente invoca esto dos veces: una sin zona para
    clasificar, y otra con zona solo si la accion resulto estar en
    ACCIONES_CON_ZONA. Ver el modulo de inferencia.
    """
    q: Dict[str, Any] = {
        "accion": {
            "type": "choice",
            "instructions": "Que accion pide el usuario",
            "criteria": dict(ACCIONES),
        }
    }
    if con_zona:
        q["zona"] = {
            "type": "choice",
            "instructions": "Sobre que zona del aula actuar",
            "criteria": dict(ZONAS),
        }
    return q


def preguntas_zona() -> Dict[str, Any]:
    """Solo la pregunta de zona, para la segunda llamada condicional."""
    return {
        "zona": {
            "type": "choice",
            "instructions": "Sobre que zona del aula actuar",
            "criteria": dict(ZONAS),
        }
    }


def requiere_zona(accion: str) -> bool:
    return accion in ACCIONES_CON_ZONA


# Sinonimos de zona observados en el banco de frases. La zona esta escrita
# en la orden casi siempre, de modo que resolverla por texto es mas fiable
# que preguntarla: el banco del 10 de noviembre la clasifico mal en dos de
# tres casos, con la palabra presente en la frase.
ALIAS_ZONA = {
    "FRENTE": ("frente", "adelante", "enfrente", "pizarra", "tablero",
               "proyector", "primeras filas"),
    "FONDO":  ("fondo", "atras", "detras", "al final", "ultimas filas"),
}

# Palabras que declaran explicitamente el aula completa. Se reconocen para
# distinguir "apaga todas las luces" —que afecta todo y exige confirmacion—
# de una orden que simplemente omitio la zona.
PALABRAS_DE_TODO = ("todas", "todos", "todo el aula", "el aula completa",
                    "completa", "general")


def zona_explicita(texto: str) -> Optional[str]:
    """Zona nombrada en la orden, o None si no nombra ninguna.

    Devolver None significa "no lo dijo", que el agente interpreta como el
    aula completa. Es deliberado que no haya un valor intermedio: una orden
    sin zona y una orden que dice "todas" actuan igual, y la diferencia solo
    importa para decidir si se pide confirmacion.
    """
    t = normalizar(texto)
    for zona, palabras in ALIAS_ZONA.items():
        if any(p in t for p in palabras):
            return zona
    return None


# Palabras que identifican una operacion sin ambiguedad dentro de este
# catalogo. Se resuelven por texto, como las escenas y la zona: preguntarle
# al modelo algo que ya esta escrito cuesta quince segundos y, medido el 10
# de noviembre, termina en abstencion incluso con frases tan directas como
# "¿que tengo pendiente?". Solo entran palabras sin otro uso posible entre
# las nueve operaciones; ante la menor duda, decide el modelo.
PALABRAS_DE_OPERACION = {
    "ENTREGA_VER": ("pendiente", "pendientes"),
}

# Verbos de registro. Desactivan la ruta rapida aunque aparezca una palabra
# de operacion: "anota la entrega pendiente de Maria" contiene "pendiente"
# pero pide registrar, no consultar. Ahi hay ambiguedad real y corresponde
# clasificar.
PALABRAS_DE_REGISTRO = ("registra", "anota", "marca", "apunta", "recibid")


def operacion_explicita(texto: str) -> Optional[str]:
    """Operacion identificada por una palabra inequivoca, o None.

    Devolver None no es un fallo: significa que la orden no se puede
    resolver sin interpretar, y entonces se consulta al modelo.
    """
    t = normalizar(texto)
    if any(p in t for p in PALABRAS_DE_REGISTRO):
        return None
    for accion, palabras in PALABRAS_DE_OPERACION.items():
        if any(p in t for p in palabras):
            return accion
    return None


def menciona_todo(texto: str) -> bool:
    """Si la orden declara el aula completa de forma explicita."""
    t = normalizar(texto)
    return any(p in t for p in PALABRAS_DE_TODO)


def zona_concreta(zona: Optional[str]) -> bool:
    """Si la zona identifica una sola zona de la instalacion.

    El modelo devuelve "NINGUNA" cuando no reconoce ninguna, y la ausencia
    de zona se representa con None. Ambos significan el aula completa.
    """
    return bool(zona) and zona != "NINGUNA"


def requiere_confirmacion(accion: str, zona: Optional[str] = None) -> bool:
    """Si la operacion debe anunciarse y esperar confirmacion (RF-09).

    Dos razones distintas, y conviene no confundirlas:

      irreversibilidad   registrar una entrega modifica un registro que el
                         usuario no puede deshacer desde la interfaz.
      alcance            apagar el aula completa es reversible, pero afecta
                         a todos los presentes a la vez. Apagar una sola
                         zona no lo es, y pedir confirmacion ahi costaria
                         quince segundos en la orden mas frecuente.

    El parametro de zona es opcional porque inferencia.py construye la
    decision antes de conocerla; alli se recalcula una vez resuelta.
    """
    if accion in ACCIONES_IRREVERSIBLES:
        return True
    if accion in ACCIONES_DE_ALCANCE_TOTAL and not zona_concreta(zona):
        return True
    return False


def requiere_generacion(accion: str) -> bool:
    return accion in ACCIONES_GENERATIVAS


# Sinonimos frecuentes observados en el banco de frases. Algunos son
# ambiguos fuera de contexto —"prueba" aparece en "quien entrego la prueba",
# "cerrar" en "cerra la sesion"— de modo que por si solos no bastan para
# tomar la ruta rapida: ver escena_explicita().
ALIAS_ESCENA = {
    "proyeccion": ("proyectar", "pantalla", "presentacion", "cine"),
    "lectura":    ("leer", "individual"),
    "grupos":     ("grupo", "equipos", "subgrupos"),
    "examen":     ("prueba", "quiz", "evaluacion"),
    "salida":     ("salir", "terminamos", "cerrar"),
}

# Palabras que declaran la intencion de cambiar el ambiente del aula. Junto a
# un sinonimo ambiguo lo vuelven inequivoco.
PALABRAS_DE_ESCENA = ("escena", "modo", "ambiente")


def normalizar(texto: str) -> str:
    """Minusculas sin acentos.

    El usuario escribe "proyeccion" o "proyección" indistintamente, y la
    interfaz propone la forma acentuada. Comparar sin normalizar hace que la
    sugerencia del propio portal no coincida con el catalogo.
    """
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower())
                   if unicodedata.category(c) != "Mn")


def escena_en(texto: str) -> str | None:
    """Busca una escena conocida mencionada en la orden, con sinonimos.

    Resuelto por coincidencia de texto y no por el modelo: una cuarta pregunta
    costaria unos 130 tokens y siete segundos para distinguir cinco opciones
    que el usuario nombra literalmente.

    Es permisiva a proposito: se invoca cuando el modelo ya clasifico la
    orden como cambio de escena, de modo que el contexto esta establecido y
    lo unico que falta es cual.
    """
    t = normalizar(texto)
    for escena in ESCENAS_CONOCIDAS:
        if escena in t:
            return escena
    for escena, palabras in ALIAS_ESCENA.items():
        if any(p in t for p in palabras):
            return escena
    return None


def escena_explicita(texto: str) -> str | None:
    """Escena nombrada de forma inequivoca, para resolver sin el modelo.

    Mas estricta que escena_en() porque decide sin que nadie haya
    establecido el contexto: basta el nombre exacto de la escena, o un
    sinonimo acompanado de una palabra que declare la intencion. Sin esa
    exigencia, "quien entrego la prueba" aplicaria la escena de examen.

    Medido el 9 de noviembre: las tres frases de escena del banco —"pone el
    aula en modo proyeccion", "cambia a modo lectura", "activa la escena de
    trabajo en grupo"— se resuelven por esta via, que el modelo clasificaba
    mal en las tres mediciones anteriores.
    """
    t = normalizar(texto)
    for escena in ESCENAS_CONOCIDAS:
        if escena in t:
            return escena
    if not any(p in t for p in PALABRAS_DE_ESCENA):
        return None
    for escena, palabras in ALIAS_ESCENA.items():
        if any(p in t for p in palabras):
            return escena
    return None


def costo_estimado(con_zona: bool = False) -> str:
    """Resumen legible del costo de una consulta, para diagnostico."""
    base, extra = 277, 144
    n = base + (extra if con_zona else 0)
    return f"{n} tokens, ~{n / 18.5:.1f} s a 18,5 tok/s"
