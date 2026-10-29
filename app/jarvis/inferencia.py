"""Cliente de los dos carriles de inferencia local (Rol A).

El sistema usa dos modelos distintos sobre el mismo servidor local:

    carril de decision    tev1:0.8b     POST /v1/systemone    clasificacion
                                                              tipada con
                                                              probabilidad
    carril generativo     llama3.2:1b   POST /api/chat        redaccion de
                                                              lenguaje natural

El endpoint de decision no sirve modelos de chat y el modelo de decision no
genera tokens: de ahi que se requieran dos modelos y un coordinador entre
ellos (RS-05). Ambos quedan residentes en memoria por configuracion del
servicio, de modo que ninguna consulta paga la carga desde la MicroSD (R1).

ESQUEMA DE LLAMADA
------------------
La clasificacion se resuelve en una sola etapa. La zona se consulta en una
segunda llamada, y solo cuando la accion resultante la necesita:

    orden que no es de luz   ->  1 llamada   277 tok   15,0 s
    orden de iluminacion     ->  2 llamadas  421 tok   22,6 s

Preguntar la zona siempre costaria 7,5 s en las 23 de cada 27 ordenes que no
la usan. Las dos alternativas evaluadas el 28 de octubre resultaron peores o
iguales: plegar la zona dentro de las acciones da 377 tokens y 20,4 s con
peor granularidad, y las tres preguntas juntas dan 533 tokens y 22,5 s para
todas las ordenes por igual.

TIEMPOS DE ESPERA
-----------------
A 18,5 tokens por segundo, una consulta normal tarda quince segundos. El
tiempo de espera no es un margen de seguridad sobre un valor tipico: es el
limite a partir del cual algo esta roto. Se fija holgado para no abortar una
consulta valida bajo carga, y la aplicacion informa el estado de
procesamiento al usuario mientras tanto (RF-10).

Solo biblioteca estandar: la imagen no necesita un cliente HTTP adicional.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from . import catalogo

SERVIDOR = "http://127.0.0.1:11434"
MODELO_DECISION = "tev1:0.8b"
MODELO_GENERATIVO = "llama3.2:1b"

TIMEOUT_DECISION = 90.0
TIMEOUT_GENERATIVO = 120.0

MAX_TOKENS_GENERATIVO = 40
"""Limite de salida del carril generativo (RD-02).

