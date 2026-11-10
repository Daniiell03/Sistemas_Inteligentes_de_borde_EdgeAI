#!/usr/bin/env python3
"""Zona y color por texto, y confirmacion por alcance.

POR QUE
-------
Tres observaciones del 10 de noviembre, con una sola raiz.

1. «enciende luz de frente con color rojo» encendio la zona correcta en
   verde. El color no se honraba porque LUZ_ON no lo recibe: el catalogo
   solo tiene encender, apagar y escenas.

2. El banco clasifico la zona mal en dos de tres casos. «apaga la
   iluminacion del fondo» devolvio NINGUNA con 0,18 de confianza, con la
   palabra "fondo" escrita en la propia orden. Y esa segunda llamada cuesta
   7,6 s: la orden completa tarda 22,9 s en lugar de 15.

3. «apaga todas las luces» se ejecuto sin pedir confirmacion.

Las dos primeras son el mismo error de diseno que ya corregimos para las
escenas: preguntarle al modelo algo que esta escrito literalmente en la
orden. Resolver la zona por texto no solo acierta siempre, sino que permite
omitir la segunda llamada, y esa es la operacion mas frecuente del sistema.

La tercera no era un defecto. El catalogo declara ACCIONES_IRREVERSIBLES =
{"ENTREGA_REG"} y apagar luces es reversible, de modo que el agente hizo lo
que esta escrito. Pero apagar el aula completa es disruptivo aunque sea
reversible, y eso no es lo mismo que apagar una zona. La distincion correcta
es por alcance, no por operacion: pedir confirmacion en toda orden de
apagado costaria quince segundos de ida y vuelta en la orden mas comun.

QUE CAMBIA
----------
  catalogo.py    ALIAS_ZONA y zona_explicita(); requiere_confirmacion() pasa
                 a considerar el alcance, con el parametro de zona opcional
                 para no romper la llamada de inferencia.py.
  inferencia.py  clasificar() acepta zona_conocida y omite la segunda
                 llamada cuando la recibe; el riesgo se recalcula una vez
                 conocida la zona, que es el unico momento en que ese dato
                 existe.
  agente.py      resuelve la zona antes de clasificar; _luz() aplica color;
                 la confirmacion cubre el apagado total; _resolver_pendiente
                 deja de asumir que todo lo pendiente es una entrega.

UNA DECISION DE UBICACION
-------------------------
Los nombres de color se resuelven contra PALETTE dentro de agente.py, donde
ya esta en alcance, y no en el catalogo. Duplicar la paleta en dos modulos
garantiza que se desincronicen: si manana se agrega un color a la HAL, el
agente lo reconoce sin tocar nada mas.

Uso:  python3 parche-zona-color.py app/jarvis
"""

import sys
from pathlib import Path

# ==========================================================================
# catalogo.py
# ==========================================================================
CONF_VIEJO = '''def requiere_confirmacion(accion: str) -> bool:
    return accion in ACCIONES_IRREVERSIBLES
'''

CONF_NUEVO = '''# Sinonimos de zona observados en el banco de frases. La zona esta escrita
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
'''

IRREV_VIEJO = '''ACCIONES_IRREVERSIBLES = frozenset({"ENTREGA_REG"})
'''

IRREV_NUEVO = '''ACCIONES_IRREVERSIBLES = frozenset({"ENTREGA_REG"})

# Operaciones reversibles que, sin zona, afectan el aula entera y por lo
# tanto se confirman por alcance y no por irreversibilidad. Encender no esta
# aqui: encender de mas molesta mucho menos que apagar de mas.
ACCIONES_DE_ALCANCE_TOTAL = frozenset({"LUZ_OFF"})
'''

# ==========================================================================
# inferencia.py
# ==========================================================================
CLASIF_FIRMA_VIEJA = '''def clasificar(orden: str) -> Decision:
    """Clasifica una orden en lenguaje natural contra el catalogo.

    Hace una llamada de accion y, solo si la accion lo requiere, una segunda
    de zona. Devuelve la decision aunque no supere el umbral: decidir si se
    actua es responsabilidad del agente, no de este modulo.
    """
'''

CLASIF_FIRMA_NUEVA = '''def clasificar(orden: str, zona_conocida=None) -> Decision:
    """Clasifica una orden en lenguaje natural contra el catalogo.

    Hace una llamada de accion y, solo si la accion lo requiere y la zona no
    viene resuelta, una segunda de zona. Devuelve la decision aunque no
    supere el umbral: decidir si se actua es responsabilidad del agente, no
    de este modulo.

    zona_conocida permite al agente entregar la zona ya resuelta por
    coincidencia de texto. Cuando la entrega, la segunda llamada se omite:
    son 7,6 s menos en la orden mas frecuente del sistema.
    """
'''

