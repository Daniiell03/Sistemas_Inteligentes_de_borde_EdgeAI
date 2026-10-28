import os
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# Importamos la abstracción, no la implementación concreta
from app.core.interfaces import ICoordinatorAgent

app = FastAPI(title="Jarvis Aula Edge Portal")

# Configuración de plantillas HTML
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Modelo de datos esperado desde el Frontend
class CommandPayload(BaseModel):
    role: str  # 'estudiante' o 'profesor'
    command: str

# Dependencia: Simulamos la inyección del agente (MOCK para desarrollo)
# En producción, esto se reemplaza por la instancia real del Agente Coordinador
class MockAgent(ICoordinatorAgent):
    async def process_command(self, user_role: str, text_command: str) -> dict:
        return {
            "status": "success",
            "intent": "SIMULATED",
            "message": f"Comando '{text_command}' simulado para rol {user_role}."
        }

def get_agent() -> ICoordinatorAgent:
    return MockAgent()

@app.get("/", response_class=HTMLResponse)
async def render_portal(request: Request):
    """Renderiza la interfaz web única (SPA) para estudiantes y profesores."""
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/command")
async def handle_command(payload: CommandPayload, agent: ICoordinatorAgent = Depends(get_agent)):
    """Endpoint REST que recibe el texto del navegador y lo pasa al Agente."""
    if not payload.command:
        raise HTTPException(status_code=400, detail="El comando no puede estar vacío.")
    
    # Delegamos el ciclo cognitivo al agente
    result = await agent.process_command(payload.role, payload.command)
    return result