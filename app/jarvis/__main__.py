"""Punto de entrada del asistente de aula.

Es lo que arranca la unidad de servicio en la imagen:

    ExecStart=/usr/bin/python3 -m jarvis

Construye las implementaciones concretas de la capa de abstraccion de
hardware, el almacen y el agente, y levanta el portal. Es el unico modulo
que decide que implementacion usar: todo lo demas opera contra las
interfaces abstractas, que es lo que permite desarrollar y probar sin la
Raspberry Pi.

SELECCION DE LA IMPLEMENTACION
------------------------------
Por defecto se detecta el hardware: si el nucleo expone el subsistema de
modulacion por ancho de pulso y el dispositivo de caracteres de GPIO, se usa
la implementacion real; si no, la simulada. Asi el mismo comando funciona en
la Pi y en una laptop de desarrollo sin configuracion adicional.

La deteccion se puede forzar con la variable de entorno JARVIS_HAL, que
acepta 'real' o 'simulada'. Util para probar el portal sobre la Pi antes de
cablear los periferico, o para diagnosticar si un fallo viene del hardware
o de la aplicacion.
"""

from __future__ import annotations

import os
import sys

from . import inferencia
from .agente import Agente
from .interfaces import ICoordinatorAgent
from .persistencia import RUTA_POR_DEFECTO, Almacen
from .portal import DIRECCION_POR_DEFECTO, PUERTO_POR_DEFECTO, servir

RUTA_PWM = "/sys/class/pwm"
RUTA_GPIO = "/dev/gpiochip0"
RUTA_IIO = "/sys/bus/iio/devices"


def hay_hardware() -> bool:
    """Si el nucleo expone lo que la implementacion real necesita."""
    return (os.path.isdir(RUTA_PWM)
            and bool(os.listdir(RUTA_PWM))
            and os.path.exists(RUTA_GPIO))


def construir_hal():
    """Devuelve (luces, sensor) segun el entorno."""
    modo = os.environ.get("JARVIS_HAL", "").strip().lower()
    if modo not in ("real", "simulada", ""):
        print(f"JARVIS_HAL={modo!r} no es valido; se detecta automaticamente",
              file=sys.stderr)
        modo = ""
    if modo == "":
        modo = "real" if hay_hardware() else "simulada"

    if modo == "real":
        from .hal_rpi import Dht22IioSensor
        from .hal_rpi_rooms import PwmRoomLight
        print("HAL real: PWM, GPIO y sensor del nucleo")
        return PwmRoomLight(), Dht22IioSensor()

    from .hal_fake import FakeRoomLight, FakeTempHumSensor
    if not hay_hardware():
        print("HAL simulada: el nucleo no expone PWM o GPIO "
              f"(revisar que {RUTA_PWM} tenga canales y que existan los overlays)")
    else:
        print("HAL simulada por JARVIS_HAL")
    return FakeRoomLight(), FakeTempHumSensor()


class AgenteCoordinador(ICoordinatorAgent):
    """Adapta el agente al contrato que consume el portal."""

    def __init__(self, agente: Agente):
        self._agente = agente

    def process_command(self, user_role: str, text_command: str):
        return self._agente.procesar(user_role, text_command).dict()


def main() -> int:
    direccion = os.environ.get("JARVIS_HOST", DIRECCION_POR_DEFECTO)
    puerto = int(os.environ.get("JARVIS_PORT", PUERTO_POR_DEFECTO))
    ruta_db = os.environ.get("JARVIS_DB", RUTA_POR_DEFECTO)

    # El servidor de inferencia no es opcional, pero tampoco motivo para no
    # arrancar: el servicio de calentamiento puede estar todavia cargando los
    # modelos. Se avisa y se sigue; cada orden reporta el error si persiste.
    if not inferencia.disponible():
        print("aviso: el servidor de inferencia no responde todavia",
              file=sys.stderr)
    else:
        residentes = inferencia.modelos_residentes()
        print("modelos residentes:", ", ".join(residentes) or "ninguno")
        if len(residentes) < 2:
            print("aviso: se esperaban dos modelos residentes; una orden "
                  "podria pagar la carga desde la MicroSD (R1)", file=sys.stderr)

    luces, sensor = construir_hal()
    almacen = Almacen(ruta_db)
    print("almacen en", ruta_db)

    agente = AgenteCoordinador(Agente(luces, sensor, almacen))
    servir(agente, direccion, puerto)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
