"""Punto de entrada del asistente de aula.

Es lo que arranca la unidad de servicio en la imagen:

    ExecStart=/usr/bin/python3 -m jarvis

Levanta el portal, que al importarse construye la capa de abstraccion de
hardware, abre el almacen y registra sus rutas. La deteccion de hardware, el
muestreo del sensor y la restauracion del estado de iluminacion ocurren
dentro del portal, de modo que este modulo solo resuelve parametros de red y
arranca el servidor.

PARAMETROS
----------
Por variables de entorno, que la unidad de servicio fija:

    JARVIS_HOST     direccion de escucha        0.0.0.0
    JARVIS_PORT     puerto                      8000
    JARVIS_DATA     datos y archivos subidos    /var/lib/jarvis
    JARVIS_DB       base de datos               <JARVIS_DATA>/jarvis.db
    JARVIS_CONFIG   configuracion del portal    /etc/jarvis/portal_config.json

Se prefieren variables de entorno a un archivo de configuracion propio: son
cinco valores, la unidad de servicio ya las declara, y leer YAML exigiria
python3-pyyaml en la imagen sin ninguna ganancia. Los parametros de
operacion del portal —PIN, umbrales, limites de subida— si viven en un
archivo, porque los cambia el usuario y no el integrador.

UN SOLO PROCESO
---------------
El servidor corre con un unico trabajador. Varios procesos competirian por
las lineas de GPIO y los canales de modulacion, que son recursos exclusivos:
el segundo en arrancar fallaria al reservarlos. Ademas no habria nada que
ganar, porque el servidor de inferencia serializa las consultas de todos
modos.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    direccion = os.environ.get("JARVIS_HOST", "0.0.0.0")
    try:
        puerto = int(os.environ.get("JARVIS_PORT", "8000"))
    except ValueError:
        print("JARVIS_PORT no es un numero; se usa 8000", file=sys.stderr)
        puerto = 8000

    try:
        import uvicorn
    except ImportError:
        print("falta uvicorn: el portal no puede arrancar.\n"
              "En la imagen lo provee python3-uvicorn; en una laptop de "
              "desarrollo, 'pip install uvicorn fastapi python-multipart "
              "itsdangerous'.", file=sys.stderr)
        return 1

    # Importar el portal construye la HAL y abre el almacen. Se hace aqui,
    # despues de resolver los parametros, para que un error de configuracion
    # no deje lineas de GPIO reservadas.
    from .portal import app

    print(f"portal escuchando en http://{direccion}:{puerto}")
    uvicorn.run(
        app,
        host=direccion,
        port=puerto,
        workers=1,
        log_level="warning",
        # Una linea por peticion en el diario del sistema es escritura
        # constante en la MicroSD sin valor de diagnostico: el portal ya
        # registra las decisiones del agente en su bitacora.
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
