"""Prueba de la HAL en la Pi: 10 ciclos de lectura del DHT22 con parpadeo del LED."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis.hal_rpi import Dht22IioSensor, SysfsLed  # noqa: E402

sensor = Dht22IioSensor()
led = SysfsLed()
time.sleep(3)                                  # primera lectura

for i in range(10):
    led.set_state(i % 2 == 0)
    r = sensor.last_reading()
    if r is None:
        print(f"[{i}] sin lectura todavia")
    else:
        edad = time.monotonic() - r.timestamp
        print(f"[{i}] {r.temperature_c:.1f} °C  {r.humidity_pct:.1f} %  (hace {edad:.1f} s)  LED={led.get_state()}")
    time.sleep(2)

led.set_state(False)
sensor.close()
print(f"Lecturas fallidas: {sensor.errors}")
