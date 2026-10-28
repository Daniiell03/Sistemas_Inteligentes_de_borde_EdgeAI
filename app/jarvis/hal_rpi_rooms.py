"""Implementacion real de la iluminacion por zonas (Rol B).

Se instala en app/jarvis/hal_rpi_rooms.py.

Hardware: dos LEDs RGB de anodo o catodo comun conectados por jumpers a
una protoboard, cada uno representando una zona.

REPARTO DE CANALES
------------------
La Raspberry Pi 4 expone dos canales de PWM por hardware. Dos zonas
en RGB requieren seis canales:

    FRENTE  rojo   -> PWM0 (GPIO12)   intensidad continua
            verde  -> GPIO16          conmutado
            azul   -> GPIO20          conmutado
    FONDO   rojo   -> PWM1 (GPIO13)   intensidad continua
            verde  -> GPIO21          conmutado
            azul   -> GPIO26          conmutado

Los canales conmutados aceptan solo encendido y apagado, de modo que la
paleta efectiva se reduce a ocho combinaciones por zona, moduladas
en intensidad por el canal rojo.

No se usa PWM por software en espacio de usuario: depende del planificador
de Linux y produce parpadeo perceptible bajo carga de CPU, que es la
condicion durante una inferencia. Se prefiere una paleta reducida y
estable a una completa e inestable.

ACCESO AL HARDWARE
------------------
PWM: subsistema PWM del kernel por sysfs (/sys/class/pwm/), que es la
interfaz vigente.

GPIO: interfaz de caracteres del kernel (/dev/gpiochip0) mediante ioctl.
NO se usa /sys/class/gpio: esa interfaz esta obsoleta desde Linux 4.8 y
puede no estar compilada en el kernel 6.18 de la imagen. El acceso por
chardev es el que usa libgpiod; aqui se implementa con el modulo fcntl de
la biblioteca estandar para no agregar dependencias a la imagen, ya que la
receta de libgpiod de meta-oe no provee bindings de Python.

La via de escalamiento es un controlador PCA9685 por I2C (16 canales de
PWM por hardware). El bus ya esta habilitado en el BSP; incorporarlo seria
otra implementacion de RoomLight sin cambios en el resto del sistema.

Requiere en el BSP:
    dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4
"""
import fcntl
import os
import struct
import threading
from pathlib import Path
from typing import Dict, Optional

from .hal_rooms import PALETTE, Color, Room, RoomLight, RoomState

PWM_CHIP = Path("/sys/class/pwm/pwmchip0")
GPIO_CHIP = "/dev/gpiochip0"
PWM_PERIOD_NS = 1_000_000          # 1 kHz: sin parpadeo visible


# --------------------------------------------------------------------------
# GPIO por interfaz de caracteres (ABI v2 del kernel, Linux >= 5.10)
# --------------------------------------------------------------------------
# struct gpio_v2_line_request  = 592 bytes
# struct gpio_v2_line_values   =  16 bytes
# Verificado contra include/uapi/linux/gpio.h

_LINE_REQUEST_FMT = "<64I32s" + "QI5I" + "IIQQ" * 10 + "II5Ii"
_LINE_VALUES_FMT = "<QQ"

_GPIO_V2_GET_LINE_IOCTL = 0xC250B407        # _IOWR(0xB4, 0x07, line_request)
_GPIO_V2_LINE_SET_VALUES_IOCTL = 0xC010B40F  # _IOWR(0xB4, 0x0F, line_values)

_GPIO_V2_LINE_FLAG_OUTPUT = 1 << 1


class _GpioLines:
    """Conjunto de lineas GPIO de salida sobre /dev/gpiochipN.

    Una sola solicitud al kernel reserva todas las lineas; los cambios de
    valor posteriores son un ioctl sobre el descriptor devuelto, sin abrir
    ni cerrar archivos en cada operacion.
    """

    def __init__(self, offsets, consumer: str = "jarvis", chip: str = GPIO_CHIP):
        if not os.path.exists(chip):
            raise RuntimeError(
                f"{chip} no existe: el kernel no expone la interfaz de "
                f"caracteres de GPIO"
            )
        self._offsets = list(offsets)
        self._index = {off: i for i, off in enumerate(self._offsets)}
        n = len(self._offsets)
        if not 1 <= n <= 64:
            raise ValueError(f"numero de lineas fuera de rango: {n}")

        # struct gpio_v2_line_request
        campos = []
        campos += self._offsets + [0] * (64 - n)        # offsets[64]
        campos.append(consumer.encode()[:31])            # consumer[32]
        campos.append(_GPIO_V2_LINE_FLAG_OUTPUT)         # config.flags
        campos.append(0)                                 # config.num_attrs
        campos += [0] * 5                                # config.padding[5]
        campos += [0, 0, 0, 0] * 10                      # config.attrs[10]
        campos.append(n)                                 # num_lines
        campos.append(0)                                 # event_buffer_size
        campos += [0] * 5                                # padding[5]
        campos.append(0)                                 # fd (lo llena el kernel)

        peticion = struct.pack(_LINE_REQUEST_FMT, *campos)

        self._chip_fd = os.open(chip, os.O_RDWR)
        try:
            resultado = fcntl.ioctl(self._chip_fd, _GPIO_V2_GET_LINE_IOCTL, peticion)
        except OSError as e:
            os.close(self._chip_fd)
            raise RuntimeError(
                f"no se pudieron reservar las lineas {self._offsets} en {chip}: {e}. "
                f"Verifique que ningun otro proceso las tenga tomadas "
                f"(gpioinfo lo muestra) y que no choquen con un dtoverlay."
            ) from e

        self._line_fd = struct.unpack(_LINE_REQUEST_FMT, resultado)[-1]
        if self._line_fd < 0:
            os.close(self._chip_fd)
            raise RuntimeError(f"el kernel devolvio un descriptor invalido: {self._line_fd}")

        self._bits = 0
        self._write(0)

    def _write(self, bits: int) -> None:
        mascara = (1 << len(self._offsets)) - 1
        fcntl.ioctl(
            self._line_fd,
            _GPIO_V2_LINE_SET_VALUES_IOCTL,
            struct.pack(_LINE_VALUES_FMT, bits, mascara),
        )
        self._bits = bits

    def set(self, offset: int, on: bool) -> None:
        bit = 1 << self._index[offset]
        self._write(self._bits | bit if on else self._bits & ~bit)

    def get(self, offset: int) -> bool:
        return bool(self._bits & (1 << self._index[offset]))

    def close(self) -> None:
        try:
            self._write(0)
        except OSError:
            pass
        for fd in (self._line_fd, self._chip_fd):
            try:
                os.close(fd)
            except OSError:
                pass


