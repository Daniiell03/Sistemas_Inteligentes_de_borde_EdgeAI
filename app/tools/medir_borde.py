#!/usr/bin/env python3
"""Banco de medicion del borde — Proyecto II EdgeAI 'Jarvis' (Rol B).

Se instala en app/tools/medir_borde.py y se ejecuta EN LA RASPBERRY PI:

    python3 -m tools.medir_borde
    python3 -m tools.medir_borde --salida docs/benchmark/borde-2026-11-02.txt

Produce la evidencia de verificacion del proyecto:

  1. Latencia del carril de decision con el catalogo del aula, p50 y p95.
  2. Latencia del carril generativo con salida acotada, para contrastar.
  3. Tasa de acierto contra un conjunto etiquetado de frases en espanol.
  4. Distribucion de la confianza, insumo para calibrar el umbral.
  5. Validacion de la ley de latencia t = n / V medida el 27/10/2026.
  6. Memoria residente del servidor de inferencia y sus procesos hijos.
  7. Temperatura y limitacion termica del SoC antes y despues.
  8. Lecturas de la MicroSD antes y despues: verificacion de la regla R1.

CAMBIOS RESPECTO DE LA VERSION DEL 25/10/2026
---------------------------------------------
- Catalogo del aula con nueve operaciones en UNA sola etapa. El router de
  dos etapas se elimino: cada etapa paga ~100 tokens de plantilla fija, y
  el diseno costaba 35.6 s medidos.
- La memoria residente suma TODOS los procesos del servidor de inferencia.
  La version anterior consultaba solo el proceso principal, que no es el
  que mantiene los modelos cargados, y reportaba 33 MiB.
- Consulta de calentamiento obligatoria antes de medir: la primera llamada
  tras iniciar el servidor incluye la carga del modelo y contamina la serie.
- Se reporta la velocidad de procesamiento de prompt en tokens por segundo,
  que es la constante que gobierna el diseno del catalogo.

Solo biblioteca estandar: no agrega dependencias a la imagen.
"""
import argparse
import json
import os
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
TIMEOUT = 180
MAX_TOKENS_GENERATIVO = 40

# Velocidad de procesamiento de prompt medida el 27/10/2026 en Pi 4.
# El banco la vuelve a estimar y compara con este valor de referencia.
VELOCIDAD_REFERENCIA = 18.5

# ---------------------------------------------------------------------------
# Catalogo del aula — nueve operaciones, una sola etapa
# ---------------------------------------------------------------------------
# Las descripciones son deliberadamente breves: cada palabra se paga en cada
# consulta. Presupuesto para responder en menos de 15 s: 277 tokens.

ACCIONES = {
    "LUZ_ON":      "Encender iluminacion",
    "LUZ_OFF":     "Apagar iluminacion",
    "LUZ_MODO":    "Cambiar escena",
    "AMBIENTE":    "Consultar ambiente",
    "ENTREGA_REG": "Registrar entrega",
    "ENTREGA_VER": "Consultar entregas",
    "AGENDA":      "Ver agenda",
    "RESUMEN":     "Resumir sesion",
    "NADA":        "Ninguna anterior",
}

ZONAS = {
    "FRENTE": "Zona de proyeccion",
    "FONDO":  "Zona de trabajo",
    "NINGUNA": "No se menciona zona",
}

