from abc import ABC, abstractmethod
from typing import Dict, Any

class ICoordinatorAgent(ABC):
    @abstractmethod
    async def process_command(self, user_role: str, text_command: str) -> Dict[str, Any]:
        """
        Recibe el texto natural, ejecuta el ciclo Percibir -> Decidir -> Actuar -> Evaluar.
        Retorna un diccionario con el resultado de la operación y el texto de síntesis.
        """
        pass
    