ZONA_VIEJA = '''    # Segunda llamada solo cuando la operacion actua sobre una zona, y solo
    # si la primera fue confiable: preguntar la zona de una clasificacion que
    # se va a descartar son siete segundos tirados.
    if catalogo.requiere_zona(accion) and d.confiable:
'''

ZONA_NUEVA = '''    # La zona puede venir resuelta por texto desde el agente, que es el caso
    # normal: casi toda orden de iluminacion la nombra. Cuando viene, la
    # segunda llamada se omite. Medido el 10 de noviembre, el modelo la
    # clasificaba mal en dos de tres casos —NINGUNA con 0,18 de confianza
    # para "apaga la iluminacion del fondo"— y cobraba 7,6 s por hacerlo.
    if zona_conocida is not None:
        d.zona = zona_conocida
        d.confianza_zona = 1.0
    # Segunda llamada solo cuando la operacion actua sobre una zona, no se
    # resolvio por texto, y la primera fue confiable: preguntar la zona de
    # una clasificacion que se va a descartar son siete segundos tirados.
    elif catalogo.requiere_zona(accion) and d.confiable:
'''

RETORNO_VIEJO = '''    return d


def generar('''

RETORNO_NUEVO = '''    # El riesgo depende de la zona en las operaciones que se confirman por
    # alcance, y la zona no existe hasta aqui. Se recalcula en lugar de
    # dejar el valor provisional que se fijo al construir la decision.
    d.riesgo = catalogo.requiere_confirmacion(accion, d.zona)
    return d


def generar('''

# ==========================================================================
# agente.py
# ==========================================================================
COLOR_VIEJO = '''NEGACIONES = ("no", "cancela", "cancelar", "olvidalo", "mejor no", "negativo")
'''

COLOR_NUEVO = '''NEGACIONES = ("no", "cancela", "cancelar", "olvidalo", "mejor no", "negativo")


def _color_en(orden: str) -> Optional[str]:
    """Color nombrado en la orden, resuelto contra la paleta de la HAL.

    Se recorre PALETTE en lugar de mantener una lista de colores aqui: si se
    agrega un color a la paleta, el agente lo reconoce sin tocar este
    modulo. Los nombres mas largos se prueban primero para que "azul
    profundo" no se resuelva como "azul".

    Como las escenas y la zona, se resuelve por texto y no por el modelo: el
    usuario nombra el color literalmente, y preguntarlo costaria una tercera
    llamada de siete segundos para distinguir opciones que ya estan escritas.
    """
    t = catalogo.normalizar(orden)
    for nombre in sorted(PALETTE, key=len, reverse=True):
        if catalogo.normalizar(nombre) in t:
            return nombre
    return None
'''

PENDIENTE_DOC_VIEJO = '''class _Pendiente:
    """Accion irreversible esperando confirmacion del usuario."""
'''

PENDIENTE_DOC_NUEVO = '''class _Pendiente:
    """Accion esperando confirmacion del usuario.

    Por irreversibilidad —registrar una entrega— o por alcance —apagar el
    aula completa—. Ver catalogo.requiere_confirmacion().
    """
'''

LLAMADA_VIEJA = '''        t0 = time.perf_counter()
        try:
            decision = inferencia.clasificar(orden)
'''

LLAMADA_NUEVA = '''        # La zona se resuelve por texto antes de clasificar y se entrega a la
        # inferencia, que entonces omite su segunda llamada. Son 7,6 s menos
        # en toda orden de iluminacion, y acierta donde el modelo fallaba.
        zona_texto = catalogo.zona_explicita(orden)

        t0 = time.perf_counter()
        try:
            decision = inferencia.clasificar(orden, zona_conocida=zona_texto)
'''

CONFIRMA_VIEJA = '''        # Accion irreversible: se anuncia y se espera confirmacion (RF-09).
        if catalogo.requiere_confirmacion(decision.accion):
            datos = self._extraer_entrega(orden)
            if not datos:
                self.almacen.anotar_decision(orden, decision.accion,
                                             decision.confianza, False, "falta dato")
                return Respuesta(
                    Parada.FALTA_DATO,
                    "¿De quién es la entrega y de qué trabajo?",
                    accion=decision.accion,
                    confianza=decision.confianza,
                    latencia=decision.latencia,
                )
            self._pendiente[rol] = _Pendiente(decision.accion, orden,
                                              decision.zona, time.monotonic())
            self.almacen.anotar_decision(orden, decision.accion,
                                         decision.confianza, False, "espera confirmacion")
            return Respuesta(
                Parada.CONFIRMACION,
                f"Voy a registrar la entrega de {datos['estudiante']} "
                f"({datos['trabajo']}). ¿Confirmás?",
                accion=decision.accion,
                confianza=decision.confianza,
                latencia=decision.latencia,
                datos=datos,
            )
'''

