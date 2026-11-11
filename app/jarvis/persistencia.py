"""Persistencia local del asistente de aula.

Toda la informacion academica vive en la Raspberry Pi y no sale del aula: no
se trata de cifrar un transito hacia el exterior sino de eliminarlo.

ESQUEMA UNIFICADO
-----------------
El portal y el agente comparten un unico archivo SQLite. El modelo de tareas
y entregas con archivos adjuntos es el del portal, que es el correcto: una
entrega pertenece a una tarea, la hace un estudiante identificado por carne,
y puede traer un archivo. Este modulo lo adopta tal cual y le suma las tres
tablas que el agente necesita y el portal no tenia:

    tareas, entregas, ambiente     del portal
    agenda, zonas, bitacora        del agente

La alternativa —dos archivos, uno por componente— se descarto porque la
pregunta "quien ya entrego" debe responder sobre las entregas reales, no
sobre un registro paralelo. Dos tablas con el mismo nombre y distinto
esquema en el mismo archivo es un fallo silencioso: la primera que se crea
gana y las consultas de la otra fallan en ejecucion.

SQLite por tres razones:

  - Es biblioteca estandar de Python. No agrega una sola dependencia a la
    imagen, y los modelos de inferencia ya pesan el 97 % de los 2,3 GB.
  - Las escrituras son atomicas. La demostracion de R2 apaga el equipo
    desconectandolo; con un archivo JSON reescrito completo en cada cambio,
    un corte a mitad de escritura pierde el archivo entero, no una fila.
  - Permite filtrar sin cargar todo en memoria, que es lo que pide RF-04:
    el estudiante ve sus propias entregas, el profesor ve todas.

El archivo vive en /var/lib/jarvis/, no en memoria: RF-03 pide durabilidad
entre reinicios. Esto implica escrituras en la MicroSD, lo cual es correcto:
la regla R1 prohibe que los MODELOS se lean del medio de arranque tras el
inicio, no que el sistema persista datos.
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterator, List, Optional

RUTA_POR_DEFECTO = os.environ.get("JARVIS_DB", "/var/lib/jarvis/jarvis.db")

CADENCIA_AMBIENTE = 300.0
"""Segundos minimos entre filas ambientales escritas por el agente.