# Conjunto etiquetado. Tres frases por operacion con vocabulario variado,
# para que la tasa de acierto sea representativa y no un eco del catalogo.
CASOS = [
    ("encende la luz del frente",                  "LUZ_ON"),
    ("prenda las luces por favor",                 "LUZ_ON"),
    ("necesito iluminacion en la zona de trabajo", "LUZ_ON"),
    ("apaga la luz del fondo",                     "LUZ_OFF"),
    ("apaga todas las luces",                      "LUZ_OFF"),
    ("ya no necesitamos la iluminacion",           "LUZ_OFF"),
    ("pone el aula en modo proyeccion",            "LUZ_MODO"),
    ("cambia a modo lectura",                      "LUZ_MODO"),
    ("activa la escena de trabajo en grupo",       "LUZ_MODO"),
    ("a cuanto estamos",                           "AMBIENTE"),
    ("que temperatura hay en el aula",             "AMBIENTE"),
    ("hace mucho calor aqui, cuanto marca",        "AMBIENTE"),
    ("registra la entrega de Maria",               "ENTREGA_REG"),
    ("anota que Jose entrego el laboratorio tres", "ENTREGA_REG"),
    ("marca la tarea como recibida",               "ENTREGA_REG"),
    ("quien ya entrego",                           "ENTREGA_VER"),
    ("cuantas entregas van",                       "ENTREGA_VER"),
    ("mostrame el estado de las entregas",         "ENTREGA_VER"),
    ("que hay programado hoy",                     "AGENDA"),
    ("cual es la agenda de la sesion",             "AGENDA"),
    ("que sigue despues de esto",                  "AGENDA"),
    ("hacemos un resumen de la clase",             "RESUMEN"),
    ("resumime como fue la sesion",                "RESUMEN"),
    ("dame un cierre de lo que hicimos",           "RESUMEN"),
    ("asdfgh qwerty zxcvb",                        "NADA"),
    ("cuanto cuesta un carro en Japon",            "NADA"),
    ("pone musica de los ochenta",                 "NADA"),
]

# Operaciones que requieren el carril generativo
GENERATIVAS = {"RESUMEN"}

