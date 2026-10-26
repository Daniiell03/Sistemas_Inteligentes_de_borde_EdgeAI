#!/usr/bin/env python3
"""Banco de medicion del borde: los dos carriles de inferencia sobre la Pi (Rol B).

Se instala en app/tools/medir_borde.py y se ejecuta EN LA RASPBERRY PI:

    python3 -m tools.medir_borde
    python3 -m tools.medir_borde --salida docs/benchmark/borde-2026-11-02.txt

Produce la evidencia central de la verificacion del proyecto:

  1. Latencia del carril de decision por etapa (dominio y accion), p50 y p95.
  2. Latencia del carril generativo, para contrastar.
  3. Tasa de acierto de dominio y de accion contra un conjunto etiquetado.
  4. Distribucion de la confianza, insumo para calibrar el umbral.
  5. Memoria residente de ollama y memoria libre del sistema.
  6. Temperatura y limitacion termica del SoC antes y despues de la corrida.
  7. Lecturas de la MicroSD antes y despues: verificacion de la regla R1.

Solo biblioteca estandar: no agrega dependencias a la imagen.
"""
import argparse
import json
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

OLLAMA = "http://127.0.0.1:11434"
MODELO_DECISION = "tev1:0.8b"
MODELO_GENERATIVO = "llama3.2:1b"
TIMEOUT = 120

# ---------------------------------------------------------------------------
# Catalogo del router de dos etapas (espejo reducido de intents.yaml)
# ---------------------------------------------------------------------------

DOMINIOS = {
    "AGENDA":       "Tareas, eventos, recordatorios o resumen del dia del usuario",
    "CASA":         "Luces de habitaciones, escenas de iluminacion o automatizaciones del hogar",
    "SISTEMA":      "Operaciones sobre la computadora del usuario: aplicaciones, volumen, pantalla",
    "AMBIENTE":     "Temperatura, humedad o confort del ambiente medido por el sensor",
    "CONOCIMIENTO": "Pregunta abierta que requiere una explicacion elaborada",
    "META":         "Estado del asistente, ayuda sobre lo que puede hacer, o confirmacion",
}

ACCIONES = {
    "AGENDA": {
        "TASK_ADD":      "Agregar una tarea nueva a la lista",
        "TASK_LIST":     "Consultar las tareas pendientes",
        "TASK_COMPLETE": "Marcar una tarea como terminada",
        "TASK_DELETE":   "Eliminar una tarea de la lista",
        "EVENT_ADD":     "Agendar una reunion o evento con fecha",
        "REMIND_ADD":    "Crear un recordatorio para mas tarde",
        "DAY_SUMMARY":   "Resumir como va el dia",
    },
    "CASA": {
        "LIGHT_ON":      "Encender la luz de una habitacion",
        "LIGHT_OFF":     "Apagar la luz de una habitacion",
        "LIGHT_ALL_OFF": "Apagar todas las luces",
        "LIGHT_DIM":     "Atenuar o bajar la intensidad de una luz",
        "LIGHT_COLOR":   "Cambiar el color de una luz",
        "LIGHT_STATUS":  "Consultar que luces estan encendidas",
        "SCENE_APPLY":   "Aplicar una escena predefinida de iluminacion",
        "RULE_ADD":      "Crear una automatizacion condicional para el futuro",
    },
    "SISTEMA": {
        "APP_OPEN":    "Abrir una aplicacion en la computadora",
        "APP_CLOSE":   "Cerrar una aplicacion",
        "VOLUME_SET":  "Cambiar el volumen",
        "SCREEN_LOCK": "Bloquear la pantalla",
        "FOCUS_START": "Iniciar un bloque de enfoque o pomodoro",
    },
    "AMBIENTE": {
        "READ_TEMP":   "Consultar la temperatura actual",
        "READ_HUM":    "Consultar la humedad actual",
        "ENV_HISTORY": "Consultar el historico del ambiente",
    },
    "CONOCIMIENTO": {
        "ASK_GENERAL": "Responder una pregunta abierta",
    },
    "META": {
        "STATUS":  "Informar el estado del sistema",
        "HELP":    "Explicar que puede hacer el asistente",
        "UNKNOWN": "Ninguna de las anteriores",
    },
}

