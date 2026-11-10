"""Implementacion real de la HAL para Raspberry Pi 4 (Rol B).

- DHT22: driver del kernel (dtoverlay=dht11), expuesto por IIO en /sys/bus/iio.
- LED:   driver gpio-led del kernel (dtoverlay=gpio-led), en /sys/class/leds.
Solo usa la biblioteca estandar de Python: no requiere paquetes extra en la imagen.
"""
import glob
import threading
import time
from pathlib import Path
from typing import Optional

from .hal import Actuator, TempHumReading, TempHumSensor


# Rango fisicamente posible en un aula. Una lectura fuera de el no se
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

    @staticmethod
    def _find_device() -> Path:
        for d in glob.glob("/sys/bus/iio/devices/iio:device*"):
            p = Path(d)
            if (p / "in_temp_input").exists() and (p / "in_humidityrelative_input").exists():
                return p
        raise RuntimeError("DHT22 no encontrado en IIO: revise cableado y dtoverlay=dht11")

    def _read_once(self) -> TempHumReading:
        t = int((self._dev / "in_temp_input").read_text()) / 1000.0
        h = int((self._dev / "in_humidityrelative_input").read_text()) / 1000.0
        return TempHumReading(t, h, time.monotonic())

    @staticmethod
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

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


class SysfsLed(Actuator):
    """LED controlado por el driver gpio-led del kernel."""

    def __init__(self, label: str = "jarvis-led", name: str = "luz"):
        self._path = Path("/sys/class/leds") / label / "brightness"
        if not self._path.exists():
            raise RuntimeError(f"{self._path} no existe: revise dtoverlay=gpio-led")
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def set_state(self, on: bool) -> None:
        self._path.write_text("1" if on else "0")

    def get_state(self) -> bool:
        return int(self._path.read_text().strip()) > 0
