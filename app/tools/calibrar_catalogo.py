#!/usr/bin/env python3
"""Calibra la descripcion de una operacion del catalogo contra el modelo.

POR QUE EXISTE
--------------
Las descripciones del catalogo no son documentacion: son la unica senal que
el modelo de decision recibe para distinguir una operacion de otra. Dos
palabras mal elegidas cuestan aciertos, y el banco general no dice cual
cambiar porque mide el catalogo entero de una vez.

La operacion LUZ_MODO fallo las tres veces en las corridas del 28 de octubre
y del 6 de noviembre, clasificada siempre como NADA. Dos corridas con el
mismo patron no son azar: es la descripcion.

Este banco aisla una operacion, prueba varias descripciones candidatas
contra las frases que deberian activarla, y reporta acierto y confianza de
cada una. El resto del catalogo se mantiene fijo, de modo que la unica
variable sea la descripcion que se esta probando.

COSTO
-----
Cada consulta tarda unos quince segundos sobre la Raspberry Pi. Con cinco
candidatas y tres frases son quince consultas, unos cuatro minutos. El
tamano del prompt no cambia de forma apreciable entre candidatas de longitud
similar, asi que la comparacion es justa.

Uso:
    python3 calibrar_catalogo.py                     # LUZ_MODO, por defecto
    python3 calibrar_catalogo.py --operacion AGENDA
    python3 calibrar_catalogo.py --sin-distractores  # mide el techo
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

SERVIDOR = os.environ.get("JARVIS_OLLAMA", "http://127.0.0.1:11434")
MODELO = "tev1:0.8b"
TIMEOUT = 90.0

VERDE, ROJO, GRIS, FIN = "\033[32m", "\033[31m", "\033[90m", "\033[0m"

# Catalogo vigente. La operacion bajo prueba se sustituye en cada corrida.
CATALOGO = {
    "LUZ_ON":      "Encender iluminacion",
    "LUZ_OFF":     "Apagar iluminacion",
    "LUZ_MODO":    "Cambiar escena",
    "AMBIENTE":    "Consultar ambiente",
    "ENTREGA_REG": "Registrar entrega",
    "ENTREGA_VER": "Consultar entregas",
    "AGENDA":      "Ver agenda",
    "RESUMEN":     "Resumir sesion",
    "NADA":        "Fuera de alcance",
}

# Frases que deberian activar cada operacion. Son las mismas del banco
# general, para que los resultados sean comparables.
FRASES = {
    "LUZ_MODO": [
        "pone el aula en modo proyeccion",
        "cambia a modo lectura",
        "activa la escena de trabajo en grupo",
    ],
    "AGENDA": [
        "que hay programado hoy",
        "cual es la agenda de la sesion",
        "que sigue despues de esto",
    ],
    "ENTREGA_VER": [
        "quien ya entrego",
        "cuantas entregas van",
        "mostrame el estado de las entregas",
    ],
    "RESUMEN": [
        "hacemos un resumen de la clase",
        "resumime como fue la sesion",
        "dame un cierre de lo que hicimos",
    ],
}

# Descripciones candidatas. Dos palabras, como el resto del catalogo: cada
# palabra adicional se paga en las nueve operaciones y en todas las ordenes.
CANDIDATAS = {
    "LUZ_MODO": [
        "Cambiar escena",          # la vigente, como referencia
        "Aplicar escena",
        "Modo del aula",
        "Escena iluminacion",
        "Ambiente del aula",
        "Configurar escena",
    ],
    "AGENDA": [
        "Ver agenda",
        "Agenda de clase",
        "Plan de sesion",
        "Que sigue",
    ],
    "ENTREGA_VER": [
        "Consultar entregas",
        "Estado de entregas",
        "Quien entrego",
    ],
    "RESUMEN": [
        "Resumir sesion",
        "Cierre de clase",
        "Resumen de clase",
    ],
}


def post(cuerpo: dict) -> tuple[dict, float]:
    datos = json.dumps(cuerpo).encode("utf-8")
    req = urllib.request.Request(
        SERVIDOR + "/v1/systemone", data=datos,
        headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read()), time.perf_counter() - t0


def clasificar(orden: str, criterios: dict) -> tuple[str, float, int, float, dict]:
    r, t = post({
        "model": MODELO,
        "state": orden,
        "questions": {
            "accion": {
                "type": "choice",
                "instructions": "Que accion pide el usuario",
                "criteria": criterios,
            }
        },
    })
    bloque = r.get("answers", r).get("accion", {})
    opcion = bloque.get("choice") or bloque.get("answer") or bloque.get("value")
    probs = {k: float(v) for k, v in (bloque.get("probabilities") or {}).items()}
    conf = bloque.get("confidence")
    if conf is None and opcion:
        conf = probs.get(opcion, 0.0)
    tokens = (r.get("usage") or {}).get("input_tokens") or 0
    return opcion, float(conf or 0.0), int(tokens), t, probs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--operacion", default="LUZ_MODO",
                    help="operacion del catalogo a calibrar")
    ap.add_argument("--umbral", type=float, default=0.50,
                    help="umbral de abstencion vigente (RF-02)")
    ap.add_argument("--sin-distractores", action="store_true",
                    help="probar la operacion sola contra NADA, para medir "
                         "el techo: si tampoco acierta asi, el problema no "
                         "es la competencia con otras operaciones")
    args = ap.parse_args()

    op = args.operacion.upper()
    if op not in CATALOGO:
        print(f"operacion desconocida: {op}", file=sys.stderr)
        return 2
    if op not in FRASES or op not in CANDIDATAS:
        print(f"no hay frases o candidatas definidas para {op}", file=sys.stderr)
        return 2

    frases = FRASES[op]
    candidatas = CANDIDATAS[op]

    print("=" * 72)
    print(f"CALIBRACION DE DESCRIPCION — {op}")
    print("=" * 72)
    print(f"  servidor   : {SERVIDOR}")
    print(f"  frases     : {len(frases)}")
    print(f"  candidatas : {len(candidatas)}")
    print(f"  umbral     : {args.umbral:.2f}")
    if args.sin_distractores:
        print("  modo       : sin distractores (solo la operacion y NADA)")
    print(f"  consultas  : {len(frases) * len(candidatas)}"
          f"  (~{len(frases) * len(candidatas) * 15 / 60:.0f} min)")
    print()

    # Calentamiento: la primera consulta incluye la carga del modelo si el
    # servicio acaba de arrancar, y contaminaria la primera candidata.
    try:
        clasificar("calentamiento", dict(CATALOGO))
    except urllib.error.URLError as e:
        print(f"{ROJO}no se pudo consultar {SERVIDOR}: {e}{FIN}", file=sys.stderr)
        print("Verifique que el servicio de inferencia este activo.", file=sys.stderr)
        return 1

    resultados = []
    for desc in candidatas:
        criterios = dict(CATALOGO)
        criterios[op] = desc
        if args.sin_distractores:
            criterios = {op: desc, "NADA": CATALOGO["NADA"]}

        print(f"{'─' * 72}")
        print(f"  «{desc}»")
        aciertos, confs, sobre_umbral, tokens, robos = 0, [], 0, [], {}
        for frase in frases:
            try:
                accion, conf, n_tok, t, probs = clasificar(frase, criterios)
            except Exception as e:
                print(f"    ERROR  {frase!r}: {e}")
                continue
            bien = accion == op
            aciertos += bien
            confs.append(conf)
            tokens.append(n_tok)
            if bien and conf >= args.umbral:
                sobre_umbral += 1
            if not bien and accion:
                robos[accion] = robos.get(accion, 0) + 1
            marca = f"{VERDE}OK   {FIN}" if bien else f"{ROJO}FALLA{FIN}"
            aviso = "" if (not bien or conf >= args.umbral) else \
                f"  {GRIS}(se abstiene: {conf:.2f} < {args.umbral:.2f}){FIN}"
            print(f"    {marca} {t:5.1f}s {n_tok:4d}tok conf={conf:.2f}  "
                  f"-> {accion or '-':<12}{aviso}")
            print(f"           {GRIS}«{frase}»{FIN}")

        resultados.append({
            "desc": desc,
            "aciertos": aciertos,
            "utiles": sobre_umbral,
            "conf": statistics.mean(confs) if confs else 0.0,
            "tokens": round(statistics.mean(tokens)) if tokens else 0,
            "robos": robos,
        })
        if robos:
            detalle = ", ".join(f"{k} ({v})" for k, v in sorted(robos.items()))
            print(f"    {GRIS}se la llevo: {detalle}{FIN}")

    print()
    print("=" * 72)
    print("RESUMEN")
    print("=" * 72)
    print(f"  {'descripcion':<22} {'acierta':>8} {'util':>6} {'conf':>6} {'tok':>5}")
    print(f"  {'-' * 22} {'-' * 8} {'-' * 6} {'-' * 6} {'-' * 5}")
    for r in sorted(resultados, key=lambda x: (-x["utiles"], -x["conf"])):
        print(f"  {r['desc']:<22} {r['aciertos']:>3}/{len(frases):<4} "
              f"{r['utiles']:>6} {r['conf']:>6.2f} {r['tokens']:>5}")

    print()
    print("  'acierta' es cuantas frases clasifico bien; 'util' cuantas ademas")
    print("  superan el umbral de abstencion, que son las que el sistema")
    print("  ejecutaria de verdad. Una descripcion que acierta pero siempre por")
    print("  debajo del umbral no sirve: el agente se abstiene igual.")

    mejor = max(resultados, key=lambda x: (x["utiles"], x["conf"]))
    vigente = next((r for r in resultados if r["desc"] == CATALOGO[op]), None)
    print()
    if mejor["utiles"] == 0:
        print(f"  {ROJO}Ninguna candidata produce una sola clasificacion utilizable.{FIN}")
        print("  Con el modelo y el catalogo actuales esta operacion no es")
        print("  clasificable. Conviene retirarla del catalogo —lo que abarata")
        print("  el prompt de todas las ordenes— y exponerla solo por la")
        print("  interfaz, o repetir con --sin-distractores para ver si el")
        print("  problema es la competencia con otra operacion.")
    elif vigente and mejor["utiles"] > vigente["utiles"]:
        print(f"  {VERDE}Cambiar «{vigente['desc']}» por «{mejor['desc']}»:{FIN}")
        print(f"  pasa de {vigente['utiles']} a {mejor['utiles']} de {len(frases)}"
              f" clasificaciones utilizables.")
    else:
        print(f"  La descripcion vigente «{CATALOGO[op]}» no mejora con las")
        print("  candidatas probadas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
