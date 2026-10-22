"""Contrato de la capa de abstraccion de hardware (HAL) de Jarvis.

La aplicacion (Rol A) depende solo de estas interfaces. Las implementaciones
reales (Rol B) y las falsas para pruebas viven aparte y se inyectan.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TempHumReading:
    temperature_c: float
    humidity_pct: float
    timestamp: float          # time.monotonic() de la ultima lectura valida


class TempHumSensor(ABC):
    """Sensor de temperatura y humedad (DHT22 via IIO en la implementacion real)."""

    @abstractmethod
    def last_reading(self) -> Optional[TempHumReading]:
        """Ultima lectura valida en cache. Nunca bloquea; None si aun no hay datos."""


class Actuator(ABC):
    """Actuador binario (LED en la implementacion real)."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def set_state(self, on: bool) -> None: ...

    @abstractmethod
    def get_state(self) -> bool: ...
