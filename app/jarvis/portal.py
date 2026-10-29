"""Portal web local del asistente de aula (Rol A).

Sirve la interfaz en la red del equipo y expone un unico endpoint que recibe
la orden del navegador y la entrega al agente. No contiene logica de
decision: transporta la orden e informa el estado del procesamiento.

POR QUE BIBLIOTECA ESTANDAR Y NO UN MARCO DE TRABAJO
----------------------------------------------------
El diseno original usaba FastAPI. Se sustituyo por http.server tras evaluar
el costo sobre esta plataforma:

  - FastAPI incorpora a la imagen starlette, uvicorn y pydantic, y el nucleo
    de pydantic v2 esta compilado en Rust, lo que exige compilacion cruzada
    de un arbol de crates a ARM64.
  - El sistema expone un endpoint, atiende un usuario a la vez y tarda quince
    segundos por orden. Ninguna de las capacidades que justifican un marco de
    trabajo —validacion compleja, documentacion automatica, concurrencia
    alta— se ejerce aqui.

El contrato se conserva intacto: mismo endpoint, mismo cuerpo de peticion,
misma interfaz ICoordinatorAgent. Solo cambia el transporte, de modo que
volver a FastAPI es sustituir este archivo.

CONCURRENCIA
------------
El servidor atiende en hilos. Una orden ocupa el modelo de decision durante
quince segundos, y el servidor de inferencia serializa las consultas de
todos modos: los hilos no aceleran nada, pero evitan que una peticion lenta
impida servir la pagina o un recurso estatico a otro navegador.
"""

from __future__ import annotations

import json
import mimetypes
import os
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

from .interfaces import ICoordinatorAgent

DIRECTORIO = os.path.dirname(os.path.abspath(__file__))
PLANTILLAS = os.path.join(DIRECTORIO, "templates")

PUERTO_POR_DEFECTO = 8000
DIRECCION_POR_DEFECTO = "0.0.0.0"
LIMITE_CUERPO = 8192  # una orden en lenguaje natural no pesa mas que esto


class AgenteSimulado(ICoordinatorAgent):
    """Responde sin consultar modelos. Para probar el transporte y el frontend."""

    def process_command(self, user_role: str, text_command: str) -> Dict[str, Any]:
        return {
            "status": "ok",
            "intent": "SIMULADO",
            "confidence": 1.0,
            "message": f"Orden '{text_command}' simulada para el rol {user_role}.",
        }


class _Handler(BaseHTTPRequestHandler):
    """Maneja la pagina, los estaticos y el endpoint de ordenes."""

    server_version = "JarvisAula/1.0"
    agente: ICoordinatorAgent = AgenteSimulado()

    # -- utilidades ---------------------------------------------------------

    def _responder(self, codigo: int, cuerpo: bytes, tipo: str) -> None:
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _json(self, codigo: int, datos: Dict[str, Any]) -> None:
        self._responder(codigo, json.dumps(datos, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8")

    def _archivo(self, nombre: str) -> None:
        # Se sirve solo desde el directorio de plantillas y sin separadores de
        # ruta: el portal no es un servidor de archivos de proposito general.
        if "/" in nombre or "\\" in nombre or nombre.startswith("."):
            self._json(404, {"detail": "no encontrado"})
            return
        ruta = os.path.join(PLANTILLAS, nombre)
        if not os.path.isfile(ruta):
            self._json(404, {"detail": "no encontrado"})
            return
        tipo = mimetypes.guess_type(ruta)[0] or "application/octet-stream"
        with open(ruta, "rb") as f:
            self._responder(200, f.read(), tipo)

    # -- rutas --------------------------------------------------------------

    def do_GET(self) -> None:
        ruta = self.path.split("?")[0]
        if ruta in ("/", "/index.html"):
            self._archivo("index.html")
        elif ruta == "/api/salud":
            self._json(200, {"status": "ok"})
        else:
            self._archivo(ruta.lstrip("/"))

    def do_POST(self) -> None:
        if self.path.split("?")[0] != "/api/command":
            self._json(404, {"detail": "no encontrado"})
            return

        try:
            largo = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._json(400, {"detail": "longitud invalida"})
            return
        if largo <= 0 or largo > LIMITE_CUERPO:
            self._json(400, {"detail": "cuerpo vacio o demasiado grande"})
            return

        try:
            payload = json.loads(self.rfile.read(largo))
        except json.JSONDecodeError:
            self._json(400, {"detail": "el cuerpo no es JSON valido"})
            return

        comando = (payload.get("command") or "").strip()
        rol = (payload.get("role") or "estudiante").strip()
        if not comando:
            self._json(400, {"detail": "El comando no puede estar vacio."})
            return
        if rol not in ("estudiante", "profesor", "tecnico"):
            rol = "estudiante"

        try:
            resultado = self.agente.process_command(rol, comando)
        except Exception as e:
            self._json(500, {"detail": f"error al procesar la orden: {e}"})
            return
        self._json(200, resultado)

    def log_message(self, formato: str, *args: Any) -> None:
        # El registro por defecto escribe en stderr una linea por peticion,
        # incluidos los estaticos. En la imagen eso va al diario del sistema
        # y a la MicroSD sin aportar nada.
        return


def crear_servidor(agente: ICoordinatorAgent,
                   direccion: str = DIRECCION_POR_DEFECTO,
                   puerto: int = PUERTO_POR_DEFECTO) -> ThreadingHTTPServer:
    """Construye el servidor con el agente ya inyectado."""
    handler = type("_HandlerConAgente", (_Handler,), {"agente": agente})
    socketserver.TCPServer.allow_reuse_address = True
    return ThreadingHTTPServer((direccion, puerto), handler)


def servir(agente: ICoordinatorAgent,
           direccion: str = DIRECCION_POR_DEFECTO,
           puerto: int = PUERTO_POR_DEFECTO) -> None:
    servidor = crear_servidor(agente, direccion, puerto)
    print(f"portal escuchando en http://{direccion}:{puerto}")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.shutdown()
        servidor.server_close()


if __name__ == "__main__":
    servir(AgenteSimulado())