class _PwmChannel:
    """Canal de PWM por hardware sobre sysfs."""

    def __init__(self, index: int):
        self._dir = PWM_CHIP / f"pwm{index}"
        if not self._dir.exists():
            export = PWM_CHIP / "export"
            if not export.exists():
                raise RuntimeError(
                    f"{PWM_CHIP} no existe: revise dtoverlay=pwm-2chan en config.txt"
                )
            export.write_text(str(index))
        (self._dir / "period").write_text(str(PWM_PERIOD_NS))
        (self._dir / "duty_cycle").write_text("0")
        (self._dir / "enable").write_text("1")

    def set(self, value: float) -> None:
        duty = int(max(0.0, min(1.0, value)) * PWM_PERIOD_NS)
        (self._dir / "duty_cycle").write_text(str(duty))

    def close(self) -> None:
        try:
            (self._dir / "duty_cycle").write_text("0")
            (self._dir / "enable").write_text("0")
        except OSError:
            pass


class PwmRoomLight(RoomLight):
    """Dos zonas RGB: canal rojo con intensidad, verde y azul conmutados."""

    # zona -> (indice de PWM para rojo, GPIO verde, GPIO azul)
    LAYOUT: Dict[Room, tuple] = {
        Room.FRENTE:   (0, 16, 20),
        Room.FONDO: (1, 21, 26),
    }

    # Umbral sobre el que un canal conmutado se considera encendido
    UMBRAL = 0.35

    def __init__(self, active_low: bool = False):
        """active_low=True para LEDs de anodo comun (el pin drena corriente)."""
        self._invert = active_low
        self._lock = threading.Lock()
        self._state: Dict[Room, RoomState] = {}
        self._pwm: Dict[Room, _PwmChannel] = {}
        self._pines: Dict[Room, tuple] = {}

        offsets = [g for _, gv, ga in self.LAYOUT.values() for g in (gv, ga)]
        self._gpio = _GpioLines(offsets, consumer="jarvis-luces")

        try:
            for room, (pwm_idx, gpio_g, gpio_b) in self.LAYOUT.items():
                self._pwm[room] = _PwmChannel(pwm_idx)
                self._pines[room] = (gpio_g, gpio_b)
                self._state[room] = RoomState(PALETTE["apagado"], 0.0)
        except Exception:
            self._gpio.close()
            for canal in self._pwm.values():
                canal.close()
            raise

        self.all_off()

    def available(self) -> frozenset:
        return frozenset(self.LAYOUT)

    def set(self, room: Room, color: Color, brightness: float = 1.0) -> None:
        if room not in self.LAYOUT:
            raise ValueError(
                f"zona no disponible: {room.value}. "
                f"Disponibles: {', '.join(r.value for r in sorted(self.LAYOUT))}"
            )
        brightness = max(0.0, min(1.0, brightness))
        efectivo = color.scaled(brightness)

        r = 1.0 - efectivo.r if self._invert else efectivo.r
        g_on = (efectivo.g >= self.UMBRAL) != self._invert
        b_on = (efectivo.b >= self.UMBRAL) != self._invert

        gpio_g, gpio_b = self._pines[room]
        with self._lock:
            self._pwm[room].set(r)
            self._gpio.set(gpio_g, g_on)
            self._gpio.set(gpio_b, b_on)
            self._state[room] = RoomState(color, brightness)

    def get(self, room: Room) -> Optional[RoomState]:
        with self._lock:
            return self._state.get(room)

    def close(self) -> None:
        try:
            self.all_off()
        except OSError:
            pass
        for canal in self._pwm.values():
            canal.close()
        self._gpio.close()
