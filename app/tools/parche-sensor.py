#!/usr/bin/env python3
"""Hace caducar la lectura del sensor y deja de escribir duplicados.

POR QUE
-------
El 10 de noviembre el portal mostraba 79,8 grados mientras el sysfs del
nucleo entregaba 84,4, y la tabla de ambiente tenia 867 filas con el mismo
valor, una cada cinco segundos. Las dos cosas son el mismo defecto.

El muestreador descarta las lecturas fuera del rango de validez, que va de
-40 a 80 grados. El sensor entrega 84,4, de modo que todas se rechazan y
'_last' conserva indefinidamente el ultimo valor que si paso el filtro. El
portal lo publica sin distinguirlo de una lectura reciente, y el muestreador
lo escribe en la base cada cinco segundos.

El filtro esta bien: un aula a mas de 80 grados no existe, y publicar ese
valor seria peor. Lo que falla es que su rechazo sea invisible. Un sensor
que dejo de dar datos validos debe verse como un sensor sin datos, no como
un sensor con datos viejos.

Esto tambien reinterpreta algo que veniamos arrastrando desde el 6 de
noviembre. Lo que atribuiamos a una tasa de falla del 70 por ciento del
controlador 'dht11' del nucleo no era del controlador: era este filtro
rechazando valores imposibles. El controlador entrega lecturas con su suma
de comprobacion valida; lo que entrega son valores malos, lo que apunta al
sensor o a la falta del pull-up de 10 kOhm en la linea de datos, no al
software.

QUE CAMBIA
----------
  hal_rpi.py   Dht22IioSensor caduca la lectura a los seis periodos, separa
               los errores de entrada/salida de los valores fuera de rango,
               y conserva el ultimo valor rechazado para diagnostico.
  portal.py    guardar_muestra() no escribe una lectura ya escrita, y no
               inventa filas cuando el sensor esta caducado.

POR QUE SEIS PERIODOS
---------------------
El sensor publica cada 2,5 s y el portal muestrea cada 5 s. Con tres
periodos (7,5 s) una sola falla de lectura del DHT22 —frecuentes y
normales en este sensor— ya dejaria la vista en "no disponible". Con seis
(15 s) hacen falta varias fallas seguidas, y una temperatura de aula de
quince segundos de antiguedad sigue siendo actual.

QUE NO CAMBIA Y POR QUE
-----------------------
El rango de validez se queda igual. Y 'registrar_ambiente()' de
persistencia.py queda sin usar: hay dos muestreadores en el proyecto y el
que corre es el del portal. Unificarlos es lo correcto, pero toca codigo
del Rol A sin necesidad funcional, de modo que va a la deuda tecnica del
documento en lugar de al parche.

Uso:  python3 parche-sensor.py app/jarvis
"""

import sys
from pathlib import Path

# --------------------------------------------------------------------------
# hal_rpi.py — constructor
# --------------------------------------------------------------------------
INIT_VIEJO = '''class Dht22IioSensor(TempHumSensor):
    """Muestrea el DHT22 en un hilo y guarda la ultima lectura valida."""

    def __init__(self, period_s: float = 2.5):
        self._dev = self._find_device()
        self._period = period_s
        self._last: Optional[TempHumReading] = None
        self._lock = threading.Lock()
        self.errors = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="dht22", daemon=True)
        self._thread.start()
'''

INIT_NUEVO = '''# Rango fisicamente posible en un aula. Una lectura fuera de el no se
# publica: es mas seguro no tener dato que tener uno falso.
TEMP_MIN_C, TEMP_MAX_C = -40.0, 80.0
HUM_MIN_PCT, HUM_MAX_PCT = 0.0, 100.0

# Cuantos periodos de muestreo puede tener una lectura antes de considerarse
# vencida. Seis, no tres: el DHT22 falla lecturas con frecuencia y con un
# margen corto la vista parpadearia a "no disponible" por una sola falla.
PERIODOS_DE_VALIDEZ = 6


class Dht22IioSensor(TempHumSensor):
    """Muestrea el DHT22 en un hilo y publica la ultima lectura valida.

    La lectura caduca. Un sensor que deja de entregar valores validos debe
    verse como un sensor sin datos y no como un sensor con datos viejos: el
    10 de noviembre el portal mostro durante una hora una temperatura de
    hace una hora, porque el sensor habia empezado a entregar 84,4 grados
    —fuera de rango— y el ultimo valor aceptado se quedo fijo.
    """

    def __init__(self, period_s: float = 2.5, validez_s: Optional[float] = None):
        self._dev = self._find_device()
        self._period = period_s
        self._validez = validez_s if validez_s else period_s * PERIODOS_DE_VALIDEZ
        self._last: Optional[TempHumReading] = None
        self._lock = threading.Lock()

        # Dos contadores y no uno: "no pude leer" y "lei algo imposible" son
        # diagnosticos opuestos. El primero apunta al cableado o al tiempo de
        # muestreo; el segundo, al sensor mismo. Mezclados en un solo
        # contador, el defecto del 10 de noviembre fue invisible por horas.
        self.fallos_io = 0
        self.fuera_de_rango = 0
        self.ultimo_rechazo: Optional[TempHumReading] = None

        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="dht22", daemon=True)
        self._thread.start()

    @property
    def errors(self) -> int:
        """Total de fallas, para el codigo que ya consultaba este atributo."""
        return self.fallos_io + self.fuera_de_rango
'''

