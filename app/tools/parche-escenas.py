#!/usr/bin/env python3
"""Resuelve las escenas antes de consultar al modelo.

POR QUE
-------
La operacion LUZ_MODO fallo en tres mediciones independientes (28 de
octubre, 6 y 9 de noviembre), clasificada siempre como NADA. La calibracion
del 9 de noviembre mostro que la causa no es que el modelo desconozca la
operacion: aislada contra NADA la acierta tres de tres. Lo que falla es el
margen —la medida que el endpoint reporta como confianza— porque NADA,
descrita como "Fuera de alcance", compite de forma plausible con cualquier
frase y deja el margen por debajo del umbral de abstencion.

El catalogo ya resolvia las escenas por coincidencia de texto, y lo
documenta: preguntarlas al modelo costaria unos 130 tokens y siete segundos
para distinguir cinco opciones que el usuario nombra literalmente. Lo que
faltaba era aplicar esa decision en el lugar correcto del ciclo. Hoy el
agente clasifica primero —quince segundos— y recien despues busca la
escena; si la clasificacion se abstiene, la escena nunca se aplica aunque
estuviera escrita en la orden.

Este parche invierte el orden. Cuando la orden nombra una escena de forma
inequivoca, el agente la aplica directamente: cero tokens, respuesta
inmediata, sin posibilidad de error de clasificacion. El modelo se consulta
solo cuando la orden no nombra ninguna.

No es evitar al agente: el agente coordina recursos, y la coincidencia de
texto es el recurso mas barato que resuelve el caso de forma determinista.
Debe decirse explicitamente en la documentacion y en la demostracion, porque
esa ruta no consulta ningun modelo.

QUE CAMBIA
----------
  catalogo.py   escena_en() normaliza acentos; se agrega escena_explicita(),
                que exige una senal inequivoca para la ruta rapida.
  agente.py     procesar() resuelve la escena antes de clasificar.

Uso:  python3 parche-escenas.py app/jarvis
"""

import sys
from pathlib import Path

# --------------------------------------------------------------------------
# catalogo.py
# --------------------------------------------------------------------------
IMPORT_VIEJO = "from typing import Dict, Any\n"
IMPORT_NUEVO = "import unicodedata\nfrom typing import Dict, Any\n"

ESCENA_VIEJA = '''def escena_en(texto: str) -> str | None:
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
'''

ESCENA_NUEVA = '''# Sinonimos frecuentes observados en el banco de frases. Algunos son
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
'''

# --------------------------------------------------------------------------
# agente.py
# --------------------------------------------------------------------------
CLASIF_VIEJA = """        t0 = time.perf_counter()
        try:
            decision = inferencia.clasificar(orden)
"""

CLASIF_NUEVA = """        # Ruta rapida: si la orden nombra una escena de forma inequivoca, se
        # aplica sin consultar al modelo. Cero tokens y respuesta inmediata,
        # frente a los quince segundos y la clasificacion poco fiable que
        # midieron las campanas del 28/10, 6/11 y 9/11. El catalogo ya
        # resolvia la escena por texto; lo que faltaba era hacerlo antes de
        # clasificar y no despues.
        escena = catalogo.escena_explicita(orden)
        if escena is not None:
            t0 = time.perf_counter()
            respuesta = self._actuar(rol, orden, "LUZ_MODO", None)
            respuesta.confianza = 1.0
            respuesta.latencia = time.perf_counter() - t0
            self.almacen.anotar_decision(
                orden, "LUZ_MODO", 1.0, respuesta.ejecutada,
                "resuelta por coincidencia de texto, sin consultar al modelo")
            return respuesta

        t0 = time.perf_counter()
        try:
            decision = inferencia.clasificar(orden)
"""


def sustituir(s: str, viejo: str, nuevo: str, nombre: str, marca: str,
              hechos: list, omitidos: list) -> str:
    if marca in s:
        omitidos.append(nombre)
        return s
    if viejo not in s:
        print(f"  NO ENCONTRADO: {nombre}", file=sys.stderr)
        print(f"    se buscaba: {viejo.strip().splitlines()[0][:76]}",
              file=sys.stderr)
        raise SystemExit(1)
    hechos.append(nombre)
    return s.replace(viejo, nuevo, 1)


def main(paquete: Path) -> int:
    hechos, omitidos = [], []

    cat = paquete / "catalogo.py"
    age = paquete / "agente.py"
    for f in (cat, age):
        if not f.is_file():
            print(f"no existe: {f}", file=sys.stderr)
            return 1

    s = cat.read_text(encoding="utf-8")
    s = sustituir(s, IMPORT_VIEJO, IMPORT_NUEVO,
                  "import de unicodedata", "import unicodedata",
                  hechos, omitidos)
    s = sustituir(s, ESCENA_VIEJA, ESCENA_NUEVA,
                  "escena_en normalizada y escena_explicita",
                  "def escena_explicita", hechos, omitidos)
    cat.write_text(s, encoding="utf-8")

    s = age.read_text(encoding="utf-8")
    s = sustituir(s, CLASIF_VIEJA, CLASIF_NUEVA,
                  "ruta rapida de escenas en procesar()",
                  "escena_explicita", hechos, omitidos)
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
