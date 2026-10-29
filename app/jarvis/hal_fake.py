"""Implementaciones falsas de la HAL para desarrollar sin hardware (Rol B → Rol A).

Permiten al Rol A escribir y probar los handlers de los dominios CASA y
AMBIENTE sin Raspberry Pi, sin sensor y sin LEDs. La aplicacion no distingue
estas implementaciones de las reales: dependen del mismo contrato.

Uso:
    from jarvis.hal_fake import FakeRoomLight, FakeTempHumSensor

    luces = FakeRoomLight()
    luces.turn_on(Room.FRENTE)
    luces.apply_scene("cine")

Para pruebas automaticas, FakeRoomLight.history guarda cada cambio.
"""
import math
import time
from typing import Dict, List, Optional, Tuple

from .hal import TempHumReading, TempHumSensor
from .hal_rooms import PALETTE, Color, Room, RoomLight, RoomState


class FakeRoomLight(RoomLight):
    """Iluminacion simulada: imprime el estado y lo registra.

    Por defecto expone las dos zonas de la demostracion. Para probar
    el diseno objetivo de cuatro:  FakeRoomLight(rooms=set(Room))
    """

    def __init__(self, rooms: Optional[set] = None, verbose: bool = True):
        self._rooms = frozenset(rooms or {Room.FRENTE, Room.FONDO})
        self._state: Dict[Room, RoomState] = {
            r: RoomState(PALETTE["apagado"], 0.0) for r in self._rooms
        }
        self._verbose = verbose
        self.history: List[Tuple[float, Room, RoomState]] = []

    def available(self) -> frozenset:
        return self._rooms

    def set(self, room: Room, color: Color, brightness: float = 1.0) -> None:
        if room not in self._rooms:
            raise ValueError(
                f"zona no disponible: {room.value}. "
                f"Disponibles: {', '.join(r.value for r in sorted(self._rooms))}"
            )
        brightness = max(0.0, min(1.0, brightness))
        st = RoomState(color, brightness)
        self._state[room] = st
        self.history.append((time.monotonic(), room, st))
        if self._verbose:
            print(f"  [luz] {room.value:8s} {self._render(st)}")

    def get(self, room: Room) -> Optional[RoomState]:
        return self._state.get(room)

    @staticmethod
    def _render(st: RoomState) -> str:
        if not st.is_on:
            return "apagada"
        efectivo = st.color.scaled(st.brightness)
        nombre = next(
            (n for n, c in PALETTE.items()
             if abs(c.r - st.color.r) < 0.01
             and abs(c.g - st.color.g) < 0.01
             and abs(c.b - st.color.b) < 0.01),
            "personalizado",
        )
        barra = "#" * int(st.brightness * 10)
        return (f"{nombre:12s} {int(st.brightness * 100):3d}% |{barra:<10s}| "
                f"rgb({efectivo.r:.2f}, {efectivo.g:.2f}, {efectivo.b:.2f})")

    def reset_history(self) -> None:
        self.history.clear()


class FakeTempHumSensor(TempHumSensor):
    """Sensor simulado con una oscilacion suave alrededor de un valor base.

    Reproduce el comportamiento del real: nunca bloquea y puede devolver
    None antes de la primera lectura.
    """

    def __init__(
        self,
        base_temp_c: float = 24.0,
        base_hum_pct: float = 65.0,
        amplitude: float = 2.0,
        period_s: float = 60.0,
        warmup_s: float = 0.0,
    ):
        self._base_t = base_temp_c
        self._base_h = base_hum_pct
        self._amp = amplitude
        self._period = period_s
        self._t0 = time.monotonic()
        self._warmup = warmup_s
        self.errors = 0

    def last_reading(self) -> Optional[TempHumReading]:
        now = time.monotonic()
        if now - self._t0 < self._warmup:
            return None                     # como el real antes del primer muestreo
        fase = 2 * math.pi * (now - self._t0) / self._period
        return TempHumReading(
            temperature_c=round(self._base_t + self._amp * math.sin(fase), 1),
            humidity_pct=round(self._base_h + self._amp * math.cos(fase), 1),
            timestamp=now,
        )

    def set_base(self, temp_c: float, hum_pct: float) -> None:
        """Fuerza valores concretos: util para probar reglas por umbral."""
        self._base_t = temp_c
        self._base_h = hum_pct

    def close(self) -> None:
        pass


if __name__ == "__main__":
    print("Demostracion de la HAL falsa\n")

    luces = FakeRoomLight()
    print("Encender la sala:")
    luces.turn_on(Room.FRENTE)

    print("\nPoner el cuarto en azul al 40%:")
    luces.set(Room.FONDO, PALETTE["azul"], 0.4)

    print("\nAtenuar la sala:")
    luces.dim(Room.FRENTE)

    print("\nEscena 'cine':")
    luces.apply_scene("cine")

    print("\nEscena 'dormir':")
    luces.apply_scene("dormir")

    print("\nApagar todo:")
    luces.all_off()

    print(f"\nCambios registrados: {len(luces.history)}")

    print("\nSensor simulado:")
    sensor = FakeTempHumSensor()
    r = sensor.last_reading()
    print(f"  {r.temperature_c} C, {r.humidity_pct} %")

    print("\nForzar 29 C para probar una regla por umbral:")
    sensor.set_base(29.0, 70.0)
    r = sensor.last_reading()
    print(f"  {r.temperature_c} C, {r.humidity_pct} %")