# --------------------------------------------------------------------------
# hal_rpi.py — bucle de muestreo y publicacion
# --------------------------------------------------------------------------
LOOP_VIEJO = '''    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                r = self._read_once()
                if -40.0 <= r.temperature_c <= 80.0 and 0.0 <= r.humidity_pct <= 100.0:
                    with self._lock:
                        self._last = r
                else:
                    self.errors += 1
            except OSError:
                self.errors += 1          # EIO/ETIMEDOUT esporadicos son normales en el DHT22
            self._stop.wait(self._period)

    def last_reading(self) -> Optional[TempHumReading]:
        with self._lock:
            return self._last
'''

LOOP_NUEVO = '''    @staticmethod
    def _plausible(r: TempHumReading) -> bool:
        return (TEMP_MIN_C <= r.temperature_c <= TEMP_MAX_C
                and HUM_MIN_PCT <= r.humidity_pct <= HUM_MAX_PCT)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                r = self._read_once()
                if self._plausible(r):
                    with self._lock:
                        self._last = r
                else:
                    # El sensor respondio y la trama paso su suma de
                    # comprobacion, pero el valor es imposible. Se guarda
                    # para poder decir cual es sin ir al sysfs a mano.
                    self.fuera_de_rango += 1
                    self.ultimo_rechazo = r
            except OSError:
                self.fallos_io += 1       # EIO/ETIMEDOUT esporadicos son normales en el DHT22
            self._stop.wait(self._period)

    def last_reading(self) -> Optional[TempHumReading]:
        """Ultima lectura valida, o None si vencio.

        Nunca bloquea. Devolver None cuando la lectura es vieja es lo que
        permite al portal decir "sensor no disponible" en lugar de publicar
        un dato de hace una hora como si fuera actual.
        """
        with self._lock:
            r = self._last
        if r is None:
            return None
        if (time.monotonic() - r.timestamp) > self._validez:
            return None
        return r

    def diagnostico(self) -> dict:
        """Estado del sensor en forma legible, para el registro y la API.

        Distingue los tres estados que importan: sin datos todavia, datos
        vencidos, y datos vigentes. Y dice cual fue el ultimo valor
        rechazado, que es el dato que permite decidir si el problema esta en
        el sensor o en el cableado.
        """
        with self._lock:
            r = self._last
        edad = None if r is None else round(time.monotonic() - r.timestamp, 1)
        if r is None:
            estado = "sin datos"
        elif edad is not None and edad > self._validez:
            estado = "vencido"
        else:
            estado = "vigente"
        d = {
            "estado": estado,
            "edad_s": edad,
            "validez_s": self._validez,
            "fallos_io": self.fallos_io,
            "fuera_de_rango": self.fuera_de_rango,
        }
        if self.ultimo_rechazo is not None:
            d["ultimo_rechazo"] = {
                "temp": self.ultimo_rechazo.temperature_c,
                "hum": self.ultimo_rechazo.humidity_pct,
            }
        return d
'''

# --------------------------------------------------------------------------
# portal.py — muestreador
# --------------------------------------------------------------------------
MUESTRA_VIEJO = '''def guardar_muestra():
    r = SENSOR.last_reading()
    if r is None:
        return
    with db() as con:
'''

MUESTRA_NUEVO = '''# Marca de tiempo de la ultima lectura escrita en la tabla. El muestreador
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
'''

# --------------------------------------------------------------------------
# portal.py — endpoint de ambiente
# --------------------------------------------------------------------------
API_VIEJO = '''    return {
        "actual": None if r is None else {"temp": r.temperature_c, "hum": r.humidity_pct},
        "historial": [dict(f) for f in reversed(filas)],
        "umbral": CFG["umbral_temp_c"],
    }
'''

API_NUEVO = '''    # El diagnostico se expone junto al valor para que un "actual": null no
    # sea indistinguible de un sensor que no existe. La HAL falsa no lo
    # implementa, de ahi el getattr.
    diag = getattr(SENSOR, "diagnostico", None)
    return {
        "actual": None if r is None else {"temp": r.temperature_c, "hum": r.humidity_pct},
        "historial": [dict(f) for f in reversed(filas)],
        "umbral": CFG["umbral_temp_c"],
        "sensor": diag() if callable(diag) else {"estado": "simulado"},
    }
'''


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

    hal = paquete / "hal_rpi.py"
    por = paquete / "portal.py"
    for f in (hal, por):
        if not f.is_file():
            print(f"no existe: {f}", file=sys.stderr)
            return 1

    s = hal.read_text(encoding="utf-8")
    s = sustituir(s, INIT_VIEJO, INIT_NUEVO,
                  "constructor con caducidad y contadores separados",
                  "PERIODOS_DE_VALIDEZ", hechos, omitidos)
    s = sustituir(s, LOOP_VIEJO, LOOP_NUEVO,
                  "bucle, caducidad de la lectura y diagnostico",
                  "def diagnostico", hechos, omitidos)
    hal.write_text(s, encoding="utf-8")

    s = por.read_text(encoding="utf-8")
    s = sustituir(s, MUESTRA_VIEJO, MUESTRA_NUEVO,
                  "muestreador sin duplicados",
                  "_ULTIMA_GUARDADA", hechos, omitidos)
    s = sustituir(s, API_VIEJO, API_NUEVO,
                  "diagnostico del sensor en /api/ambiente",
                  '"sensor": diag()', hechos, omitidos)
    por.write_text(s, encoding="utf-8")

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
