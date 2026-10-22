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


class Dht22IioSensor(TempHumSensor):
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

    def _loop(self) -> None:
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
