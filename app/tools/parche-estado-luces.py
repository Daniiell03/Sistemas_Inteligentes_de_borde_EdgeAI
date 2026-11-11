#!/usr/bin/env python3
"""Consulta del estado de las luces, sin tocar el catalogo del modelo.

POR QUE
-------
«¿Cuales son las luces del aula?» no obtiene respuesta: el catalogo tiene
encender, apagar y escenas —las tres de actuacion— pero ninguna de consulta.

La salida obvia seria agregar una decima operacion. Seria un error medible.
Cada criterio cuesta unos diecisiete tokens en el prompt, y ese prompt se
envia en TODAS las ordenes del sistema: el presupuesto de 277 tokens, que
hoy da exactamente quince segundos, se rompe. Ademas cambia la competencia
entre criterios, de modo que el umbral de abstencion calibrado el 9 de
noviembre dejaria de ser valido y habria que repetir la curva.

No hace falta. La respuesta se construye leyendo la capa de hardware: el
modelo no aportaria nada que no este ya en la memoria del proceso. Y la
pregunta se identifica por texto sin ambiguedad.

De ahi la regla que este parche deja establecida, y que conviene enunciar
explicitamente porque gobierna el diseno:

    Una operacion que se identifica por coincidencia de texto y cuya
    respuesta no requiere interpretacion no necesita estar en el catalogo
    del modelo. El catalogo es la lista de lo que hay que PREGUNTAR, no la
    lista de lo que el sistema sabe hacer.

Asi, LUZ_VER existe para el agente y para el portal, pero el modelo nunca
la ve: el catalogo sigue teniendo nueve criterios y 277 tokens.

QUE CAMBIA
----------
  catalogo.py   LUZ_VER entre las operaciones identificadas por texto, y un
                guardado contra los verbos de actuacion.
  agente.py     _estado_luces(), que lee la HAL y describe cada zona.
  portal.py     LUZ_VER se asocia a la vista de luces en la interfaz.
  probar_agente.py  verificacion de que responde sin consultar al modelo.

SOBRE LOS PERMISOS
------------------
Consultar es de lectura, de modo que LUZ_VER no entra en SOLO_PROFESOR: un
estudiante puede preguntar como estan las luces, igual que puede consultar
el ambiente. Lo que no puede es cambiarlas.

Uso:  python3 parche-estado-luces.py app/jarvis
"""

import sys
from pathlib import Path

# ==========================================================================
# catalogo.py
# ==========================================================================
CAT_VIEJO = '''PALABRAS_DE_OPERACION = {
    "ENTREGA_VER": ("pendiente", "pendientes"),
}

# Verbos de registro. Desactivan la ruta rapida aunque aparezca una palabra
# de operacion: "anota la entrega pendiente de Maria" contiene "pendiente"
# pero pide registrar, no consultar. Ahi hay ambiguedad real y corresponde
# clasificar.
PALABRAS_DE_REGISTRO = ("registra", "anota", "marca", "apunta", "recibid")'''

CAT_NUEVO = '''PALABRAS_DE_OPERACION = {
    "ENTREGA_VER": ("pendiente", "pendientes"),
    # Consultar el estado de la iluminacion. No es una operacion del
    # catalogo del modelo y no lo sera: la respuesta se lee de la capa de
    # hardware, de modo que el modelo no aportaria nada, y un decimo
    # criterio costaria diecisiete tokens en todas las ordenes del sistema.
    "LUZ_VER": ("cuales son las luces", "cuales luces", "que luces",
                "estado de las luces", "como estan las luces",
                "como estan las luz", "estado de la iluminacion"),
}

# Verbos de actuacion. Desactivan la ruta rapida aunque aparezca una palabra
# de operacion, porque ahi la ambiguedad es real y corresponde clasificar:
# "anota la entrega pendiente de Maria" contiene "pendiente" pero pide
# registrar, y "que luces apago" contiene "que luces" pero pide apagar.
PALABRAS_DE_REGISTRO = ("registra", "anota", "marca", "apunta", "recibid",
                        "encend", "enciend", "prend", "apag", "ilumina")'''

# ==========================================================================
# agente.py — actuacion
# ==========================================================================
AGE_ACTUAR_VIEJA = '''            if accion == "LUZ_MODO":
                return self._escena(orden)'''

AGE_ACTUAR_NUEVA = '''            if accion == "LUZ_MODO":
                return self._escena(orden)
            if accion == "LUZ_VER":
                return self._estado_luces()'''

# ==========================================================================
# agente.py — la consulta de estado
# ==========================================================================
AGE_ESCENA_VIEJA = '''    def _escena(self, orden: str) -> Respuesta:'''

