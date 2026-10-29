"""Contratos de la capa de aplicacion.

La interfaz del agente la definio el Rol A y se conserva tal como la
escribio: el portal depende de la abstraccion y no de la implementacion, de
modo que puede servirse con un agente real o con uno simulado sin cambiar
una linea del transporte ni del frontend.

Lo unico que cambio respecto del original es la ubicacion del modulo, que
paso al paquete jarvis para que la receta lo empaquete junto con el resto
de la aplicacion.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict


class ICoordinatorAgent(ABC):
    @abstractmethod
    def process_command(self, user_role: str, text_command: str) -> Dict[str, Any]:
        """
        Recibe el texto natural, ejecuta el ciclo Percibir -> Decidir -> Actuar -> Evaluar.
        Retorna un diccionario con el resultado de la operacion y el texto de sintesis.
        """
        ...