CONFIRMA_NUEVA = '''        # Se anuncia y se espera confirmacion (RF-09), por irreversibilidad o
        # por alcance. Los dos casos necesitan mensajes distintos: anunciar
        # "voy a hacerlo, confirma" sin decir que se va a hacer convierte la
        # confirmacion en un tramite y deja de proteger de nada.
        if catalogo.requiere_confirmacion(decision.accion, decision.zona):
            datos = {}
            if decision.accion == "ENTREGA_REG":
                datos = self._extraer_entrega(orden)
                if not datos:
                    self.almacen.anotar_decision(orden, decision.accion,
                                                 decision.confianza, False, "falta dato")
                    return Respuesta(
                        Parada.FALTA_DATO,
                        "¿De quién es la entrega y de qué trabajo?",
                        accion=decision.accion,
                        confianza=decision.confianza,
                        latencia=decision.latencia,
                    )
                aviso = (f"Voy a registrar la entrega de {datos['estudiante']} "
                         f"({datos['trabajo']}). ¿Confirmás?")
            else:
                aviso = ("Eso apaga la iluminación de todo el aula. "
                         "¿Confirmás?")

            self._pendiente[rol] = _Pendiente(decision.accion, orden,
                                              decision.zona, time.monotonic())
            self.almacen.anotar_decision(orden, decision.accion,
                                         decision.confianza, False, "espera confirmacion")
            return Respuesta(
                Parada.CONFIRMACION,
                aviso,
                accion=decision.accion,
                confianza=decision.confianza,
                latencia=decision.latencia,
                datos=datos,
            )
'''

RESOLVER_VIEJA = '''        del self._pendiente[rol]
        datos = self._extraer_entrega(pendiente.orden)
'''

RESOLVER_NUEVA = '''        del self._pendiente[rol]

        # Solo el registro de entregas necesita extraer datos de la orden
        # original. El resto se ejecuta por la via normal: asumir que todo
        # lo pendiente es una entrega fue correcto mientras ENTREGA_REG era
        # la unica operacion que se confirmaba.
        if pendiente.accion != "ENTREGA_REG":
            respuesta = self._actuar(rol, pendiente.orden,
                                     pendiente.accion, pendiente.zona)
            respuesta.confianza = 1.0
            self.almacen.anotar_decision(pendiente.orden, pendiente.accion,
                                         1.0, respuesta.ejecutada, "confirmada")
            return respuesta

        datos = self._extraer_entrega(pendiente.orden)
'''

ACTUAR_VIEJA = '''            if accion == "LUZ_ON":
                return self._luz(zona, encender=True)
            if accion == "LUZ_OFF":
                return self._luz(zona, encender=False)
'''

ACTUAR_NUEVA = '''            if accion == "LUZ_ON":
                return self._luz(zona, encender=True, color=_color_en(orden))
            if accion == "LUZ_OFF":
                return self._luz(zona, encender=False)
'''

LUZ_VIEJA = '''    def _luz(self, zona: Optional[str], encender: bool) -> Respuesta:
        objetivo = self._zonas_objetivo(zona)
        if not objetivo:
            return Respuesta(Parada.FALTA_DATO,
                             f"Esa zona no está disponible en esta instalación.",
                             accion="LUZ_ON" if encender else "LUZ_OFF")
        for r in objetivo:
            if encender:
                self.luces.turn_on(r)
            else:
                self.luces.turn_off(r)
'''

LUZ_NUEVA = '''    def _luz(self, zona: Optional[str], encender: bool,
             color: Optional[str] = None) -> Respuesta:
        objetivo = self._zonas_objetivo(zona)
        if not objetivo:
            return Respuesta(Parada.FALTA_DATO,
                             "Esa zona no está disponible en esta instalación.",
                             accion="LUZ_ON" if encender else "LUZ_OFF")
        for r in objetivo:
            if encender and color is not None:
                # Encender con un color nombrado es una sola operacion, no
                # encender y luego recolorear: dos llamadas producirian un
                # destello del color anterior.
                self.luces.set(r, PALETTE[color], 1.0)
            elif encender:
                self.luces.turn_on(r)
            else:
                self.luces.turn_off(r)
'''

LUZ_MSG_VIEJA = '''        nombres = ", ".join(r.value for r in objetivo)
        verbo = "Encendí" if encender else "Apagué"
        return Respuesta(Parada.CUMPLIDA, f"{verbo} la iluminación de: {nombres}.",
                         accion="LUZ_ON" if encender else "LUZ_OFF",
                         datos={"zonas": [r.name for r in objetivo]})
'''