AGE_ESCENA_NUEVA = '''    def _estado_luces(self) -> Respuesta:
        """Estado actual de cada zona, leido de la capa de hardware.

        No consulta al modelo y no esta en su catalogo: la pregunta se
        identifica por texto y la respuesta esta en la memoria del proceso.
        Es la misma informacion que muestra la pestana del aula, dicha en
        palabras para quien pregunta por el asistente.
        """
        partes, zonas = [], {}
        for r in sorted(self.luces.available(), key=lambda z: z.value):
            st = self.luces.get(r)
            if st is None or not st.is_on:
                partes.append(f"{r.value}: apagada")
                zonas[r.name] = {"encendida": False}
                continue
            nombre = next((k for k, v in PALETTE.items() if v == st.color),
                          "color")
            partes.append(f"{r.value}: {nombre} al "
                          f"{round(st.brightness * 100)} por ciento")
            zonas[r.name] = {"encendida": True, "color": nombre,
                             "brillo": st.brightness}

        if not partes:
            return Respuesta(Parada.FALTA_DATO,
                             "No hay zonas de iluminación en esta instalación.",
                             accion="LUZ_VER")
        return Respuesta(Parada.CUMPLIDA, "Luces: " + "; ".join(partes) + ".",
                         accion="LUZ_VER", datos={"zonas": zonas})

    def _escena(self, orden: str) -> Respuesta:'''

# ==========================================================================
# portal.py
# ==========================================================================
POR_VIEJO = '''INTENTO_VISIBLE = {"LUZ_ON": "LUCES", "LUZ_OFF": "LUCES", "LUZ_MODO": "LUCES",'''

POR_NUEVO = '''INTENTO_VISIBLE = {"LUZ_ON": "LUCES", "LUZ_OFF": "LUCES", "LUZ_MODO": "LUCES",
                   "LUZ_VER": "LUCES",'''

# ==========================================================================
# probar_agente.py
# ==========================================================================
TEST_VIEJO = '''        b.verificar("zona declarada pero no disponible devuelve falta de dato",
                    r.motivo is Parada.FALTA_DATO,
                    f"motivo={r.motivo.value} mensaje={r.mensaje!r}")'''

TEST_NUEVO = '''        b.verificar("zona declarada pero no disponible devuelve falta de dato",
                    r.motivo is Parada.FALTA_DATO,
                    f"motivo={r.motivo.value} mensaje={r.mensaje!r}")

        # Consultar el estado se resuelve leyendo la HAL: ni se programa una
        # clasificacion ni debe consumirse ninguna. Es la propiedad que
        # justifica que LUZ_VER no ocupe un criterio del catalogo.
        antes_luz = len(falso.consultas)
        r = agente.procesar("estudiante", "cuáles son las luces del aula")
        b.verificar("consultar el estado de las luces no consulta al modelo",
                    r.motivo is Parada.CUMPLIDA and len(falso.consultas) == antes_luz,
                    f"motivo={r.motivo.value} consultas={len(falso.consultas)}"
                    f" mensaje={r.mensaje!r}")

        # Y la guarda contra el falso positivo: lleva "que luces" pero pide
        # actuar, de modo que tiene que llegar al modelo.
        falso.programar("LUZ_OFF", 0.9, "FRENTE")
        r = agente.procesar("profesor", "qué luces apago del frente")
        b.verificar("una orden de actuacion no toma la ruta rapida",
                    r.accion == "LUZ_OFF",
                    f"accion={r.accion} motivo={r.motivo.value}")'''


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
    age = paquete / "agente.py"
    por = paquete / "portal.py"
    tst = paquete.parent / "tools" / "probar_agente.py"
    for f in (cat, age, por, tst):
        if not f.is_file():
            print(f"no existe: {f}", file=sys.stderr)
            return 1

    s = cat.read_text(encoding="utf-8")
    s = sustituir(s, CAT_VIEJO, CAT_NUEVO,
                  "LUZ_VER identificada por texto", '"LUZ_VER": (',
                  hechos, omitidos)
    cat.write_text(s, encoding="utf-8")

    s = age.read_text(encoding="utf-8")
    s = sustituir(s, AGE_ACTUAR_VIEJA, AGE_ACTUAR_NUEVA,
                  "LUZ_VER en la actuacion", 'accion == "LUZ_VER"',
                  hechos, omitidos)
    s = sustituir(s, AGE_ESCENA_VIEJA, AGE_ESCENA_NUEVA,
                  "lectura del estado de las luces", "def _estado_luces",
                  hechos, omitidos)
    age.write_text(s, encoding="utf-8")

    s = por.read_text(encoding="utf-8")
    s = sustituir(s, POR_VIEJO, POR_NUEVO,
                  "LUZ_VER en la vista del portal", '"LUZ_VER": "LUCES"',
                  hechos, omitidos)
    por.write_text(s, encoding="utf-8")

    s = tst.read_text(encoding="utf-8")
    s = sustituir(s, TEST_VIEJO, TEST_NUEVO,
                  "verificaciones del estado de las luces", "antes_luz",
                  hechos, omitidos)
    tst.write_text(s, encoding="utf-8")

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