HABITACIONES = {
    "SALA":    "La sala o el living",
    "CUARTO":  "El cuarto o dormitorio",
    "NINGUNA": "No se menciona ninguna habitacion",
}

# Conjunto etiquetado: (frase, dominio esperado, accion esperada)
CASOS = [
    ("prende la luz de la sala",                      "CASA",         "LIGHT_ON"),
    ("apaga el cuarto por favor",                     "CASA",         "LIGHT_OFF"),
    ("apaga todas las luces",                         "CASA",         "LIGHT_ALL_OFF"),
    ("baja un poco la luz de la sala",                "CASA",         "LIGHT_DIM"),
    ("pone el cuarto en azul",                        "CASA",         "LIGHT_COLOR"),
    ("que luces estan encendidas",                    "CASA",         "LIGHT_STATUS"),
    ("activa el modo cine",                           "CASA",         "SCENE_APPLY"),
    ("cuando pasen los 28 grados pone la sala en azul", "CASA",       "RULE_ADD"),
    ("hace mucho calor, dime a cuanto estamos",       "AMBIENTE",     "READ_TEMP"),
    ("que temperatura hay",                           "AMBIENTE",     "READ_TEMP"),
    ("que tan humedo esta el ambiente",               "AMBIENTE",     "READ_HUM"),
    ("a que hora hizo mas calor hoy",                 "AMBIENTE",     "ENV_HISTORY"),
    ("agrega comprar cafe a mis tareas",              "AGENDA",       "TASK_ADD"),
    ("que tengo pendiente hoy",                       "AGENDA",       "TASK_LIST"),
    ("marca como hecha la tarea del informe",         "AGENDA",       "TASK_COMPLETE"),
    ("borra la tarea de comprar cafe",                "AGENDA",       "TASK_DELETE"),
    ("tengo reunion el jueves a las tres",            "AGENDA",       "EVENT_ADD"),
    ("recordame llamar al doctor en una hora",        "AGENDA",       "REMIND_ADD"),
    ("resumime como va el dia",                       "AGENDA",       "DAY_SUMMARY"),
    ("abrime el navegador",                           "SISTEMA",      "APP_OPEN"),
    ("cerra spotify",                                 "SISTEMA",      "APP_CLOSE"),
    ("subi el volumen",                               "SISTEMA",      "VOLUME_SET"),
    ("bloquea la pantalla",                           "SISTEMA",      "SCREEN_LOCK"),
    ("modo enfoque veinticinco minutos",              "SISTEMA",      "FOCUS_START"),
    ("como funciona un transistor",                   "CONOCIMIENTO", "ASK_GENERAL"),
    ("que podes hacer",                               "META",         "HELP"),
    ("como estas",                                    "META",         "STATUS"),
    ("asdfgh qwerty zxcvb",                           "META",         "UNKNOWN"),
]

GENERATIVOS = [
    "Resumi en dos frases que hace un sistema operativo embebido.",
    "Escribi un chiste corto sobre compilar software.",
    "Explica en tres lineas que es la inferencia en el borde.",
]


# ---------------------------------------------------------------------------
# Utilidades del sistema
# ---------------------------------------------------------------------------

def sh(cmd: str) -> str:
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=15).stdout.strip()
    except Exception:
        return ""


def temperatura_c():
    for ruta in ("/sys/class/thermal/thermal_zone0/temp",):
        p = Path(ruta)
        if p.exists():
            try:
                return int(p.read_text().strip()) / 1000.0
            except (ValueError, OSError):
                pass
    salida = sh("vcgencmd measure_temp")
    m = re.search(r"([\d.]+)", salida)
    return float(m.group(1)) if m else None


def throttled() -> str:
    s = sh("vcgencmd get_throttled")
    return s or "no disponible"


