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
import threading

from . import inferencia
from .agente import Agente
from .hal_rooms import PALETTE, Color, Room, RoomLight
from .interfaces import ICoordinatorAgent
from .persistencia import CADENCIA_AMBIENTE, RUTA_POR_DEFECTO, Almacen
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


def arrancar_muestreo(sensor, almacen: Almacen) -> threading.Event:
    """Persiste una lectura ambiental con la cadencia del almacen.

    El sensor se muestrea solo: su implementacion real mantiene un hilo que
    refresca la lectura en cache cada 2,5 s. Lo que falta es guardarla, y sin
    eso el historico queda vacio y el resumen generativo no tiene de que
    hablar: registrar_ambiente solo se invocaba cuando alguien preguntaba la
    temperatura.

    El hilo es demonio y despierta cada treinta segundos en lugar de dormir
    los cinco minutos completos, para que el apagado no espere un ciclo
    entero. La cadencia real la impone el almacen, que descarta las escrituras
    prematuras.
    """
    alto = threading.Event()

    def bucle() -> None:
        while not alto.is_set():
            try:
                lectura = sensor.last_reading()
                if lectura is not None:
                    almacen.registrar_ambiente(lectura.temperature_c,
                                               lectura.humidity_pct)
            except Exception as e:
                # Un fallo de sensor o de escritura no debe tumbar el servicio:
                # la operacion AMBIENTE reportara el problema cuando se use.
                print(f"aviso: fallo el muestreo ambiental: {e}", file=sys.stderr)
            alto.wait(30.0)

    threading.Thread(target=bucle, name="muestreo-ambiente", daemon=True).start()
    print(f"muestreo ambiental cada {CADENCIA_AMBIENTE:.0f} s")
    return alto


def restaurar_iluminacion(luces: RoomLight, almacen: Almacen) -> None:
    """Reaplica el estado de iluminacion guardado.

    Tras un reinicio los canales arrancan apagados, mientras el almacen
    conserva el ultimo estado conocido. Sin esta restauracion el sistema
    afirma una cosa y el aula muestra otra, y la discrepancia aparece
    justamente al reconectar el equipo, que es lo que ocurre durante la
    demostracion de la regla R2.
    """
    guardado = almacen.estado_zonas()
    if not guardado:
        return
    disponibles = {r.name: r for r in luces.available()}
    restauradas = []
    for nombre, estado in guardado.items():
        zona = disponibles.get(nombre)
        if zona is None:
            continue  # zona del diseno objetivo que esta instalacion no controla
        color = PALETTE.get(estado["color"])
        if color is None:
            continue
        brillo = float(estado["brillo"])
        try:
            luces.set(zona, color, brillo)
        except Exception as e:
            print(f"aviso: no se pudo restaurar {nombre}: {e}", file=sys.stderr)
            continue
        if brillo > 0.0:
            restauradas.append(f"{zona.value} {estado['color']} {brillo:.0%}")
    if restauradas:
        print("iluminacion restaurada:", ", ".join(restauradas))


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

    restaurar_iluminacion(luces, almacen)
    alto_muestreo = arrancar_muestreo(sensor, almacen)

    agente = AgenteCoordinador(Agente(luces, sensor, almacen))
    try:
        servir(agente, direccion, puerto)
    finally:
        alto_muestreo.set()
        for recurso in (sensor, luces):
            cerrar = getattr(recurso, "close", None)
            if cerrar is not None:
                try:
                    cerrar()
                except Exception:
                    pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
