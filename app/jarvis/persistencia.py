"""Persistencia local del asistente de aula (Rol A).

Toda la informacion academica vive en la Raspberry Pi y no sale del aula: no
se trata de cifrar un transito hacia el exterior sino de eliminarlo. Este
modulo es el unico punto del sistema que escribe datos de estudiantes, y lo
hace sobre un archivo local.

SQLite por tres razones concretas:

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

CADENCIA DEL HISTORICO AMBIENTAL
--------------------------------
El sensor se muestrea cada 2,5 s. Guardar cada lectura son unas 34 000 filas
por dia sin ningun valor analitico, y desgaste innecesario de la tarjeta. Se
registra una fila cada cinco minutos, suficiente para responder a que hora
hizo mas calor.
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterator, List, Optional

RUTA_POR_DEFECTO = "/var/lib/jarvis/jarvis.db"
CADENCIA_AMBIENTE = 300.0  # segundos entre filas del historico

ESQUEMA = """
CREATE TABLE IF NOT EXISTS entregas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    estudiante  TEXT NOT NULL,
    trabajo     TEXT NOT NULL,
    momento     TEXT NOT NULL,
    registrado_por TEXT NOT NULL DEFAULT 'profesor'
);
CREATE INDEX IF NOT EXISTS idx_entregas_estudiante ON entregas(estudiante);

