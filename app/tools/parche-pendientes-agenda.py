#!/usr/bin/env python3
"""Pendientes por texto, y la agenda que incluye las entregas.

POR QUE
-------
Dos observaciones del 10 de noviembre, probando desde el celular con los dos
roles conectados a la vez.

1. La interfaz sugiere «¿Que tengo pendiente?» como ejemplo de orden, y el
   agente se abstiene: "no entendi la orden con suficiente seguridad". La
   misma consulta escrita como «cuales entregas tengo pendientes» si acierta.
   Un boton que la propia interfaz ofrece y el sistema rechaza es peor que no
   ofrecerlo.

2. El agente responde «no hay nada en la agenda de hoy» con dos tareas con
   fecha limite a la vista en la pantalla de al lado. La interfaz ya funde
   actividades y entregas en una sola linea de tiempo; el agente seguia
   mirando solo la tabla de agenda.

QUE CAMBIA
----------
  catalogo.py      operacion_explicita(): palabras que identifican una
                   operacion sin ambiguedad, resueltas sin consultar al
                   modelo.
  agente.py        segunda ruta rapida en procesar(), y _agenda() que
                   tambien mira las entregas proximas.
  persistencia.py  tareas_proximas(): entregas con fecha aun no vencida.

EL CRITERIO PARA UNA RUTA RAPIDA
--------------------------------
Es el mismo que se aplico a las escenas, la zona y el color, y conviene
dejarlo escrito porque es una decision de diseno y no un atajo: se resuelve
por texto lo que esta escrito literalmente en la orden y no admite otra
lectura dentro de este catalogo. "Pendiente" cumple: ninguna otra de las
nueve operaciones trata de cosas pendientes.

Con una salvedad que se implementa explicitamente: «anota la entrega
pendiente de Maria» contiene la palabra pero pide registrar, no consultar.
Por eso la ruta rapida se desactiva ante cualquier verbo de registro, y en
ese caso decide el modelo, que es lo que corresponde cuando hay ambiguedad
real.

"¿Que se trabajo hoy?" —que tambien fallo, clasificada como NADA— no se
resuelve aqui: no contiene ninguna palabra inequivoca, y forzar una
coincidencia sobre una frase vaga seria adivinar. Esa queda como limitacion
documentada del clasificador.

Uso:  python3 parche-pendientes-agenda.py app/jarvis
"""

import sys
from pathlib import Path

# ==========================================================================
# catalogo.py
# ==========================================================================
CAT_VIEJO = '''def menciona_todo(texto: str) -> bool:'''

CAT_NUEVO = '''# Palabras que identifican una operacion sin ambiguedad dentro de este
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


def menciona_todo(texto: str) -> bool:'''

# ==========================================================================
# agente.py — segunda ruta rapida
# ==========================================================================
AGE_RAPIDA_VIEJA = '''        # La zona se resuelve por texto antes de clasificar y se entrega a la
        # inferencia, que entonces omite su segunda llamada. Son 7,6 s menos
        # en toda orden de iluminacion, y acierta donde el modelo fallaba.
        zona_texto = catalogo.zona_explicita(orden)'''

AGE_RAPIDA_NUEVA = '''        # Segunda ruta rapida, por la misma razon que la de escenas: la
        # palabra "pendiente" identifica la consulta de entregas sin
        # ambiguedad en este dominio. Medido el 10 de noviembre desde el
        # celular, el ejemplo que la propia interfaz sugiere —"¿Que tengo
        # pendiente?"— era rechazado por falta de confianza tras quince
        # segundos de espera.
        accion_texto = catalogo.operacion_explicita(orden)
        if accion_texto is not None:
            t0 = time.perf_counter()
            respuesta = self._actuar(rol, orden, accion_texto, None)
            respuesta.confianza = 1.0
            respuesta.latencia = time.perf_counter() - t0
            self.almacen.anotar_decision(
                orden, accion_texto, 1.0, respuesta.ejecutada,
                "resuelta por coincidencia de texto, sin consultar al modelo")
            return respuesta

        # La zona se resuelve por texto antes de clasificar y se entrega a la
        # inferencia, que entonces omite su segunda llamada. Son 7,6 s menos
        # en toda orden de iluminacion, y acierta donde el modelo fallaba.
        zona_texto = catalogo.zona_explicita(orden)'''

