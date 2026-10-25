"""Implementacion real de la iluminacion por habitaciones (Rol B).

Se agrega a app/jarvis/hal_rpi.py.

Hardware: dos LEDs RGB de anodo o catodo comun conectados por jumpers,
cada uno representando una habitacion.

RESTRICCION DE LA PLATAFORMA
----------------------------
La Raspberry Pi 4 expone dos canales de PWM por hardware. Dos habitaciones
en RGB requieren seis canales. El reparto adoptado es:

    SALA    rojo   -> PWM0 (GPIO12)   intensidad continua
            verde  -> GPIO16          conmutado
            azul   -> GPIO20          conmutado
    CUARTO  rojo   -> PWM1 (GPIO13)   intensidad continua
            verde  -> GPIO21          conmutado
            azul   -> GPIO26          conmutado

Los canales conmutados aceptan solo encendido y apagado, de modo que la
paleta efectiva se reduce a ocho combinaciones por habitacion, moduladas
en intensidad por el canal rojo.

No se usa PWM por software: en espacio de usuario con Python depende del
planificador de Linux y produce parpadeo perceptible bajo carga de CPU,
que es la condicion durante una inferencia. Se prefiere una paleta
reducida y estable a una completa e inestable.

La via de escalamiento es un controlador PCA9685 por I2C (16 canales de
PWM por hardware). El bus ya esta habilitado en el BSP; la incorporacion
seria otra implementacion de RoomLight sin cambios en el resto del sistema.

Requiere en el BSP:
    dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4

Solo biblioteca estandar: sysfs para PWM y para GPIO.
"""
import threading
from pathlib import Path
from typing import Dict, Optional

from .hal_rooms import PALETTE, Color, Room, RoomLight, RoomState

PWM_CHIP = Path("/sys/class/pwm/pwmchip0")
GPIO_BASE = Path("/sys/class/gpio")
PWM_PERIOD_NS = 1_000_000          # 1 kHz: sin parpadeo visible


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
            (self._dir / "enable").write_text("0")
        except OSError:
            pass


class _GpioPin:
    """Pin GPIO conmutado sobre sysfs."""

    def __init__(self, number: int):
        self._dir = GPIO_BASE / f"gpio{number}"
        if not self._dir.exists():
            (GPIO_BASE / "export").write_text(str(number))
        (self._dir / "direction").write_text("out")
        self._value = self._dir / "value"
        self._value.write_text("0")

    def set(self, value: float, threshold: float = 0.35) -> None:
        self._value.write_text("1" if value >= threshold else "0")

    def close(self) -> None:
        try:
            self._value.write_text("0")
        except OSError:
            pass


class PwmRoomLight(RoomLight):
    """Dos habitaciones RGB: canal rojo con intensidad, verde y azul conmutados."""

    # habitacion -> (indice de PWM para rojo, GPIO verde, GPIO azul)
    LAYOUT: Dict[Room, tuple] = {
        Room.SALA:   (0, 16, 20),
        Room.CUARTO: (1, 21, 26),
    }

    def __init__(self, active_low: bool = False):
        """active_low=True para LEDs de anodo comun (el pin drena corriente)."""
        self._invert = active_low
        self._lock = threading.Lock()
        self._channels: Dict[Room, tuple] = {}
        self._state: Dict[Room, RoomState] = {}

        for room, (pwm_idx, gpio_g, gpio_b) in self.LAYOUT.items():
            self._channels[room] = (
                _PwmChannel(pwm_idx),
                _GpioPin(gpio_g),
                _GpioPin(gpio_b),
            )
            self._state[room] = RoomState(PALETTE["apagado"], 0.0)

        self.all_off()

    def available(self) -> frozenset:
        return frozenset(self._channels)

    def set(self, room: Room, color: Color, brightness: float = 1.0) -> None:
        if room not in self._channels:
            raise ValueError(
                f"habitacion no disponible: {room.value}. "
                f"Disponibles: {', '.join(r.value for r in sorted(self._channels))}"
            )
        brightness = max(0.0, min(1.0, brightness))
        efectivo = color.scaled(brightness)
        red, green, blue = self._channels[room]

        r = 1.0 - efectivo.r if self._invert else efectivo.r
        g = 1.0 - efectivo.g if self._invert else efectivo.g
        b = 1.0 - efectivo.b if self._invert else efectivo.b

        with self._lock:
            red.set(r)
            green.set(g)
            blue.set(b)
            self._state[room] = RoomState(color, brightness)

    def get(self, room: Room) -> Optional[RoomState]:
        with self._lock:
            return self._state.get(room)

    def close(self) -> None:
        self.all_off()
        for red, green, blue in self._channels.values():
            red.close()
            green.close()
            blue.close()
