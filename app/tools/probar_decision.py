"""Prueba del carril de decision (/v1/systemone) con frases en espanol.

Uso: python3 probar_decision.py [URL]   (por defecto http://127.0.0.1:11434/v1/systemone)
Solo usa la biblioteca estandar: corre igual en la laptop y en la Pi.
Reporta aciertos, latencia y un umbral de confianza sugerido.
"""
import json
import statistics
import sys
import time
import urllib.request

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:11434/v1/systemone"
MODEL = "tev1:0.8b"
CRITERIA = {
    "READ_TEMP": "Consultar la temperatura, o si hace calor o frío",
    "READ_HUM": "Consultar la humedad del ambiente",
    "LED_ON": "Encender la luz o el LED (prender, encender, activar)",
    "LED_OFF": "Apagar la luz o el LED (apagar, desactivar)",
    "UNKNOWN": "Ninguna de las anteriores",
}
CASOS = [
    ("hace mucho calor, dime a cuánto estamos", "READ_TEMP"),
    ("qué temperatura hay en el cuarto", "READ_TEMP"),
    ("hace frío aquí, cuántos grados hay", "READ_TEMP"),
    ("qué tan húmedo está el ambiente", "READ_HUM"),
    ("dime la humedad", "READ_HUM"),
    ("está muy seco el aire", "READ_HUM"),
    ("prende la luz", "LED_ON"),
    ("enciende el led por favor", "LED_ON"),
    ("activa el led", "LED_ON"),
    ("apaga la luz", "LED_OFF"),
    ("apaga el led", "LED_OFF"),
    ("ya no quiero la luz encendida", "LED_OFF"),
    ("cuéntame un chiste", "UNKNOWN"),
    ("qué hora es", "UNKNOWN"),
    ("pon música", "UNKNOWN"),
    ("llama a mi mamá", "UNKNOWN"),
]


def decidir(frase):
    body = {
        "model": MODEL,
        "state": frase,
        "questions": {
            "intent": {
                "type": "choice",
                "instructions": "¿Qué acción pide el usuario?",
                "criteria": CRITERIA,
            }
        },
    }
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.load(r)
    return resp["answers"]["intent"], time.monotonic() - t0


print("Calentando el modelo (la primera carga no se mide)...")
decidir("hola")

aciertos, tiempos, conf_ok, conf_mal = 0, [], [], []
for frase, esperado in CASOS:
    ans, dt = decidir(frase)
    conf = ans.get("confidence", 0.0)
    ok = ans["choice"] == esperado
    aciertos += ok
    tiempos.append(dt)
    (conf_ok if ok else conf_mal).append(conf)
    print(f"{'OK ' if ok else 'MAL'} {dt:5.2f} s  conf={conf:.2f}  "
          f"{ans['choice']:<9} (esperado {esperado:<9})  «{frase}»")

print(f"\nAciertos: {aciertos}/{len(CASOS)}  ·  latencia media {statistics.mean(tiempos):.2f} s  "
      f"·  máx {max(tiempos):.2f} s")
print(f"Confianza mínima en aciertos: {min(conf_ok):.2f}")
if conf_mal:
    print(f"Confianza máxima en errores:  {max(conf_mal):.2f}")
    if max(conf_mal) < min(conf_ok):
        print(f"Umbral sugerido: {(max(conf_mal) + min(conf_ok)) / 2:.2f} "
              f"(rechaza todos los errores sin perder aciertos)")
    else:
        print("Errores y aciertos se traslapan en confianza: revisar descripciones o frases.")
else:
    print("Sin errores en esta corrida.")