# ==========================================================================
# agente.py — la agenda incluye las entregas
# ==========================================================================
AGE_AGENDA_VIEJA = '''    def _agenda(self) -> Respuesta:
        eventos = self.almacen.agenda_de_hoy()
        if not eventos:
            return Respuesta(Parada.CUMPLIDA, "No hay nada en la agenda de hoy.",
                             accion="AGENDA", datos={"eventos": []})
        lista = "; ".join(e.descripcion for e in eventos)
        return Respuesta(Parada.CUMPLIDA, f"En la agenda de hoy: {lista}.",
                         accion="AGENDA",
                         datos={"eventos": [e.__dict__ for e in eventos]})
'''

AGE_AGENDA_NUEVA = '''    def _agenda(self) -> Respuesta:
        """Plan de la sesion: actividades de hoy mas entregas proximas.

        Una entrega con fecha limite es parte del plan tanto como un repaso.
        Mirar solo la tabla de agenda hacia que el agente respondiera "no hay
        nada programado" mientras la pantalla de al lado mostraba dos
        entregas con fecha, que es lo que se observo el 10 de noviembre.
        """
        eventos = self.almacen.agenda_de_hoy()
        tareas = self.almacen.tareas_proximas(3)

        if not eventos and not tareas:
            return Respuesta(
                Parada.CUMPLIDA,
                "No hay nada en la agenda de hoy ni entregas próximas.",
                accion="AGENDA", datos={"eventos": [], "tareas": []})

        partes = []
        if eventos:
            partes.append("En la agenda de hoy: "
                          + "; ".join(e.descripcion for e in eventos) + ".")
        if tareas:
            partes.append("Próximas entregas: "
                          + "; ".join(f"{t['titulo']}, {t['cuando']}"
                                      for t in tareas) + ".")

        return Respuesta(Parada.CUMPLIDA, " ".join(partes), accion="AGENDA",
                         datos={"eventos": [e.__dict__ for e in eventos],
                                "tareas": tareas})
'''

# ==========================================================================
# persistencia.py
# ==========================================================================
PER_VIEJO = '''    def borrar_evento(self, id_evento: int) -> None:
        with self._conexion() as cx:
            cx.execute("DELETE FROM agenda WHERE id = ?", (id_evento,))
'''

PER_NUEVO = '''    def borrar_evento(self, id_evento: int) -> None:
        with self._conexion() as cx:
            cx.execute("DELETE FROM agenda WHERE id = ?", (id_evento,))

    def tareas_proximas(self, limite: int = 3) -> List[dict]:
        """Entregas con fecha limite aun no vencida, de la mas proxima.

        Las fechas se guardan en formato ISO, de modo que la comparacion de
        texto ordena igual que la cronologica y no hace falta convertir cada
        fila para filtrar. La forma legible se arma aqui y no en el agente
        para no obligarlo a importar utilidades de fecha que no necesita
        para nada mas.
        """
        ahora = datetime.now().isoformat(timespec="minutes")
        with self._conexion() as cx:
            filas = cx.execute(
                "SELECT titulo, fecha_limite FROM tareas"
                " WHERE fecha_limite IS NOT NULL AND fecha_limite <> ''"
                "   AND fecha_limite >= ?"
                " ORDER BY fecha_limite LIMIT ?",
                (ahora, limite),
            ).fetchall()

        salida = []
        for f in filas:
            try:
                cuando = datetime.fromisoformat(
                    f["fecha_limite"]).strftime("%d/%m a las %H:%M")
            except ValueError:
                cuando = f["fecha_limite"]
            salida.append({"titulo": f["titulo"],
                           "fecha_limite": f["fecha_limite"],
                           "cuando": cuando})
        return salida
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
    age = paquete / "agente.py"
    per = paquete / "persistencia.py"
    for f in (cat, age, per):
        if not f.is_file():
            print(f"no existe: {f}", file=sys.stderr)
            return 1

    s = cat.read_text(encoding="utf-8")
    s = sustituir(s, CAT_VIEJO, CAT_NUEVO,
                  "operaciones identificadas por texto",
                  "def operacion_explicita", hechos, omitidos)
    cat.write_text(s, encoding="utf-8")

    s = per.read_text(encoding="utf-8")
    s = sustituir(s, PER_VIEJO, PER_NUEVO,
                  "consulta de entregas proximas",
                  "def tareas_proximas", hechos, omitidos)
    per.write_text(s, encoding="utf-8")

    s = age.read_text(encoding="utf-8")
    s = sustituir(s, AGE_RAPIDA_VIEJA, AGE_RAPIDA_NUEVA,
                  "ruta rapida de pendientes",
                  "accion_texto", hechos, omitidos)
    s = sustituir(s, AGE_AGENDA_VIEJA, AGE_AGENDA_NUEVA,
                  "agenda con entregas proximas",
                  "tareas_proximas(3)", hechos, omitidos)
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