PROMPTS_GENERATIVOS = [
    "Resumi en una frase: se registraron 12 entregas de 20 estudiantes.",
    "En una frase, que se hizo en una sesion con 3 laboratorios entregados.",
    "Resumi brevemente: el aula estuvo a 24 grados y 60 por ciento de humedad.",
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
    p = Path("/sys/class/thermal/thermal_zone0/temp")
    if p.exists():
        try:
            return int(p.read_text().strip()) / 1000.0
        except (ValueError, OSError):
            pass
    m = re.search(r"([\d.]+)", sh("vcgencmd measure_temp"))
    return float(m.group(1)) if m else None


def throttled() -> str:
    return sh("vcgencmd get_throttled") or "no disponible"


def memoria():
    datos = {}
    try:
        for linea in Path("/proc/meminfo").read_text().splitlines():
            k, _, v = linea.partition(":")
            datos[k] = int(v.strip().split()[0]) // 1024      # MiB
    except OSError:
        return {}
    return {"total": datos.get("MemTotal"),
            "disponible": datos.get("MemAvailable")}


def rss_inferencia():
    """Memoria residente de TODOS los procesos del servidor de inferencia.

    El servidor principal delega la carga de cada modelo a un proceso hijo
    ('runner'), de modo que consultar solo el proceso padre subestima el
    consumo en mas de un orden de magnitud. Se recorre /proc sumando el RSS
    de todo proceso cuya linea de comando mencione el servidor.
    """
    total = 0
    encontrados = 0
    propio = str(os.getpid())
    for entrada in Path("/proc").iterdir():
        if not entrada.name.isdigit() or entrada.name == propio:
            continue
        try:
            cmdline = (entrada / "cmdline").read_bytes()
            # El ejecutable debe ser el servidor, no un proceso que lo nombre
            # en sus argumentos (este mismo script, por ejemplo).
            if not Path((entrada / "exe").resolve()).name.startswith("ollama"):
                if b"ollama" not in cmdline.split(b"\0")[0]:
                    continue
            for linea in (entrada / "status").read_text().splitlines():
                if linea.startswith("VmRSS:"):
                    total += int(linea.split()[1]) // 1024
                    encontrados += 1
                    break
        except (OSError, ValueError):
            continue
    return (total, encontrados) if encontrados else (None, 0)


def lecturas_sd():
    """Sectores leidos del medio de arranque. Verificacion de la regla R1."""
    total = {}
    try:
        for linea in Path("/proc/diskstats").read_text().splitlines():
            campos = linea.split()
            if len(campos) < 14:
                continue
            nombre = campos[2]
            if nombre.startswith("mmcblk") and "p" not in nombre:
                total[nombre] = {"lecturas": int(campos[3]),
                                 "sectores_leidos": int(campos[5])}
    except OSError:
        pass
    return total


# ---------------------------------------------------------------------------
# Clientes de los dos carriles
# ---------------------------------------------------------------------------

def post(ruta: str, cuerpo: dict):
    datos = json.dumps(cuerpo).encode()
    req = urllib.request.Request(OLLAMA + ruta, data=datos,
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        respuesta = json.loads(r.read())
    return respuesta, time.perf_counter() - t0


def decidir(state: str):
    """Una sola etapa: accion y zona en la misma solicitud."""
    return post("/v1/systemone", {
        "model": MODELO_DECISION,
        "state": state,
        "questions": {
            "accion": {"type": "choice",
                       "instructions": "Que accion pide el usuario",
                       "criteria": ACCIONES},
            "zona": {"type": "choice",
                     "instructions": "Que zona menciona",
                     "criteria": ZONAS},
            "riesgo": {"type": "noul",
                       "instructions": "Es una accion irreversible"},
        }})


def elegida(respuesta: dict, clave: str):
    """Extrae (opcion, probabilidad) tolerando variaciones de la forma."""
    r = respuesta.get("answers", respuesta).get(clave, {})
    if isinstance(r, str):
        return r, None
    opcion = r.get("choice") or r.get("answer") or r.get("value")
    prob = r.get("confidence") or r.get("probability") or r.get("score")
    if prob is None and isinstance(r.get("probabilities"), dict) and opcion:
        prob = r["probabilities"].get(opcion)
    return opcion, prob


def tokens_entrada(respuesta: dict):
    uso = respuesta.get("usage", {})
    return uso.get("input_tokens") or uso.get("prompt_eval_count")


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
    out(f"Equipo: {sh('tr -d \\\\0 < /proc/device-tree/model 2>/dev/null') or 'desconocido'}")
    out(f"Kernel: {sh('uname -r')}")
    out(f"Catalogo: {len(ACCIONES)} operaciones, una etapa")
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

    try:
        with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=10) as r:
            etiquetas = json.loads(r.read())
        presentes = [m["name"] for m in etiquetas.get("models", [])]
        out(f"  Modelos presentes   : {', '.join(presentes) or 'ninguno'}")
        faltan = [m for m in (MODELO_DECISION, MODELO_GENERATIVO) if m not in presentes]
        if faltan:
            out(f"  AVISO: no figuran: {', '.join(faltan)}")
    except urllib.error.URLError as e:
        out(f"\nNo se pudo contactar a Ollama en {OLLAMA}: {e}")
        out("Verifique:  systemctl status ollama")
        return 1

    # --- calentamiento obligatorio ---
    # La primera consulta tras iniciar el servidor incluye la carga del modelo
    # y falsea la serie: medido el 27/10/2026, una consulta de 106 tokens
    # tardo 12.8 s recien iniciado el servidor contra 7.1 s de una de 130.
    out()
    out("Calentando el modelo de decision (no se mide)...")
    try:
        _, t_carga = decidir("calentamiento del sistema")
        out(f"  primera consulta: {t_carga:.2f}s  (incluye la carga del modelo)")
    except Exception as e:
        out(f"Fallo el calentamiento: {e}")
        return 1
    rss_decision, n_proc = rss_inferencia()

    # --- carril de decision ---
    out()
    out("CARRIL DE DECISION — una etapa")
    out("-" * 72)
    out(f"{'tiempo':>8} {'tokens':>7} {'tok/s':>6} {'conf':>5}  resultado")
    out("-" * 72)

    tiempos, tokens, confs = [], [], []
    aciertos = errores = 0

    for frase, esperada in CASOS:
        try:
            r, t = decidir(frase)
            accion, conf = elegida(r, "accion")
            n_tok = tokens_entrada(r)
        except Exception as e:
            errores += 1
            out(f"{'':>8} {'':>7} {'':>6} {'':>5}  ERROR  «{frase[:34]}»: {e}")
            continue

        tiempos.append(t)
        if n_tok:
            tokens.append(n_tok)
        if conf is not None:
            confs.append(conf)

        bien = accion == esperada
        aciertos += bien
        marca = "OK   " if bien else "FALLA"
        detalle = accion if bien else f"{accion}  (esperado {esperada})"
        vel = f"{n_tok/t:6.1f}" if n_tok else "     -"
        cs = f"{conf:.2f}" if conf is not None else "  -  "
        out(f"{t:7.2f}s {n_tok if n_tok else '-':>7} {vel} {cs:>5}  {marca} {detalle}")
        out(f"{'':>23}  «{frase}»")

    n = len(tiempos)
    out("-" * 72)
    if n:
        out(f"Casos medidos        : {n} de {len(CASOS)}  ({errores} con error)")
        out(f"Acierto de accion    : {aciertos}/{n}  ({100*aciertos/n:.1f} %)")
        out(f"Latencia p50/p95/max : {pct(tiempos,50):.2f}s / {pct(tiempos,95):.2f}s / {max(tiempos):.2f}s")
        out(f"Latencia media       : {statistics.mean(tiempos):.2f}s")
        if confs:
            out(f"Confianza min/p50    : {min(confs):.2f} / {pct(confs,50):.2f}")
            out(f"  Umbral sugerido    : por debajo de {min(confs):.2f} se descartarian aciertos")

    # --- validacion de la ley de latencia ---
    if tokens and n:
        vel = statistics.mean(t_ / s for t_, s in zip(tokens, tiempos))
        out()
        out("LEY DE LATENCIA  t = n / V")
        out("-" * 72)
        out(f"  Tokens por consulta (media)  : {statistics.mean(tokens):.0f}")
        out(f"  Velocidad medida             : {vel:.1f} tok/s")
        out(f"  Referencia del 27/10/2026    : {VELOCIDAD_REFERENCIA:.1f} tok/s")
        desvio = 100 * abs(vel - VELOCIDAD_REFERENCIA) / VELOCIDAD_REFERENCIA
        out(f"  Desvio                       : {desvio:.1f} %")
        out(f"  Presupuesto para 15 s        : {15*vel:.0f} tokens")

    # --- carril generativo ---
    gen = []
    rss_ambos = None
    if not args.sin_generativo:
        out()
        out(f"CARRIL GENERATIVO — salida acotada a {MAX_TOKENS_GENERATIVO} tokens")
        out("-" * 72)
        for prompt in PROMPTS_GENERATIVOS:
            try:
                r, t = post("/api/chat", {
                    "model": MODELO_GENERATIVO,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"num_predict": MAX_TOKENS_GENERATIVO}})
                gen.append(t)
                texto = (r.get("message", {}).get("content") or "").replace("\n", " ")
                carga = r.get("load_duration", 0) / 1e9
                p_eval = r.get("prompt_eval_duration", 0) / 1e9
                e_eval = r.get("eval_duration", 0) / 1e9
                n_gen = r.get("eval_count", 0)
                out(f"{t:7.2f}s  carga {carga:.1f}s · prompt {p_eval:.1f}s · genera {e_eval:.1f}s ({n_gen} tok)")
                out(f"          -> {texto[:90]}")
            except Exception as e:
                out(f"  ERROR: {e}")
        rss_ambos, _ = rss_inferencia()
        if gen:
            out("-" * 72)
            out(f"Generativo p50/max   : {pct(gen,50):.2f}s / {max(gen):.2f}s")

    # --- comparacion ---
    if n and gen:
        out()
        out("COMPARACION DE CARRILES")
        out("-" * 72)
        out(f"  Decision   p50 = {pct(tiempos,50):.2f}s")
        out(f"  Generativo p50 = {pct(gen,50):.2f}s")
        if pct(tiempos, 50):
            f = pct(gen, 50) / pct(tiempos, 50)
            rel = "mas rapido" if f > 1 else "mas lento"
            out(f"  El carril de decision es {f:.1f}x {rel} que el generativo")

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
    if mem_fin and mem_ini:
        consumo = mem_ini['disponible'] - mem_fin['disponible']
        out(f"  Memoria disponible : {mem_ini['disponible']} -> {mem_fin['disponible']} MiB  (consumo {consumo} MiB)")
    if rss_decision:
        out(f"  RSS del servidor con el modelo de decision : {rss_decision} MiB ({n_proc} procesos)")
    if rss_ambos:
        out(f"  RSS del servidor con ambos modelos         : {rss_ambos} MiB")

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
                out("    CUMPLE: lectura despreciable frente al tamano de los modelos")
            else:
                out("    NO CUMPLE: hubo lectura significativa durante la corrida")
                out("    Verifique OLLAMA_MAX_LOADED_MODELS=2 en el entorno del servicio")
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