def memoria():
    datos = {}
    try:
        for linea in Path("/proc/meminfo").read_text().splitlines():
            k, _, v = linea.partition(":")
            datos[k] = int(v.strip().split()[0]) // 1024      # MiB
    except OSError:
        return {}
    return {"total": datos.get("MemTotal"), "disponible": datos.get("MemAvailable"),
            "libre": datos.get("MemFree")}


def rss_ollama():
    pid = sh("pidof ollama").split()
    if not pid:
        return None
    try:
        for linea in Path(f"/proc/{pid[0]}/status").read_text().splitlines():
            if linea.startswith("VmRSS:"):
                return int(linea.split()[1]) // 1024          # MiB
    except OSError:
        pass
    return None


def lecturas_sd():
    """Sectores leidos del dispositivo raiz. Verificacion de la regla R1."""
    total = {}
    try:
        for linea in Path("/proc/diskstats").read_text().splitlines():
            campos = linea.split()
            if len(campos) < 14:
                continue
            nombre = campos[2]
            if nombre.startswith("mmcblk") and "p" not in nombre:
                total[nombre] = {"lecturas": int(campos[3]),
                                 "sectores_leidos": int(campos[5]),
                                 "escrituras": int(campos[7]),
                                 "sectores_escritos": int(campos[9])}
    except OSError:
        pass
    return total


# ---------------------------------------------------------------------------
# Clientes HTTP de los dos carriles
# ---------------------------------------------------------------------------