A 2,6 tokens por segundo de generacion, cada token de respuesta cuesta cerca
de 0,4 s. Cuarenta tokens son unos diecisiete segundos; sin acotar, la
medicion del 27 de octubre arrojo hasta 45 s.
"""

INSTRUCCION_IDIOMA = (
    "Respondes siempre en espanol de Costa Rica, en una o dos frases breves. "
    "No saludas ni te presentas."
)
"""El modelo generativo es pequeno y multilingue: sin instruccion explicita
responde en portugues con frecuencia, por la cercania estructural con el
espanol. Observado el 28 de octubre."""


class ErrorInferencia(RuntimeError):
    """El servidor de inferencia no respondio o respondio algo inutilizable."""


@dataclass
class Decision:
    """Resultado de una consulta al carril de decision."""

    accion: str
    confianza: float
    zona: Optional[str] = None
    confianza_zona: Optional[float] = None
    riesgo: bool = False
    latencia: float = 0.0
    tokens: int = 0
    llamadas: int = 1
    probabilidades: Dict[str, float] = field(default_factory=dict)

    @property
    def confiable(self) -> bool:
        """Si la decision supera el umbral de abstencion (RF-02)."""
        return self.confianza >= catalogo.UMBRAL_CONFIANZA

    def __str__(self) -> str:
        z = f" zona={self.zona}" if self.zona else ""
        return (f"{self.accion}{z} conf={self.confianza:.2f} "
                f"{self.latencia:.1f}s {self.tokens}tok")


def _post(ruta: str, cuerpo: Dict[str, Any], timeout: float) -> Tuple[Dict[str, Any], float]:
    """Envia una solicitud JSON y devuelve (respuesta, segundos)."""
    datos = json.dumps(cuerpo).encode("utf-8")
    req = urllib.request.Request(
        SERVIDOR + ruta,
        data=datos,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            respuesta = json.loads(r.read())
    except urllib.error.URLError as e:
        raise ErrorInferencia(
            f"no se pudo consultar {ruta}: {e}. "
            f"Verificar que el servicio de inferencia este activo."
        ) from e
    except json.JSONDecodeError as e:
        raise ErrorInferencia(f"respuesta ilegible de {ruta}: {e}") from e
    return respuesta, time.perf_counter() - t0


def _elegida(respuesta: Dict[str, Any], clave: str) -> Tuple[Optional[str], float, Dict[str, float]]:
    """Extrae (opcion, confianza, probabilidades) de una respuesta tipada.

    Tolera variaciones en la forma de la respuesta: la opcion puede venir como
    'choice', 'answer' o 'value', y la confianza como 'confidence', como
    'probability' o derivarse del mapa de probabilidades.
    """
    bloque = respuesta.get("answers", respuesta).get(clave, {})
    if isinstance(bloque, str):
        return bloque, 0.0, {}
    opcion = bloque.get("choice") or bloque.get("answer") or bloque.get("value")
    probs = bloque.get("probabilities") or {}
    conf = bloque.get("confidence")
    if conf is None:
        conf = bloque.get("probability") or bloque.get("score")
    if conf is None and opcion and isinstance(probs, dict):
        conf = probs.get(opcion)
    return opcion, float(conf or 0.0), {k: float(v) for k, v in probs.items()}


def _tokens(respuesta: Dict[str, Any]) -> int:
    uso = respuesta.get("usage") or {}
    return int(uso.get("input_tokens") or uso.get("prompt_eval_count") or 0)


def clasificar(orden: str) -> Decision:
    """Clasifica una orden en lenguaje natural contra el catalogo.

    Hace una llamada de accion y, solo si la accion lo requiere, una segunda
    de zona. Devuelve la decision aunque no supere el umbral: decidir si se
    actua es responsabilidad del agente, no de este modulo.
    """
    respuesta, t1 = _post(
        "/v1/systemone",
        {"model": MODELO_DECISION, "state": orden, "questions": catalogo.preguntas(orden)},
        TIMEOUT_DECISION,
    )
    accion, conf, probs = _elegida(respuesta, "accion")
    if accion is None:
        raise ErrorInferencia(f"el modelo no devolvio una accion: {respuesta}")

    d = Decision(
        accion=accion,
        confianza=conf,
        riesgo=catalogo.requiere_confirmacion(accion),
        latencia=t1,
        tokens=_tokens(respuesta),
        probabilidades=probs,
    )

    # Segunda llamada solo cuando la operacion actua sobre una zona, y solo
    # si la primera fue confiable: preguntar la zona de una clasificacion que
    # se va a descartar son siete segundos tirados.
    if catalogo.requiere_zona(accion) and d.confiable:
        r2, t2 = _post(
            "/v1/systemone",
            {"model": MODELO_DECISION, "state": orden, "questions": catalogo.preguntas_zona()},
            TIMEOUT_DECISION,
        )
        zona, conf_z, _ = _elegida(r2, "zona")
        d.zona = zona
        d.confianza_zona = conf_z
        d.latencia += t2
        d.tokens += _tokens(r2)
        d.llamadas = 2

    return d


def generar(instruccion: str, contexto: str = "", max_tokens: int = MAX_TOKENS_GENERATIVO) -> Tuple[str, float]:
    """Redacta una respuesta breve en lenguaje natural.

    Se invoca solo para las operaciones que requieren lenguaje (RF-07). El
    contexto lo compone la aplicacion a partir de su propio estado: el modelo
    no consulta documentos ni base de datos. Un contexto largo es caro —a
    18,5 tok/s, mil quinientos tokens son mas de un minuto solo en
    procesamiento— por lo que la recuperacion sobre documentos esta fuera de
    alcance.
    """
    mensaje = instruccion if not contexto else f"{contexto}\n\n{instruccion}"
    respuesta, t = _post(
        "/api/chat",
        {
            "model": MODELO_GENERATIVO,
            "stream": False,
            "messages": [
                {"role": "system", "content": INSTRUCCION_IDIOMA},
                {"role": "user", "content": mensaje},
            ],
            "options": {"num_predict": max_tokens},
        },
        TIMEOUT_GENERATIVO,
    )
    texto = (respuesta.get("message") or {}).get("content", "").strip()
    if not texto:
        raise ErrorInferencia(f"el modelo generativo no devolvio texto: {respuesta}")
    return texto, t


def disponible() -> bool:
    """Si el servidor de inferencia responde. Para diagnostico y arranque."""
    try:
        with urllib.request.urlopen(SERVIDOR + "/api/tags", timeout=5) as r:
            json.loads(r.read())
        return True
    except Exception:
        return False


def modelos_residentes() -> list[str]:
    """Modelos actualmente cargados en memoria.

    Util para verificar antes de la demostracion que ambos estan residentes:
    si falta uno, la primera orden que lo use paga la carga desde la MicroSD
    y viola R1.
    """
    try:
        with urllib.request.urlopen(SERVIDOR + "/api/ps", timeout=5) as r:
            datos = json.loads(r.read())
    except Exception:
        return []
    return [m.get("name", "") for m in datos.get("models", [])]


if __name__ == "__main__":
    import sys

    if not disponible():
        print("El servidor de inferencia no responde en", SERVIDOR)
        sys.exit(1)

    print("modelos residentes:", modelos_residentes() or "ninguno")
    orden = " ".join(sys.argv[1:]) or "encende la luz del frente"
    print(f"orden: {orden!r}")
    d = clasificar(orden)
    print(d)
    print("  confiable:", d.confiable, f"(umbral {catalogo.UMBRAL_CONFIANZA})")
    if catalogo.requiere_generacion(d.accion):
        texto, t = generar("Resumi la sesion de hoy en una frase.")
        print(f"  generativo ({t:.1f}s): {texto}")