El portal muestrea cada pocos segundos por su cuenta y poda el historico. El
agente solo escribe cuando alguien pregunta la temperatura, y esta cadencia
evita que una rafaga de consultas llene la tabla.
"""

ESQUEMA = """
CREATE TABLE IF NOT EXISTS tareas(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo      TEXT NOT NULL,
    descripcion TEXT NOT NULL DEFAULT '',
    fecha_limite TEXT,
    creada      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS entregas(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tarea_id    INTEGER NOT NULL,
    carnet      TEXT NOT NULL,
    nombre      TEXT NOT NULL,
    comentario  TEXT NOT NULL DEFAULT '',
    archivo_nombre TEXT,
    archivo_id  TEXT,
    enviada     REAL NOT NULL,
    UNIQUE(tarea_id, carnet)
);
CREATE INDEX IF NOT EXISTS idx_entregas_nombre ON entregas(nombre);

CREATE TABLE IF NOT EXISTS ambiente(
    ts   REAL NOT NULL,
    temp REAL NOT NULL,
    hum  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS agenda(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    descripcion TEXT NOT NULL,
    momento     TEXT NOT NULL,
    creado      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agenda_momento ON agenda(momento);

CREATE TABLE IF NOT EXISTS zonas(
    zona    TEXT PRIMARY KEY,
    color   TEXT NOT NULL,
    brillo  REAL NOT NULL,
    momento TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bitacora(
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    momento   TEXT NOT NULL,
    orden     TEXT NOT NULL,
    accion    TEXT NOT NULL,
    confianza REAL NOT NULL,
    ejecutada INTEGER NOT NULL,
    motivo    TEXT
);
"""


@dataclass
class Entrega:
    """Una entrega, en los terminos que usa el agente.

    'trabajo' es el titulo de la tarea a la que pertenece: el agente razona
    sobre "la entrega de Maria del laboratorio 3", no sobre identificadores.
    """

    estudiante: str
    trabajo: str
    momento: str
    registrado_por: str = "profesor"


@dataclass
class Evento:
    descripcion: str
    momento: str


@dataclass
class LecturaAmbiente:
    momento: str
    temperatura: float
    humedad: float


def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _desde_epoch(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


class SinTareas(RuntimeError):
    """No hay ninguna tarea a la cual asociar una entrega."""


class Almacen:
    """Acceso del agente a los datos locales del aula.

    Una instancia por proceso. SQLite serializa las escrituras, de modo que
    el agente y el muestreador del portal pueden compartir el archivo sin
    coordinacion adicional.
    """

    def __init__(self, ruta: str = RUTA_POR_DEFECTO):
        self.ruta = ruta
        directorio = os.path.dirname(ruta)
        if directorio:
            os.makedirs(directorio, exist_ok=True)
        self._ultimo_ambiente = 0.0
        with self._conexion() as cx:
            cx.executescript(ESQUEMA)

    @contextmanager
    def _conexion(self) -> Iterator[sqlite3.Connection]:
        cx = sqlite3.connect(self.ruta, timeout=10.0)
        cx.row_factory = sqlite3.Row
        try:
            # WAL sobrevive mejor a un corte abrupto que el modo por defecto,
            # y permite que el portal lea mientras el agente escribe.
            cx.execute("PRAGMA journal_mode=WAL")
            yield cx
            cx.commit()
        finally:
            cx.close()

    # -- entregas -----------------------------------------------------------

    def tarea_actual(self) -> Optional[sqlite3.Row]:
        """La tarea mas reciente. Es a la que el agente asocia una entrega."""
        with self._conexion() as cx:
            return cx.execute(
                "SELECT id, titulo FROM tareas ORDER BY id DESC LIMIT 1"
            ).fetchone()

    def registrar_entrega(self, estudiante: str, trabajo: str,
                          registrado_por: str = "profesor") -> Entrega:
        """Registra una entrega dictada al agente, sin archivo adjunto.

        El agente no recibe archivos: eso pasa por el formulario del portal.
        Lo que registra es el hecho de la entrega, asociado a la tarea mas
        reciente, que es el caso de uso del aula —el profesor dicta mientras
        los estudiantes van pasando—.
        """
        tarea = self.tarea_actual()
        if tarea is None:
            raise SinTareas(
                "todavia no hay ninguna tarea creada; el profesor debe crearla "
                "desde la pestaña de entregas antes de registrar"
            )
        estudiante = estudiante.strip()
        # El carne es obligatorio y unico por tarea. Una entrega dictada no
        # lo trae, de modo que se deriva del nombre y se marca su origen.
        carnet = "DICTADA-" + "".join(
            c for c in estudiante.upper() if c.isalnum())[:12]
        momento = time.time()
        with self._conexion() as cx:
            cx.execute(
                "INSERT INTO entregas"
                " (tarea_id, carnet, nombre, comentario, archivo_nombre,"
                "  archivo_id, enviada)"
                " VALUES (?, ?, ?, ?, NULL, NULL, ?)"
                " ON CONFLICT(tarea_id, carnet) DO UPDATE SET"
                "  nombre=excluded.nombre, comentario=excluded.comentario,"
                "  enviada=excluded.enviada",
                (tarea["id"], carnet, estudiante,
                 f"registrada por {registrado_por}", momento),
            )
        return Entrega(estudiante, tarea["titulo"], _desde_epoch(momento),
                       registrado_por)

    def entregas(self, estudiante: Optional[str] = None,
                 limite: int = 50) -> List[Entrega]:
        """Entregas registradas, con el titulo de su tarea.

        Con estudiante devuelve solo las suyas: es como se implementa RF-04,
        que pide presentar los registros filtrados segun el actor que
        consulta. El llamador decide el filtro a partir del rol.
        """
        sql = ("SELECT e.nombre, e.enviada, e.comentario,"
               "       COALESCE(t.titulo, 'sin tarea') AS titulo"
               "  FROM entregas e LEFT JOIN tareas t ON t.id = e.tarea_id")
        params: tuple = ()
        if estudiante:
            sql += " WHERE e.nombre LIKE ?"
            params = (f"%{estudiante.strip()}%",)
        sql += " ORDER BY e.enviada DESC LIMIT ?"
        params += (limite,)
        with self._conexion() as cx:
            filas = cx.execute(sql, params).fetchall()
        return [Entrega(f["nombre"], f["titulo"], _desde_epoch(f["enviada"]),
                        f["comentario"] or "portal") for f in filas]

    def contar_entregas(self) -> int:
        with self._conexion() as cx:
            return cx.execute("SELECT COUNT(*) FROM entregas").fetchone()[0]

    def tareas_pendientes(self, carnet: str) -> List[str]:
        """Titulos de tareas que ese carne todavia no entrego."""
        with self._conexion() as cx:
            filas = cx.execute(
                "SELECT titulo FROM tareas WHERE id NOT IN"
                " (SELECT tarea_id FROM entregas WHERE carnet = ?)"
                " ORDER BY id DESC", (carnet,)
            ).fetchall()
        return [f["titulo"] for f in filas]

    # -- agenda -------------------------------------------------------------

    def agregar_evento(self, descripcion: str,
                       momento: Optional[str] = None) -> Evento:
        ev = Evento(descripcion.strip(), momento or _ahora())
        with self._conexion() as cx:
            cx.execute(
                "INSERT INTO agenda (descripcion, momento, creado)"
                " VALUES (?, ?, ?)",
                (ev.descripcion, ev.momento, _ahora()),
            )
        return ev

    def agenda_de_hoy(self) -> List[Evento]:
        hoy = datetime.now().date().isoformat()
        with self._conexion() as cx:
            filas = cx.execute(
                "SELECT descripcion, momento FROM agenda"
                " WHERE momento LIKE ? ORDER BY momento",
                (f"{hoy}%",),
            ).fetchall()
        return [Evento(f["descripcion"], f["momento"]) for f in filas]

    def agenda_completa(self, limite: int = 100) -> List[dict]:
        """Para la vista del portal: incluye el identificador, para borrar."""
        with self._conexion() as cx:
            filas = cx.execute(
                "SELECT id, descripcion, momento FROM agenda"
                " ORDER BY momento DESC LIMIT ?", (limite,)
            ).fetchall()
        return [dict(f) for f in filas]

    def borrar_evento(self, id_evento: int) -> None:
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

    # -- ambiente -----------------------------------------------------------

    def registrar_ambiente(self, temperatura: float, humedad: float,
                           forzar: bool = False) -> bool:
        """Guarda una lectura respetando la cadencia. Devuelve si escribio."""
        ahora = time.monotonic()
        if not forzar and (ahora - self._ultimo_ambiente) < CADENCIA_AMBIENTE:
            return False
        self._ultimo_ambiente = ahora
        with self._conexion() as cx:
            cx.execute("INSERT INTO ambiente (ts, temp, hum) VALUES (?, ?, ?)",
                       (time.time(), round(temperatura, 1), round(humedad, 1)))
        return True

    def ambiente_reciente(self, horas: int = 8) -> List[LecturaAmbiente]:
        desde = time.time() - horas * 3600
        with self._conexion() as cx:
            filas = cx.execute(
                "SELECT ts, temp, hum FROM ambiente WHERE ts >= ? ORDER BY ts",
                (desde,),
            ).fetchall()
        return [LecturaAmbiente(_desde_epoch(f["ts"]), f["temp"], f["hum"])
                for f in filas]

    def pico_de_calor(self, horas: int = 8) -> Optional[LecturaAmbiente]:
        with self._conexion() as cx:
            f = cx.execute(
                "SELECT ts, temp, hum FROM ambiente WHERE ts >= ?"
                " ORDER BY temp DESC LIMIT 1",
                (time.time() - horas * 3600,),
            ).fetchone()
        return None if f is None else LecturaAmbiente(
            _desde_epoch(f["ts"]), f["temp"], f["hum"])

    # -- estado de las zonas ------------------------------------------------

    def guardar_zona(self, zona: str, color: str, brillo: float) -> None:
        """Estado de iluminacion, para reconstruirlo tras un reinicio."""
        with self._conexion() as cx:
            cx.execute(
                "INSERT OR REPLACE INTO zonas (zona, color, brillo, momento)"
                " VALUES (?, ?, ?, ?)",
                (zona, color, round(brillo, 3), _ahora()),
            )

    def estado_zonas(self) -> dict:
        with self._conexion() as cx:
            filas = cx.execute("SELECT * FROM zonas").fetchall()
        return {f["zona"]: {"color": f["color"], "brillo": f["brillo"]}
                for f in filas}

    # -- bitacora de decisiones ---------------------------------------------

    def anotar_decision(self, orden: str, accion: str, confianza: float,
                        ejecutada: bool, motivo: Optional[str] = None) -> None:
        """Registra cada clasificacion, se haya ejecutado o no.

        Es el insumo para recalibrar el umbral con datos de uso real en lugar
        de con el banco de frases, y deja constancia de las abstenciones, que
        son un resultado legitimo del sistema y no un error.
        """
        with self._conexion() as cx:
            cx.execute(
                "INSERT INTO bitacora"
                " (momento, orden, accion, confianza, ejecutada, motivo)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (_ahora(), orden, accion, round(confianza, 3),
                 int(ejecutada), motivo),
            )

    def decisiones(self, limite: int = 100) -> List[sqlite3.Row]:
        with self._conexion() as cx:
            return cx.execute(
                "SELECT * FROM bitacora ORDER BY id DESC LIMIT ?", (limite,)
            ).fetchall()

    # -- apoyo al carril generativo -----------------------------------------

    def contexto_de_sesion(self) -> str:
        """Resumen compacto del estado, para alimentar al modelo generativo.

        Deliberadamente breve: a 18,5 tokens por segundo cada linea de
        contexto se paga en latencia. El modelo no consulta la base de datos
        ni documentos; recibe solo este resumen ya compuesto.
        """
        partes = [f"Entregas registradas: {self.contar_entregas()}."]
        eventos = self.agenda_de_hoy()
        if eventos:
            partes.append("Agenda: "
                          + "; ".join(e.descripcion for e in eventos[:3]) + ".")
        pico = self.pico_de_calor()
        if pico:
            partes.append(f"Temperatura maxima: {pico.temperatura} C.")
        return " ".join(partes)


if __name__ == "__main__":
    import sys

    ruta = sys.argv[1] if len(sys.argv) > 1 else "/tmp/jarvis-prueba.db"
    a = Almacen(ruta)
    print("almacen en", ruta)

    # El agente no crea tareas: las crea el profesor desde el portal.
    with a._conexion() as cx:
        cx.execute("INSERT INTO tareas (titulo, descripcion, creada)"
                   " VALUES (?, ?, ?)",
                   ("Laboratorio 3", "Yocto y BSP", time.time()))

    a.registrar_entrega("Maria", "")
    a.registrar_entrega("Jose", "")
    a.agregar_evento("Repaso de Yocto")
    a.registrar_ambiente(24.5, 61.0, forzar=True)
    a.guardar_zona("FRENTE", "blanco", 1.0)
    a.anotar_decision("prende la luz", "LUZ_ON", 0.96, True)
    a.anotar_decision("asdfgh", "NADA", 0.31, False, "confianza bajo umbral")

    print("entregas:", [(e.estudiante, e.trabajo) for e in a.entregas()])
    print("de Maria:", [(e.estudiante, e.trabajo) for e in a.entregas("Maria")])
    print("pendientes de DICTADA-MARIA:", a.tareas_pendientes("DICTADA-MARIA"))
    print("agenda:", [e.descripcion for e in a.agenda_de_hoy()])
    print("pico:", a.pico_de_calor())
    print("zonas:", a.estado_zonas())
    print("decisiones:", len(a.decisiones()))
    print("contexto:", a.contexto_de_sesion())

    try:
        Almacen("/tmp/jarvis-vacia.db").registrar_entrega("Ana", "")
    except SinTareas as e:
        print("sin tareas, como debe ser:", e)
