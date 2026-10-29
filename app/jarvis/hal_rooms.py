"""Extension del contrato de la HAL: iluminacion por zonas (Rol B).

Este modulo se agrega a app/jarvis/hal.py. Define la interfaz que la
aplicacion (Rol A) consume para controlar la iluminacion, sin conocer
como esta implementada.

Contrato I-4 de la seccion 6 de la propuesta de diseno.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional


class Room(str, Enum):
    """Zonas de iluminacion del aula.

    La demostracion implementa FRENTE (zona de proyeccion) y FONDO (zona
    de trabajo); LATERAL y ENTRADA estan declaradas porque el diseno
    objetivo contempla cuatro zonas, limitado a dos por los canales de
    PWM por hardware disponibles (RH-05).
    Una implementacion puede no soportarlas: ver RoomLight.available().
    """
    FRENTE = "frente"
    FONDO = "fondo"
    LATERAL = "lateral"
    ENTRADA = "entrada"


@dataclass(frozen=True)
class Color:
    """Color RGB con componentes de 0.0 a 1.0."""
    r: float
    g: float
    b: float

    def __post_init__(self):
        for c in (self.r, self.g, self.b):
            if not 0.0 <= c <= 1.0:
                raise ValueError(f"componente fuera de rango [0,1]: {c}")

    def scaled(self, brightness: float) -> "Color":
        """Mismo color con el brillo aplicado."""
        b = max(0.0, min(1.0, brightness))
        return Color(self.r * b, self.g * b, self.b * b)


# Paleta nombrada: lo que el router puede devolver como destino de color.
PALETTE: Dict[str, Color] = {
    "apagado":  Color(0.0, 0.0, 0.0),
    "blanco":   Color(1.0, 1.0, 1.0),
    "calido":   Color(1.0, 0.6, 0.2),
    "frio":     Color(0.8, 0.9, 1.0),
    "rojo":     Color(1.0, 0.0, 0.0),
    "verde":    Color(0.0, 1.0, 0.0),
    "azul":     Color(0.0, 0.0, 1.0),
    "amarillo": Color(1.0, 1.0, 0.0),
    "morado":   Color(0.7, 0.0, 1.0),
    "ambar":    Color(1.0, 0.4, 0.0),
}


@dataclass(frozen=True)
class RoomState:
    """Estado de una zona."""
    color: Color
    brightness: float          # 0.0 a 1.0

    @property
    def is_on(self) -> bool:
        return self.brightness > 0.0 and (self.color.r + self.color.g + self.color.b) > 0.0


# Escenas del aula: estado objetivo por zona. Una zona ausente se apaga.
#
# Las escenas usan blanco con intensidad variable en lugar de color pleno:
# solo el canal rojo admite intensidad continua (PWM por hardware), mientras
# que verde y azul son conmutados, de modo que una escena con color a baja
# intensidad degenera en rojo. Ajustar con los LEDs a la vista.
SCENES: Dict[str, Dict[Room, RoomState]] = {
    # Pantalla visible al frente, luz suficiente al fondo para tomar notas.
    "proyeccion": {
        Room.FONDO: RoomState(PALETTE["blanco"], 0.4),
    },
    # Lectura individual: ambas zonas al maximo.
    "lectura": {
        Room.FRENTE: RoomState(PALETTE["blanco"], 1.0),
        Room.FONDO: RoomState(PALETTE["blanco"], 1.0),
    },
    # Trabajo en grupo: iluminacion pareja, intensidad media.
    "grupos": {
        Room.FRENTE: RoomState(PALETTE["blanco"], 0.6),
        Room.FONDO: RoomState(PALETTE["blanco"], 0.6),
    },
    # Examen: maxima iluminacion uniforme, sin distracciones de color.
    "examen": {
        Room.FRENTE: RoomState(PALETTE["blanco"], 1.0),
        Room.FONDO: RoomState(PALETTE["blanco"], 1.0),
    },
    # Fin de sesion: todo apagado.
    "salida": {},
}


class RoomLight(ABC):
    """Iluminacion por zona.

    La implementacion real usa PWM sobre sysfs; la falsa imprime el estado.
    La aplicacion no distingue entre ambas.
    """

    @abstractmethod
    def available(self) -> frozenset:
        """Habitaciones que esta implementacion controla realmente."""

    @abstractmethod
    def set(self, room: Room, color: Color, brightness: float = 1.0) -> None:
        """Fija color y brillo de una zona.

        Lanza ValueError si la zona no esta en available().
        """

    @abstractmethod
    def get(self, room: Room) -> Optional[RoomState]:
        """Estado actual, o None si la zona no esta disponible."""

    def turn_on(self, room: Room, color: Optional[Color] = None) -> None:
        self.set(room, color or PALETTE["calido"], 1.0)

    def turn_off(self, room: Room) -> None:
        self.set(room, PALETTE["apagado"], 0.0)

    def dim(self, room: Room, brightness: float = 0.2) -> None:
        """Atenua conservando el color actual."""
        st = self.get(room)
        color = st.color if st and st.is_on else PALETTE["calido"]
        self.set(room, color, brightness)

    def all_off(self) -> None:
        for room in self.available():
            self.turn_off(room)

    def apply_scene(self, scene: str) -> None:
        """Aplica una escena. Las zonas no incluidas se apagan."""
        if scene not in SCENES:
            raise ValueError(f"escena desconocida: {scene}")
        target = SCENES[scene]
        for room in self.available():
            st = target.get(room)
            if st is None:
                self.turn_off(room)
            else:
                self.set(room, st.color, st.brightness)

    def snapshot(self) -> Dict[Room, RoomState]:
        """Estado de todas las zonas disponibles."""
        return {r: self.get(r) for r in self.available() if self.get(r) is not None}