def post(ruta: str, cuerpo: dict):
    datos = json.dumps(cuerpo).encode()
    req = urllib.request.Request(OLLAMA + ruta, data=datos,
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        respuesta = json.loads(r.read())
    return respuesta, time.perf_counter() - t0


def preguntar(state: str, questions: dict):
    return post("/v1/systemone", {"model": MODELO_DECISION,
                                  "state": state, "questions": questions})


def elegida(respuesta: dict, clave: str):
    """Extrae (opcion, probabilidad) tolerando variaciones de la forma."""
    r = respuesta.get("answers", respuesta).get(clave, {})
    if isinstance(r, str):
        return r, None
    opcion = r.get("answer") or r.get("choice") or r.get("value")
    prob = r.get("probability") or r.get("confidence") or r.get("score")
    if prob is None and isinstance(r.get("probabilities"), dict) and opcion:
        prob = r["probabilities"].get(opcion)
    return opcion, prob


def pct(valores, p):
    if not valores:
        return 0.0
    orden = sorted(valores)
    k = min(int(round((p / 100) * (len(orden) - 1))), len(orden) - 1)
    return orden[k]


# ---------------------------------------------------------------------------
# Corrida
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--salida", type=Path, help="archivo donde guardar el reporte")
    ap.add_argument("--sin-generativo", action="store_true",
                    help="omitir el carril generativo")
    args = ap.parse_args()

    lineas = []

    def out(s=""):
        print(s)
        lineas.append(s)

    out("=" * 72)
    out("MEDICION DEL BORDE — Proyecto II EdgeAI 'Jarvis'")
    out(f"Fecha: {datetime.now().isoformat(timespec='seconds')}")
    out(f"Equipo: {sh('cat /proc/device-tree/model 2>/dev/null | tr -d \\\\0') or 'desconocido'}")
    out(f"Kernel: {sh('uname -r')}")
    out("=" * 72)

    # --- estado inicial ---
    t_ini = temperatura_c()
    mem_ini = memoria()
    sd_ini = lecturas_sd()
    out()
    out("ESTADO INICIAL")
    out(f"  Temperatura del SoC : {t_ini:.1f} C" if t_ini else "  Temperatura: n/d")
    out(f"  Throttling          : {throttled()}")
    if mem_ini:
        out(f"  Memoria total       : {mem_ini['total']} MiB")
        out(f"  Memoria disponible  : {mem_ini['disponible']} MiB")

    # --- disponibilidad del servidor ---
    try:
        with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=10) as r:
            etiquetas = json.loads(r.read())
        presentes = [m["name"] for m in etiquetas.get("models", [])]
        out(f"  Modelos presentes   : {', '.join(presentes) or 'ninguno'}")
        faltan = [m for m in (MODELO_DECISION, MODELO_GENERATIVO) if m not in presentes]
        if faltan:
            out(f"  AVISO: no figuran en el servidor: {', '.join(faltan)}")
    except urllib.error.URLError as e:
        out(f"\nNo se pudo contactar a Ollama en {OLLAMA}: {e}")
        out("Verifique:  systemctl status ollama")
        return 1

    # --- calentamiento ---
    out()
    out("Calentando el modelo de decision (la primera carga no se mide)...")
    try:
        preguntar("prueba de calentamiento",
                  {"dominio": {"type": "choice",
                               "instructions": "Dominio de la orden",
                               "criteria": DOMINIOS}})
    except Exception as e:
        out(f"Fallo el calentamiento: {e}")
        return 1
    rss_tras_decision = rss_ollama()

    # --- carril de decision ---
    out()
    out("CARRIL DE DECISION — router de dos etapas")
    out("-" * 72)
    out(f"{'etapa1':>7} {'etapa2':>7} {'total':>7}  {'conf':>5}  resultado")
    out("-" * 72)

    e1, e2, tot, confs = [], [], [], []
    ok_dom = ok_acc = 0
    errores = 0

    for frase, dom_esp, acc_esp in CASOS:
        try:
            r1, t1 = preguntar(frase, {
                "dominio": {"type": "choice",
                            "instructions": "A que dominio pertenece la orden del usuario",
                            "criteria": DOMINIOS}})
            dom, c1 = elegida(r1, "dominio")

            criterios = ACCIONES.get(dom, ACCIONES["META"])
            r2, t2 = preguntar(frase, {
                "accion": {"type": "choice",
                           "instructions": "Que accion concreta pide el usuario",
                           "criteria": criterios},
                "destino": {"type": "choice",
                            "instructions": "Que habitacion menciona la orden",
                            "criteria": HABITACIONES},
                "completo": {"type": "noul",
                             "instructions": "La orden tiene toda la informacion necesaria para ejecutarse"},
                "riesgo": {"type": "noul",
                           "instructions": "Es una accion destructiva o irreversible"}})
            acc, c2 = elegida(r2, "accion")
        except Exception as e:
            errores += 1
            out(f"{'':>7} {'':>7} {'':>7}  {'':>5}  ERROR  «{frase[:38]}»: {e}")
            continue

        e1.append(t1)
        e2.append(t2)
        tot.append(t1 + t2)
        conf = min(x for x in (c1, c2) if x is not None) if (c1 or c2) else None
        if conf is not None:
            confs.append(conf)

        bien_dom = dom == dom_esp
        bien_acc = acc == acc_esp
        ok_dom += bien_dom
        ok_acc += bien_acc
        marca = "OK  " if (bien_dom and bien_acc) else "FALLA"
        detalle = f"{dom}/{acc}"
        if not (bien_dom and bien_acc):
            detalle += f"  (esperado {dom_esp}/{acc_esp})"
        cs = f"{conf:.2f}" if conf is not None else "  -  "
        out(f"{t1:6.2f}s {t2:6.2f}s {t1+t2:6.2f}s  {cs:>5}  {marca} {detalle}")
        out(f"{'':>24}  «{frase}»")

    n = len(tot)
    out("-" * 72)
    if n:
        out(f"Casos medidos        : {n} de {len(CASOS)}  ({errores} con error)")
        out(f"Acierto de dominio   : {ok_dom}/{n}  ({100*ok_dom/n:.1f} %)")
        out(f"Acierto de accion    : {ok_acc}/{n}  ({100*ok_acc/n:.1f} %)")
        out(f"Etapa 1  p50/p95     : {pct(e1,50):.2f}s / {pct(e1,95):.2f}s")
        out(f"Etapa 2  p50/p95     : {pct(e2,50):.2f}s / {pct(e2,95):.2f}s")
        out(f"Total    p50/p95/max : {pct(tot,50):.2f}s / {pct(tot,95):.2f}s / {max(tot):.2f}s")
        out(f"Latencia media       : {statistics.mean(tot):.2f}s")
        if confs:
            out(f"Confianza min/p50    : {min(confs):.2f} / {pct(confs,50):.2f}")
            out(f"  Umbral sugerido    : por debajo de {min(confs):.2f} se descartarian aciertos")

    # --- carril generativo ---
    gen = []
    rss_tras_generativo = None
    if not args.sin_generativo:
        out()
        out("CARRIL GENERATIVO — contraste")
        out("-" * 72)
        for prompt in GENERATIVOS:
            try:
                r, t = post("/api/chat", {
                    "model": MODELO_GENERATIVO,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False})
                gen.append(t)
                texto = (r.get("message", {}).get("content") or "").replace("\n", " ")
                out(f"{t:6.2f}s  «{prompt[:44]}»")
                out(f"         -> {texto[:100]}")
            except Exception as e:
                out(f"  ERROR  «{prompt[:44]}»: {e}")
        rss_tras_generativo = rss_ollama()
        if gen:
            out("-" * 72)
            out(f"Generativo p50/max   : {pct(gen,50):.2f}s / {max(gen):.2f}s")

    # --- comparacion: el resultado central del proyecto ---
    if n and gen:
        out()
        out("COMPARACION DE CARRILES  (resultado central de la verificacion)")
        out("-" * 72)
        out(f"  Decision   p50 = {pct(tot,50):.2f}s")
        out(f"  Generativo p50 = {pct(gen,50):.2f}s")
        factor = pct(gen, 50) / pct(tot, 50) if pct(tot, 50) else 0
        out(f"  El carril de decision es {factor:.1f}x mas rapido que el generativo")

    # --- estado final ---
    t_fin = temperatura_c()
    mem_fin = memoria()
    sd_fin = lecturas_sd()

    out()
    out("ESTADO FINAL")
    out("-" * 72)
    if t_ini and t_fin:
        out(f"  Temperatura : {t_ini:.1f} C -> {t_fin:.1f} C  (delta {t_fin-t_ini:+.1f})")
    out(f"  Throttling  : {throttled()}")
    if mem_fin:
        out(f"  Memoria disponible : {mem_ini['disponible']} -> {mem_fin['disponible']} MiB")
    if rss_tras_decision:
        out(f"  RSS de ollama tras cargar el modelo de decision : {rss_tras_decision} MiB")
    if rss_tras_generativo:
        out(f"  RSS de ollama con ambos modelos residentes      : {rss_tras_generativo} MiB")

    out()
    out("REGLA R1 — ningun modelo se lee de la MicroSD tras el arranque")
    out("-" * 72)
    if sd_ini and sd_fin:
        for disco in sd_ini:
            d_sec = sd_fin[disco]["sectores_leidos"] - sd_ini[disco]["sectores_leidos"]
            d_lec = sd_fin[disco]["lecturas"] - sd_ini[disco]["lecturas"]
            mib = d_sec * 512 / (1024 * 1024)
            out(f"  {disco}: {d_lec} lecturas, {d_sec} sectores ({mib:.2f} MiB)")
            if mib < 5:
                out(f"    CUMPLE: lectura despreciable frente a los {rss_tras_decision or '?'} MiB del modelo")
            else:
                out("    REVISAR: hubo lectura significativa durante la corrida")
    else:
        out("  No se pudo leer /proc/diskstats")

    out()
    out("=" * 72)

    if args.salida:
        args.salida.parent.mkdir(parents=True, exist_ok=True)
        args.salida.write_text("\n".join(lineas) + "\n")
        print(f"\nReporte guardado en {args.salida}")

    return 0 if n and not errores else 1


if __name__ == "__main__":
    sys.exit(main())
