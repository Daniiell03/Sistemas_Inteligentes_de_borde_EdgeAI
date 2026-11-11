"""Portal Jarvis Aula (EdgeAI).

Interfaz web unica para estudiantes y profesor:
  - Login por rol (el profesor entra con PIN; el rol vive en la sesion del
    servidor, el navegador nunca lo decide).
  - Entregas: el profesor crea tareas, los estudiantes suben archivos.
    Todo se guarda localmente (SQLite + carpeta uploads).
  - Ambiente: temperatura y humedad con historial.
  - Luces: zonas RGB con color e intensidad (solo el profesor las cambia).
  - Agenda: eventos de la sesion, que el asistente tambien consulta.
  - Asistente: el agente coordinador real sobre los dos modelos locales.

El diseno, la autenticacion, el modelo de tareas y la interfaz son del Rol A.
Lo que cambia respecto de su version de prueba en Windows:

  1. Vive dentro del paquete jarvis, con importaciones relativas, para que la
     receta lo empaquete junto con la capa de abstraccion de hardware.
  2. Usa la HAL del repositorio: zonas FRENTE y FONDO con la semantica del
     aula, escenas de clase, y la bandera de salida del ABI de GPIO
     corregida. La copia que viajaba en el paquete de prueba era anterior a
     ambos cambios.
  3. Datos en /var/lib/jarvis y configuracion en /etc/jarvis, que es donde la
     unidad de servicio los crea con permisos.
  4. El hardware se detecta solo; la configuracion puede forzarlo.
  5. SimAgent, que resolvia por palabras clave, se reemplaza por el agente
     coordinador que consulta tev1:0.8b para decidir y llama3.2:1b para
     redactar.

LATENCIA Y CONCURRENCIA
-----------------------
Una orden ocupa el modelo de decision quince segundos. El agente es sincrono
y bloqueante, de modo que se ejecuta en un hilo aparte: en el bucle de
eventos dejaria al servidor sin atender ninguna otra peticion durante ese
tiempo, y la pagina entera se congelaria para todos.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import unicodedata
import uuid
from contextlib import asynccontextmanager, closing
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from . import inferencia
from .agente import Agente, Parada, Respuesta
from .hal_rooms import PALETTE, SCENES, Color, Room
from .interfaces import ICoordinatorAgent
from .persistencia import Almacen, SinTareas

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("JARVIS_DATA", "/var/lib/jarvis"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = Path(os.environ.get("JARVIS_DB", str(DATA_DIR / "jarvis.db")))
CONFIG_PATH = Path(os.environ.get("JARVIS_CONFIG", "/etc/jarvis/portal_config.json"))

# --------------------------------------------------------------------------
# Configuracion (se crea sola la primera vez; editar y reiniciar)
# --------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "pin_profesor": "profesor2026",
    # "auto" detecta el hardware; true fuerza la simulada; false, la real.
    "simular_hardware": "auto",
    "umbral_temp_c": 28.0,
    "max_subida_mb": 10,
    "historico_ambiente_max": 2000,
    "periodo_muestreo_s": 5,
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if CONFIG_PATH.exists():
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        if "secreto_sesion" not in cfg:
            cfg["secreto_sesion"] = secrets.token_hex(32)
            CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except OSError:
        # Sistema de archivos de solo lectura o sin permisos: el portal debe
        # arrancar igual. El secreto queda en memoria y las sesiones no
        # sobreviven a un reinicio, que es degradacion aceptable.
        cfg.setdefault("secreto_sesion", secrets.token_hex(32))
    return cfg


CFG = load_config()
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

EXT_PERMITIDAS = {
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".txt", ".md",
    ".csv", ".json", ".zip", ".png", ".jpg", ".jpeg", ".py", ".c", ".cpp", ".h",
    ".ino", ".ipynb", ".tex", ".yaml", ".yml",
}

# --------------------------------------------------------------------------
# Hardware (HAL)
# --------------------------------------------------------------------------
RUTA_PWM = "/sys/class/pwm"
RUTA_GPIO = "/dev/gpiochip0"


def hay_hardware() -> bool:
    """Si el nucleo expone lo que la implementacion real necesita."""
    return (os.path.isdir(RUTA_PWM)
            and bool(os.listdir(RUTA_PWM))
            and os.path.exists(RUTA_GPIO))


def build_hardware():
    modo = CFG.get("simular_hardware", "auto")
    if isinstance(modo, str):
        modo = modo.strip().lower()
    simular = (not hay_hardware()) if modo in ("auto", "") else bool(modo)

    if simular:
        from .hal_fake import FakeRoomLight, FakeTempHumSensor
        razon = "por configuracion" if modo not in ("auto", "") else (
            f"el nucleo no expone PWM o GPIO (revisar {RUTA_PWM} y los overlays)")
        print(f"HAL simulada: {razon}")
        return FakeRoomLight(verbose=False), FakeTempHumSensor()

    from .hal_rpi import Dht22IioSensor
    from .hal_rpi_rooms import PwmRoomLight
    print("HAL real: PWM, GPIO y sensor del nucleo")
    return PwmRoomLight(), Dht22IioSensor()


LUCES, SENSOR = build_hardware()

_modo_cfg = CFG.get("simular_hardware", "auto")
SIMULADO = (not hay_hardware()) if (isinstance(_modo_cfg, str)
                                    and _modo_cfg.strip().lower() in ("auto", "")) \
    else bool(_modo_cfg)

# El almacen crea el esquema completo —tareas y entregas del portal, mas
# agenda, zonas y bitacora del agente— sobre un unico archivo.
ALMACEN = Almacen(str(DB_PATH))


# --------------------------------------------------------------------------
# Base de datos
# --------------------------------------------------------------------------
def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return closing(con)


# Marca de tiempo de la ultima lectura escrita en la tabla. El muestreador
# corre cada cinco segundos y el sensor publica cada dos y medio, de modo que
# sin esta comparacion una lectura que deja de refrescarse se escribe
# indefinidamente: el 10 de noviembre la tabla acumulo 867 filas identicas de
# un dato congelado. Se compara por marca de tiempo y no por valores, porque
# dos lecturas iguales separadas en el tiempo si son informacion, mientras
# que la misma lectura repetida no lo es.
_ULTIMA_GUARDADA = None


def guardar_muestra():
    global _ULTIMA_GUARDADA
    r = SENSOR.last_reading()
    if r is None:
        # Sensor sin datos o vencido. No se inventa una fila: el hueco en la
        # serie es la representacion honesta de un sensor que no responde.
        return
    if _ULTIMA_GUARDADA is not None and r.timestamp <= _ULTIMA_GUARDADA:
        return
    _ULTIMA_GUARDADA = r.timestamp
    with db() as con:
        con.execute("INSERT INTO ambiente (ts, temp, hum) VALUES (?,?,?)",
                    (time.time(), r.temperature_c, r.humidity_pct))
        con.execute(
            "DELETE FROM ambiente WHERE rowid <= "
            "(SELECT MAX(rowid) FROM ambiente) - ?", (CFG["historico_ambiente_max"],))
        con.commit()


async def muestreador():
    while True:
        try:
            guardar_muestra()
        except Exception as e:  # el sensor nunca debe tumbar el portal
            print("muestreo:", e)
        await asyncio.sleep(CFG["periodo_muestreo_s"])


def restaurar_iluminacion() -> None:
    """Reaplica el estado guardado de las zonas.

    Tras un reinicio los canales arrancan apagados mientras el almacen
    conserva el ultimo estado conocido. Sin esto el sistema afirma una cosa y
    el aula muestra otra, y la discrepancia aparece justo al reconectar el
    equipo, que es lo que ocurre en la demostracion de la regla R2.
    """
    guardado = ALMACEN.estado_zonas()
    if not guardado:
        return
    disponibles = {r.name: r for r in LUCES.available()}
    restauradas = []
    for nombre, estado in guardado.items():
        zona = disponibles.get(nombre)
        color = PALETTE.get(estado["color"])
        if zona is None or color is None:
            continue
        try:
            LUCES.set(zona, color, float(estado["brillo"]))
        except Exception as e:
            print(f"no se pudo restaurar {nombre}: {e}")
            continue
        if float(estado["brillo"]) > 0:
            restauradas.append(f"{zona.value} {estado['color']}")
    if restauradas:
        print("iluminacion restaurada:", ", ".join(restauradas))


@asynccontextmanager
async def lifespan(app: FastAPI):
    restaurar_iluminacion()
    tarea = asyncio.create_task(muestreador())
    print(f"\n  PIN del profesor: {CFG['pin_profesor']}   "
          f"(se cambia en {CONFIG_PATH})")
    if inferencia.disponible():
        residentes = inferencia.modelos_residentes()
        print(f"  modelos residentes: {', '.join(residentes) or 'ninguno'}")
        if len(residentes) < 2:
            print("  aviso: se esperaban dos modelos residentes; una orden "
                  "podria pagar la carga desde la MicroSD (R1)")
    else:
        print("  aviso: el servidor de inferencia no responde todavia")
    print()
    yield
    tarea.cancel()
    for recurso in (SENSOR, LUCES):
        cerrar = getattr(recurso, "close", None)
        if cerrar is not None:
            try:
                cerrar()
            except Exception:
                pass


app = FastAPI(title="Jarvis Aula Edge Portal", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=CFG["secreto_sesion"],
                   max_age=8 * 3600, same_site="lax", session_cookie="jarvis_sesion")

# --------------------------------------------------------------------------
# Autenticacion
# --------------------------------------------------------------------------
class LoginPayload(BaseModel):
    role: str
    nombre: str = Field("", max_length=60)
    carnet: str = Field("", max_length=20)
    pin: str = Field("", max_length=64)


_intentos: dict = {}          # ip -> (fallos, bloqueado_hasta)


def _bloqueado(ip: str) -> bool:
    fallos, hasta = _intentos.get(ip, (0, 0))
    return fallos >= 5 and time.time() < hasta


@app.post("/api/login")
def login(p: LoginPayload, request: Request):
    ip = request.client.host if request.client else "?"
    if p.role == "profesor":
        if _bloqueado(ip):
            raise HTTPException(429, "Demasiados intentos. Espere un minuto.")
        if not hmac.compare_digest(p.pin.encode(), str(CFG["pin_profesor"]).encode()):
            f, _ = _intentos.get(ip, (0, 0))
            _intentos[ip] = (f + 1, time.time() + 60)
            raise HTTPException(401, "PIN incorrecto.")
        _intentos.pop(ip, None)
        request.session.clear()
        request.session.update({"role": "profesor", "nombre": "Profesor", "carnet": ""})
    elif p.role == "estudiante":
        nombre, carnet = p.nombre.strip(), p.carnet.strip().upper()
        if len(nombre) < 2:
            raise HTTPException(400, "Escriba su nombre.")
        if not re.fullmatch(r"[A-Z0-9-]{4,20}", carnet):
            raise HTTPException(400, "Carné inválido (4 a 20 letras o números).")
        request.session.clear()
        request.session.update({"role": "estudiante", "nombre": nombre, "carnet": carnet})
    else:
        raise HTTPException(400, "Rol inválido.")
    return me(request)


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    s = request.session
    if not s.get("role"):
        return {"auth": False}
    return {"auth": True, "role": s["role"], "nombre": s["nombre"],
            "carnet": s.get("carnet", ""), "simulado": SIMULADO}


def usuario(request: Request) -> dict:
    if not request.session.get("role"):
        raise HTTPException(401, "Inicie sesión.")
    return dict(request.session)


def profesor(u: dict = Depends(usuario)) -> dict:
    if u["role"] != "profesor":
        raise HTTPException(403, "Solo el profesor puede hacer esto.")
    return u


def estudiante(u: dict = Depends(usuario)) -> dict:
    if u["role"] != "estudiante":
        raise HTTPException(403, "Solo los estudiantes entregan tareas.")
    return u

# --------------------------------------------------------------------------
# Ambiente (temperatura y humedad)
# --------------------------------------------------------------------------
@app.get("/api/ambiente")
def ambiente(u: dict = Depends(usuario)):
    r = SENSOR.last_reading()
    with db() as con:
        filas = con.execute(
            "SELECT ts, temp, hum FROM ambiente ORDER BY rowid DESC LIMIT 60"
        ).fetchall()
    # El diagnostico se expone junto al valor para que un "actual": null no
    # sea indistinguible de un sensor que no existe. La HAL falsa no lo
    # implementa, de ahi el getattr.
    diag = getattr(SENSOR, "diagnostico", None)
    return {
        "actual": None if r is None else {"temp": r.temperature_c, "hum": r.humidity_pct},
        "historial": [dict(f) for f in reversed(filas)],
        "umbral": CFG["umbral_temp_c"],
        "sensor": diag() if callable(diag) else {"estado": "simulado"},
    }


class SimPayload(BaseModel):
    temp: float = Field(ge=-10, le=60)
    hum: float = Field(ge=0, le=100)


@app.post("/api/ambiente/simular")
def simular(p: SimPayload, u: dict = Depends(profesor)):
    if not SIMULADO:
        raise HTTPException(400, "Solo disponible con hardware simulado.")
    SENSOR.set_base(p.temp, p.hum)
    guardar_muestra()
    return {"ok": True}

# --------------------------------------------------------------------------
# Luces (zonas RGB)
# --------------------------------------------------------------------------
def _hex(c: Color) -> str:
    return "#%02x%02x%02x" % (round(c.r * 255), round(c.g * 255), round(c.b * 255))


def _nombre_color(c: Color) -> str:
    for n, p in PALETTE.items():
        if abs(p.r - c.r) < .01 and abs(p.g - c.g) < .01 and abs(p.b - c.b) < .01:
            return n
    return "personalizado"


def _guardar_estado_luces() -> None:
    """Persiste el estado para poder restaurarlo tras un reinicio."""
    for room, st in LUCES.snapshot().items():
        ALMACEN.guardar_zona(room.name, _nombre_color(st.color), st.brightness)


@app.get("/api/luces")
def luces(u: dict = Depends(usuario)):
    habs = []
    for room in sorted(LUCES.available(), key=lambda r: r.value):
        st = LUCES.get(room)
        habs.append({"room": room.value, "encendida": st.is_on,
                     "color": _nombre_color(st.color), "hex": _hex(st.color),
                     "brillo": round(st.brightness, 2)})
    return {
        "habitaciones": habs,
        "paleta": [{"nombre": n, "hex": _hex(c)} for n, c in PALETTE.items() if n != "apagado"],
        "escenas": list(SCENES.keys()),
    }


class LuzPayload(BaseModel):
    room: str
    color: str
    brillo: float = Field(ge=0, le=1)


@app.post("/api/luces")
def set_luz(p: LuzPayload, u: dict = Depends(profesor)):
    try:
        room = Room(p.room)
        if room not in LUCES.available():
            raise ValueError
    except ValueError:
        raise HTTPException(400, "Zona no disponible.")
    if p.color not in PALETTE:
        raise HTTPException(400, "Color desconocido.")
    if p.brillo == 0 or p.color == "apagado":
        LUCES.turn_off(room)
    else:
        LUCES.set(room, PALETTE[p.color], p.brillo)
    _guardar_estado_luces()
    return {"ok": True}


class EscenaPayload(BaseModel):
    escena: str


@app.post("/api/luces/escena")
def escena(p: EscenaPayload, u: dict = Depends(profesor)):
    if p.escena not in SCENES:
        raise HTTPException(400, "Escena desconocida.")
    LUCES.apply_scene(p.escena)
    _guardar_estado_luces()
    return {"ok": True}


@app.post("/api/luces/apagar")
def apagar_todo(u: dict = Depends(profesor)):
    LUCES.all_off()
    _guardar_estado_luces()
    return {"ok": True}

# --------------------------------------------------------------------------
# Tareas y entregas
# --------------------------------------------------------------------------
class TareaPayload(BaseModel):
    titulo: str = Field(min_length=3, max_length=120)
    descripcion: str = Field("", max_length=1000)
    fecha_limite: str = Field("", max_length=16)     # YYYY-MM-DDTHH:MM


def _limite_ts(fecha: Optional[str]) -> Optional[float]:
    try:
        return datetime.fromisoformat(fecha).timestamp() if fecha else None
    except ValueError:
        return None


@app.get("/api/tareas")
def tareas(u: dict = Depends(usuario)):
    with db() as con:
        out = []
        for t in con.execute("SELECT * FROM tareas ORDER BY id DESC"):
            lim = _limite_ts(t["fecha_limite"])
            item = {"id": t["id"], "titulo": t["titulo"], "descripcion": t["descripcion"],
                    "fecha_limite": t["fecha_limite"], "vencida": bool(lim and time.time() > lim)}
            if u["role"] == "profesor":
                item["entregas"] = con.execute(
                    "SELECT COUNT(*) FROM entregas WHERE tarea_id=?", (t["id"],)).fetchone()[0]
            else:
                e = con.execute("SELECT * FROM entregas WHERE tarea_id=? AND carnet=?",
                                (t["id"], u["carnet"])).fetchone()
                item["mi_entrega"] = None if e is None else {
                    "id": e["id"], "archivo": e["archivo_nombre"], "comentario": e["comentario"],
                    "enviada": e["enviada"], "tarde": bool(lim and e["enviada"] > lim)}
            out.append(item)
    return out


@app.post("/api/tareas")
def crear_tarea(p: TareaPayload, u: dict = Depends(profesor)):
    if p.fecha_limite and _limite_ts(p.fecha_limite) is None:
        raise HTTPException(400, "Fecha límite inválida.")
    with db() as con:
        cur = con.execute("INSERT INTO tareas(titulo,descripcion,fecha_limite,creada) VALUES (?,?,?,?)",
                          (p.titulo.strip(), p.descripcion.strip(), p.fecha_limite or None, time.time()))
        con.commit()
    return {"id": cur.lastrowid}


def _borrar_archivos(ids: List[str]):
    for a in ids:
        if a:
            (UPLOAD_DIR / a).unlink(missing_ok=True)


@app.delete("/api/tareas/{tid}")
def borrar_tarea(tid: int, u: dict = Depends(profesor)):
    with db() as con:
        archivos = [r[0] for r in con.execute(
            "SELECT archivo_id FROM entregas WHERE tarea_id=?", (tid,))]
        con.execute("DELETE FROM entregas WHERE tarea_id=?", (tid,))
        con.execute("DELETE FROM tareas WHERE id=?", (tid,))
        con.commit()
    _borrar_archivos(archivos)
    return {"ok": True}


@app.get("/api/tareas/{tid}/entregas")
def entregas_de_tarea(tid: int, u: dict = Depends(profesor)):
    with db() as con:
        t = con.execute("SELECT fecha_limite FROM tareas WHERE id=?", (tid,)).fetchone()
        if t is None:
            raise HTTPException(404, "Tarea no existe.")
        lim = _limite_ts(t["fecha_limite"])
        filas = con.execute("SELECT * FROM entregas WHERE tarea_id=? ORDER BY enviada", (tid,))
        return [{"id": e["id"], "nombre": e["nombre"], "carnet": e["carnet"],
                 "comentario": e["comentario"], "archivo": e["archivo_nombre"],
                 "enviada": e["enviada"], "tarde": bool(lim and e["enviada"] > lim)}
                for e in filas]


@app.put("/api/tareas/{tid}/entrega")
async def entregar(tid: int, comentario: str = Form("", max_length=500),
                   archivo: Optional[UploadFile] = File(None),
                   u: dict = Depends(estudiante)):
    nombre_orig, archivo_id = None, None
    if archivo is not None and archivo.filename:
        nombre_orig = Path(archivo.filename.replace("\\", "/")).name[:120]
        ext = Path(nombre_orig).suffix.lower()
        if ext not in EXT_PERMITIDAS:
            raise HTTPException(400, f"Tipo de archivo no permitido ({ext or 'sin extensión'}).")
        limite = int(CFG["max_subida_mb"]) * 1024 * 1024
        archivo_id = uuid.uuid4().hex + ext
        destino, total = UPLOAD_DIR / archivo_id, 0
        with open(destino, "wb") as f:
            while chunk := await archivo.read(1024 * 256):
                total += len(chunk)
                if total > limite:
                    f.close()
                    destino.unlink(missing_ok=True)
                    raise HTTPException(413, f"El archivo supera {CFG['max_subida_mb']} MB.")
                f.write(chunk)
    elif not comentario.strip():
        raise HTTPException(400, "Adjunte un archivo o escriba un comentario.")

    viejo = None
    with db() as con:
        if con.execute("SELECT 1 FROM tareas WHERE id=?", (tid,)).fetchone() is None:
            _borrar_archivos([archivo_id])
            raise HTTPException(404, "Tarea no existe.")
        prev = con.execute("SELECT archivo_id FROM entregas WHERE tarea_id=? AND carnet=?",
                           (tid, u["carnet"])).fetchone()
        viejo = prev[0] if prev else None
        con.execute(
            "INSERT INTO entregas(tarea_id,carnet,nombre,comentario,archivo_nombre,archivo_id,enviada) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(tarea_id,carnet) DO UPDATE SET "
            "nombre=excluded.nombre, comentario=excluded.comentario, "
            "archivo_nombre=excluded.archivo_nombre, archivo_id=excluded.archivo_id, "
            "enviada=excluded.enviada",
            (tid, u["carnet"], u["nombre"], comentario.strip(), nombre_orig, archivo_id, time.time()))
        con.commit()
    if viejo and viejo != archivo_id:
        _borrar_archivos([viejo])
    return {"ok": True}


@app.get("/api/entregas/{eid}/archivo")
def descargar(eid: int, u: dict = Depends(usuario)):
    with db() as con:
        e = con.execute("SELECT * FROM entregas WHERE id=?", (eid,)).fetchone()
    if e is None or not e["archivo_id"]:
        raise HTTPException(404, "Sin archivo.")
    if u["role"] != "profesor" and e["carnet"] != u["carnet"]:
        raise HTTPException(403, "No es su entrega.")
    ruta = UPLOAD_DIR / e["archivo_id"]
    if not ruta.exists():
        raise HTTPException(404, "Archivo no encontrado.")
    return FileResponse(ruta, filename=e["archivo_nombre"])


@app.delete("/api/entregas/{eid}")
def borrar_entrega(eid: int, u: dict = Depends(profesor)):
    with db() as con:
        e = con.execute("SELECT archivo_id FROM entregas WHERE id=?", (eid,)).fetchone()
        con.execute("DELETE FROM entregas WHERE id=?", (eid,))
        con.commit()
    if e:
        _borrar_archivos([e[0]])
    return {"ok": True}

# --------------------------------------------------------------------------
# Agenda de la sesion
# --------------------------------------------------------------------------
class EventoPayload(BaseModel):
    descripcion: str = Field(min_length=2, max_length=200)
    momento: str = Field("", max_length=16)          # YYYY-MM-DDTHH:MM


@app.get("/api/agenda")
def agenda(u: dict = Depends(usuario)):
    return ALMACEN.agenda_completa()


@app.post("/api/agenda")
def crear_evento(p: EventoPayload, u: dict = Depends(profesor)):
    momento = p.momento.strip()
    if momento and _limite_ts(momento) is None:
        raise HTTPException(400, "Fecha inválida.")
    ev = ALMACEN.agregar_evento(p.descripcion, momento or None)
    return {"ok": True, "momento": ev.momento}


@app.delete("/api/agenda/{eid}")
def borrar_evento(eid: int, u: dict = Depends(profesor)):
    ALMACEN.borrar_evento(eid)
    return {"ok": True}

# --------------------------------------------------------------------------
# Asistente: el agente coordinador real
# --------------------------------------------------------------------------
def _norm(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t.lower())
                   if unicodedata.category(c) != "Mn")


# Operaciones del catalogo que solo el profesor puede ejecutar. El estudiante
# consulta el ambiente, sus entregas y la agenda; no opera la instalacion.
SOLO_PROFESOR = {"LUZ_ON", "LUZ_OFF", "LUZ_MODO", "ENTREGA_REG"}

# El intento se reporta en el vocabulario que la interfaz ya conoce, de modo
# que refresque la vista del aula cuando corresponda. La accion exacta del
# catalogo viaja aparte, para la bitacora y para mostrar la trazabilidad.
INTENTO_VISIBLE = {
    "LUZ_ON": "LUCES", "LUZ_OFF": "LUCES", "LUZ_MODO": "LUCES", "LUZ_VER": "LUCES",
    "AMBIENTE": "AMBIENTE",
    "ENTREGA_REG": "ENTREGAS", "ENTREGA_VER": "ENTREGAS",
    "AGENDA": "AGENDA", "RESUMEN": "RESUMEN", "NADA": "NONE",
}


class AgenteConRoles(Agente):
    """El agente del proyecto, con control de acceso por rol.

    La comprobacion ocurre despues de clasificar y antes de actuar: saber que
    pidio el usuario exige consultar al modelo, y negar sin clasificar seria
    adivinar. El costo es que una orden negada igual paga sus quince
    segundos, lo cual es correcto: la negacion queda registrada en la
    bitacora con la operacion que se intento.
    """

    def __init__(self, *args, carnet: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.carnet = carnet

    def procesar(self, rol: str, orden: str) -> Respuesta:
        # registrar_entrega falla cuando no hay ninguna tarea creada, y se
        # invoca al confirmar, fuera del manejo de errores de _actuar.
        try:
            return super().procesar(rol, orden)
        except SinTareas as e:
            return Respuesta(Parada.FALTA_DATO, f"No pude registrarla: {e}.",
                             accion="ENTREGA_REG")

    def _actuar(self, rol: str, orden: str, accion: str,
                zona: Optional[str]) -> Respuesta:
        if accion in SOLO_PROFESOR and rol != "profesor":
            que = "las luces" if accion.startswith("LUZ") else "las entregas"
            return Respuesta(
                Parada.FALTA_DATO,
                f"Solo el profesor puede operar {que}.",
                accion=accion,
            )
        return super()._actuar(rol, orden, accion, zona)

    def _ver_entregas(self, rol: str, orden: str) -> Respuesta:
        """El estudiante consulta lo suyo; el profesor, todo.

        Para el estudiante la pregunta util no es quien entrego sino que le
        falta, de modo que se responde con sus tareas pendientes.
        """
        if rol == "profesor":
            return super()._ver_entregas(rol, orden)
        pendientes = self.almacen.tareas_pendientes(self.carnet)
        if not pendientes:
            return Respuesta(Parada.CUMPLIDA, "No tiene tareas pendientes.",
                             accion="ENTREGA_VER", datos={"pendientes": []})
        return Respuesta(
            Parada.CUMPLIDA,
            "Pendientes: " + "; ".join(pendientes) + ".",
            accion="ENTREGA_VER", datos={"pendientes": pendientes})


# Un agente por usuario, no uno por peticion: el flujo de confirmacion de una
# accion irreversible guarda la orden pendiente en memoria del agente, y
# construirlo de nuevo en cada peticion perderia ese estado entre el "¿confirma?"
# y el "si".
_AGENTES: dict = {}


def _agente_de(u: dict) -> "AgenteConRoles":
    clave = u["role"] if u["role"] == "profesor" else f"estudiante:{u.get('carnet', '')}"
    agente = _AGENTES.get(clave)
    if agente is None:
        agente = AgenteConRoles(LUCES, SENSOR, ALMACEN,
                                carnet=u.get("carnet", ""))
        _AGENTES[clave] = agente
    return agente


class AgenteReal(ICoordinatorAgent):
    """Adapta el agente coordinador al contrato que consume el portal."""

    def __init__(self, agente: "AgenteConRoles"):
        self._agente = agente

    async def process_command(self, user_role: str, text_command: str) -> dict:
        # Quince segundos de inferencia en el bucle de eventos dejarian al
        # servidor sin atender nada mas. Va a un hilo.
        r: Respuesta = await asyncio.to_thread(
            self._agente.procesar, user_role, text_command)
        d = r.dict()
        d["accion"] = r.accion
        d["intent"] = INTENTO_VISIBLE.get(r.accion or "", "NONE")
        d["status"] = "success" if r.ejecutada else (
            "denied" if r.motivo is Parada.ERROR else r.motivo.value)
        return d


class CommandPayload(BaseModel):
    command: str = Field(max_length=300)


def get_agent(u: dict = Depends(usuario)) -> ICoordinatorAgent:
    return AgenteReal(_agente_de(u))


@app.post("/api/command")
async def handle_command(p: CommandPayload, u: dict = Depends(usuario),
                         agent: ICoordinatorAgent = Depends(get_agent)):
    if not p.command.strip():
        raise HTTPException(400, "El comando no puede estar vacío.")
    # El rol sale de la sesion del servidor, no del navegador.
    return await agent.process_command(u["role"], p.command)

# --------------------------------------------------------------------------
@app.get("/")
def portal():
    return FileResponse(BASE_DIR / "templates" / "index.html")