CREATE TABLE IF NOT EXISTS agenda (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    descripcion TEXT NOT NULL,
    momento     TEXT NOT NULL,
    creado      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agenda_momento ON agenda(momento);

CREATE TABLE IF NOT EXISTS ambiente (
    momento     TEXT PRIMARY KEY,
    temperatura REAL NOT NULL,
    humedad     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS zonas (
    zona        TEXT PRIMARY KEY,
    color       TEXT NOT NULL,
    brillo      REAL NOT NULL,
    momento     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bitacora (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    momento     TEXT NOT NULL,
    orden       TEXT NOT NULL,
    accion      TEXT NOT NULL,
    confianza   REAL NOT NULL,
    ejecutada   INTEGER NOT NULL,
    motivo      TEXT
);
"""


@dataclass
class Entrega:
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


class Almacen:
    """Acceso a los datos locales del aula.

    Una instancia por proceso. SQLite serializa las escrituras, de modo que
    el hilo de muestreo del sensor y el que atiende el portal pueden
    compartirla sin coordinacion adicional.
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
            # WAL sobrevive mejor a un corte abrupto que el modo por defecto.
            cx.execute("PRAGMA journal_mode=WAL")
            yield cx
            cx.commit()
        finally:
            cx.close()

    # -- entregas -----------------------------------------------------------

    def registrar_entrega(self, estudiante: str, trabajo: str,
                          registrado_por: str = "profesor") -> Entrega:
        e = Entrega(estudiante.strip(), trabajo.strip(), _ahora(), registrado_por)
        with self._conexion() as cx:
            cx.execute(
                "INSERT INTO entregas (estudiante, trabajo, momento, registrado_por)"
                " VALUES (?, ?, ?, ?)",
                (e.estudiante, e.trabajo, e.momento, e.registrado_por),
            )
        return e

    def entregas(self, estudiante: Optional[str] = None,
                 limite: int = 50) -> List[Entrega]:
        """Entregas registradas.

        Con estudiante devuelve solo las suyas: es como se implementa RF-04,
        que pide presentar los registros filtrados segun el actor que
        consulta. El llamador decide el filtro a partir del rol.
        """
        sql = "SELECT * FROM entregas"
        params: tuple = ()
        if estudiante:
            sql += " WHERE estudiante LIKE ?"
            params = (f"%{estudiante.strip()}%",)
        sql += " ORDER BY momento DESC LIMIT ?"
        params += (limite,)
        with self._conexion() as cx:
            filas = cx.execute(sql, params).fetchall()
        return [Entrega(f["estudiante"], f["trabajo"], f["momento"],
                        f["registrado_por"]) for f in filas]

    def contar_entregas(self) -> int:
        with self._conexion() as cx:
            return cx.execute("SELECT COUNT(*) FROM entregas").fetchone()[0]

    # -- agenda -------------------------------------------------------------

    def agregar_evento(self, descripcion: str, momento: Optional[str] = None) -> Evento:
        ev = Evento(descripcion.strip(), momento or _ahora())
        with self._conexion() as cx:
            cx.execute(
                "INSERT INTO agenda (descripcion, momento, creado) VALUES (?, ?, ?)",
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

    # -- ambiente -----------------------------------------------------------

    def registrar_ambiente(self, temperatura: float, humedad: float,
                           forzar: bool = False) -> bool:
        """Guarda una lectura respetando la cadencia. Devuelve si escribio."""
        ahora = time.monotonic()
        if not forzar and (ahora - self._ultimo_ambiente) < CADENCIA_AMBIENTE:
            return False
        self._ultimo_ambiente = ahora
        with self._conexion() as cx:
            cx.execute(
                "INSERT OR REPLACE INTO ambiente (momento, temperatura, humedad)"
                " VALUES (?, ?, ?)",
                (_ahora(), round(temperatura, 1), round(humedad, 1)),
            )
        return True

    def ambiente_reciente(self, horas: int = 8) -> List[LecturaAmbiente]:
        desde = (datetime.now() - timedelta(hours=horas)).isoformat(timespec="seconds")
        with self._conexion() as cx:
            filas = cx.execute(
                "SELECT * FROM ambiente WHERE momento >= ? ORDER BY momento",
                (desde,),
            ).fetchall()
        return [LecturaAmbiente(f["momento"], f["temperatura"], f["humedad"])
                for f in filas]

    def pico_de_calor(self, horas: int = 8) -> Optional[LecturaAmbiente]:
        lecturas = self.ambiente_reciente(horas)
        return max(lecturas, key=lambda l: l.temperatura) if lecturas else None

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
        return {f["zona"]: {"color": f["color"], "brillo": f["brillo"]} for f in filas}

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
                "INSERT INTO bitacora (momento, orden, accion, confianza, ejecutada, motivo)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (_ahora(), orden, accion, round(confianza, 3), int(ejecutada), motivo),
            )

    def decisiones(self, limite: int = 100) -> List[sqlite3.Row]:
        with self._conexion() as cx:
            return cx.execute(
                "SELECT * FROM bitacora ORDER BY momento DESC LIMIT ?", (limite,)
            ).fetchall()

    # -- apoyo al carril generativo -----------------------------------------

    def contexto_de_sesion(self) -> str:
        """Resumen compacto del estado, para alimentar al modelo generativo.

        Deliberadamente breve: a 18,5 tokens por segundo cada linea de
        contexto se paga en latencia. El modelo no consulta la base de datos
        ni documentos; recibe solo este resumen ya compuesto.
        """
        partes = [f"Entregas registradas hoy: {self.contar_entregas()}."]
        eventos = self.agenda_de_hoy()
        if eventos:
            partes.append("Agenda: " + "; ".join(e.descripcion for e in eventos[:3]) + ".")
        pico = self.pico_de_calor()
        if pico:
            partes.append(f"Temperatura maxima: {pico.temperatura} C.")
        return " ".join(partes)


if __name__ == "__main__":
    import sys

    ruta = sys.argv[1] if len(sys.argv) > 1 else "/tmp/jarvis-prueba.db"
    a = Almacen(ruta)
    print("almacen en", ruta)

    a.registrar_entrega("Maria", "laboratorio 3")
    a.registrar_entrega("Jose", "laboratorio 3")
    a.agregar_evento("Repaso de Yocto")
    a.registrar_ambiente(24.5, 61.0, forzar=True)
    a.guardar_zona("FRENTE", "blanco", 1.0)
    a.anotar_decision("prende la luz", "LUZ_ON", 0.96, True)
    a.anotar_decision("asdfgh", "NADA", 0.31, False, "confianza bajo umbral")

    print("entregas:", [(e.estudiante, e.trabajo) for e in a.entregas()])
    print("de Maria:", [(e.estudiante, e.trabajo) for e in a.entregas("Maria")])
    print("agenda:", [e.descripcion for e in a.agenda_de_hoy()])
    print("pico:", a.pico_de_calor())
    print("zonas:", a.estado_zonas())
    print("decisiones:", len(a.decisiones()))
    print("contexto:", a.contexto_de_sesion())