LUZ_MSG_NUEVA = '''        nombres = ", ".join(r.value for r in objetivo)
        verbo = "Encendí" if encender else "Apagué"
        detalle = f" en {color}" if (encender and color) else ""
        return Respuesta(Parada.CUMPLIDA,
                         f"{verbo} la iluminación de: {nombres}{detalle}.",
                         accion="LUZ_ON" if encender else "LUZ_OFF",
                         datos={"zonas": [r.name for r in objetivo],
                                "color": color})
'''


def sustituir(s, viejo, nuevo, nombre, marca, hechos, omitidos):
    if marca in s:
        omitidos.append(nombre)
        return s
    if viejo not in s:
        print(f"  NO ENCONTRADO: {nombre}", file=sys.stderr)
        print(f"    se buscaba: {viejo.strip().splitlines()[0][:76]}",
              file=sys.stderr)
        raise SystemExit(1)
    if s.count(viejo) > 1:
        print(f"  AMBIGUO: {nombre} aparece {s.count(viejo)} veces",
              file=sys.stderr)
        raise SystemExit(1)
    hechos.append(nombre)
    return s.replace(viejo, nuevo, 1)


def main(paquete: Path) -> int:
    hechos, omitidos = [], []

    cat = paquete / "catalogo.py"
    inf = paquete / "inferencia.py"
    age = paquete / "agente.py"
    for f in (cat, inf, age):
        if not f.is_file():
            print(f"no existe: {f}", file=sys.stderr)
            return 1

    # -- catalogo ----------------------------------------------------------
    s = cat.read_text(encoding="utf-8")
    s = sustituir(s, IRREV_VIEJO, IRREV_NUEVO,
                  "conjunto de operaciones de alcance total",
                  "ACCIONES_DE_ALCANCE_TOTAL", hechos, omitidos)
    s = sustituir(s, CONF_VIEJO, CONF_NUEVO,
                  "zona por texto y confirmacion por alcance",
                  "def zona_explicita", hechos, omitidos)
    cat.write_text(s, encoding="utf-8")

    # -- inferencia --------------------------------------------------------
    s = inf.read_text(encoding="utf-8")
    s = sustituir(s, CLASIF_FIRMA_VIEJA, CLASIF_FIRMA_NUEVA,
                  "firma de clasificar con zona conocida",
                  "zona_conocida", hechos, omitidos)
    s = sustituir(s, ZONA_VIEJA, ZONA_NUEVA,
                  "omision de la segunda llamada",
                  "if zona_conocida is not None", hechos, omitidos)
    s = sustituir(s, RETORNO_VIEJO, RETORNO_NUEVO,
                  "recalculo del riesgo con la zona",
                  "d.riesgo = catalogo.requiere_confirmacion", hechos, omitidos)
    inf.write_text(s, encoding="utf-8")

    # -- agente ------------------------------------------------------------
    s = age.read_text(encoding="utf-8")
    s = sustituir(s, PENDIENTE_DOC_VIEJO, PENDIENTE_DOC_NUEVO,
                  "documentacion de _Pendiente", "o por alcance",
                  hechos, omitidos)
    s = sustituir(s, COLOR_VIEJO, COLOR_NUEVO,
                  "resolucion de color contra la paleta",
                  "def _color_en", hechos, omitidos)
    s = sustituir(s, LLAMADA_VIEJA, LLAMADA_NUEVA,
                  "zona resuelta antes de clasificar",
                  "zona_texto", hechos, omitidos)
    s = sustituir(s, CONFIRMA_VIEJA, CONFIRMA_NUEVA,
                  "confirmacion con dos mensajes",
                  "apaga la iluminación de todo el aula", hechos, omitidos)
    s = sustituir(s, RESOLVER_VIEJA, RESOLVER_NUEVA,
                  "resolucion de pendientes no de entrega",
                  'pendiente.accion != "ENTREGA_REG"', hechos, omitidos)
    s = sustituir(s, ACTUAR_VIEJA, ACTUAR_NUEVA,
                  "color en la actuacion", "color=_color_en(orden)",
                  hechos, omitidos)
    s = sustituir(s, LUZ_VIEJA, LUZ_NUEVA,
                  "_luz aplica color", "self.luces.set(r, PALETTE[color]",
                  hechos, omitidos)
    s = sustituir(s, LUZ_MSG_VIEJA, LUZ_MSG_NUEVA,
                  "mensaje de luz con color", 'detalle = f" en {color}"',
                  hechos, omitidos)
    age.write_text(s, encoding="utf-8")

    for h in hechos:
        print(f"  aplicado: {h}")
    for o in omitidos:
        print(f"  ya estaba: {o}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(Path(sys.argv[1])))
