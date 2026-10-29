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


def requiere_confirmacion(accion: str) -> bool:
    return accion in ACCIONES_IRREVERSIBLES


def requiere_generacion(accion: str) -> bool:
    return accion in ACCIONES_GENERATIVAS


def escena_en(texto: str) -> str | None:
    """Busca una escena conocida mencionada en la orden.

    Resuelto por coincidencia de texto y no por el modelo: una cuarta pregunta
    costaria unos 130 tokens y siete segundos para distinguir cinco opciones
    que el usuario nombra literalmente.
    """
    t = texto.lower()
    for escena in ESCENAS_CONOCIDAS:
        if escena in t:
            return escena
    # Sinonimos frecuentes observados en el banco de frases.
    alias = {
        "proyeccion": ("proyectar", "pantalla", "presentacion", "cine"),
        "lectura":    ("leer", "individual"),
        "grupos":     ("grupo", "equipos", "subgrupos"),
        "examen":     ("prueba", "quiz", "evaluacion"),
        "salida":     ("salir", "terminamos", "cerrar"),
    }
    for escena, palabras in alias.items():
        if any(p in t for p in palabras):
            return escena
    return None


def costo_estimado(con_zona: bool = False) -> str:
    """Resumen legible del costo de una consulta, para diagnostico."""
    base, extra = 277, 144
    n = base + (extra if con_zona else 0)
    return f"{n} tokens, ~{n / 18.5:.1f} s a 18,5 tok/s"
