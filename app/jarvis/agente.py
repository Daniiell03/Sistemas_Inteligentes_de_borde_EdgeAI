"""Agente coordinador del asistente de aula (Rol A).

El agente no clasifica ni redacta por si mismo. Su funcion es de control:
determina a cual modelo consultar, con que preguntas, que herramienta
invocar, y cuando detenerse. Esa separacion es la contribucion central del
diseno: el modelo de decision y el generativo son recursos que el agente
coordina, no el sistema en si.

CICLO
-----
    percibir -> decidir -> actuar -> evaluar

Percibir compone el estado observable a partir del sensor, la persistencia y
las zonas de iluminacion. Decidir consulta el carril de decision. Actuar
invoca una herramienta. Evaluar determina si la orden quedo satisfecha o si
corresponde otra iteracion.

POLITICA DE PARADA
------------------
El ciclo termina por una de cinco razones, todas resultados legitimos del
sistema y ninguna un error:

    CUMPLIDA        la orden se ejecuto
    SIN_CONFIANZA   la clasificacion no supero el umbral (RF-02)
    FALTA_DATO      la operacion necesita informacion que la orden no trae
    CONFIRMACION    la accion es irreversible y espera confirmacion (RF-09)
    AGOTADO         se alcanzo el maximo de iteraciones

ITERACIONES
-----------
La propuesta contemplaba hasta cinco iteraciones. Sobre esta plataforma cada
iteracion cuesta quince segundos, de modo que una orden que itere tres veces
tarda cuarenta y cinco: mas de lo que nadie espera frente a un formulario. El
maximo se fija en dos y las ordenes compuestas quedan fuera del caso comun.
El valor es configurable por si la plataforma cambia.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from . import catalogo, inferencia
from .hal import TempHumSensor
from .hal_rooms import PALETTE, Room, RoomLight
from .persistencia import Almacen, SinTareas

MAX_ITERACIONES = 2


class Parada(str, Enum):
    CUMPLIDA = "cumplida"
    SIN_CONFIANZA = "sin_confianza"
    FALTA_DATO = "falta_dato"
    CONFIRMACION = "confirmacion"
    AGOTADO = "agotado"
    ERROR = "error"


@dataclass
class Respuesta:
    """Lo que el agente devuelve al portal."""

    motivo: Parada
    mensaje: str
    accion: Optional[str] = None
    confianza: float = 0.0
    zona: Optional[str] = None
    latencia: float = 0.0
    iteraciones: int = 1
    datos: Dict[str, Any] = field(default_factory=dict)

    @property
    def ejecutada(self) -> bool:
        return self.motivo is Parada.CUMPLIDA

    def dict(self) -> Dict[str, Any]:
        return {
            "status": "ok" if self.ejecutada else self.motivo.value,
            "message": self.mensaje,
            "intent": self.accion,
            "confidence": round(self.confianza, 3),
            "zone": self.zona,
            "latency": round(self.latencia, 2),
            "data": self.datos,
        }


@dataclass
class _Pendiente:
    """Accion esperando confirmacion del usuario.

    Por irreversibilidad —registrar una entrega— o por alcance —apagar el
    aula completa—. Ver catalogo.requiere_confirmacion().
    """

    accion: str
    orden: str
    zona: Optional[str]
    momento: float

    def vigente(self, ventana: float = 180.0) -> bool:
        return (time.monotonic() - self.momento) < ventana


AFIRMACIONES = ("si", "sí", "dale", "confirmo", "correcto", "adelante",
                "hacelo", "hazlo", "ok", "de una", "afirmativo")
NEGACIONES = ("no", "cancela", "cancelar", "olvidalo", "mejor no", "negativo")


def _color_en(orden: str) -> Optional[str]:
    """Color nombrado en la orden, resuelto contra la paleta de la HAL.

    Se recorre PALETTE en lugar de mantener una lista de colores aqui: si se
    agrega un color a la paleta, el agente lo reconoce sin tocar este
    modulo. Los nombres mas largos se prueban primero para que "azul
    profundo" no se resuelva como "azul".

    Como las escenas y la zona, se resuelve por texto y no por el modelo: el
    usuario nombra el color literalmente, y preguntarlo costaria una tercera
    llamada de siete segundos para distinguir opciones que ya estan escritas.
    """
    t = catalogo.normalizar(orden)
    for nombre in sorted(PALETTE, key=len, reverse=True):
        if catalogo.normalizar(nombre) in t:
            return nombre
    return None


class Agente:
    """Coordina los dos modelos y las herramientas del sistema.

    Las herramientas se reciben por constructor y no se construyen aqui: el
    agente opera igual contra la implementacion real de la HAL y contra la
    simulada, que es lo que permite desarrollarlo y probarlo sin la Pi.
    """

    def __init__(self, luces: RoomLight, sensor: TempHumSensor,
                 almacen: Almacen, max_iteraciones: int = MAX_ITERACIONES):
        self.luces = luces
        self.sensor = sensor
        self.almacen = almacen
        self.max_iteraciones = max_iteraciones
        self._pendiente: Dict[str, _Pendiente] = {}

    # -- ciclo --------------------------------------------------------------

    def procesar(self, rol: str, orden: str) -> Respuesta:
        """Ejecuta el ciclo completo para una orden en lenguaje natural."""
        orden = (orden or "").strip()
        if not orden:
            return Respuesta(Parada.FALTA_DATO, "No recibi ninguna orden.")

        # Una confirmacion pendiente se resuelve sin volver a clasificar:
        # reclasificar un "si" costaria otros quince segundos y el modelo no
        # tiene por que entender que se refiere a la orden anterior.
        resuelta = self._resolver_pendiente(rol, orden)
        if resuelta is not None:
            return resuelta

        # Ruta rapida: si la orden nombra una escena de forma inequivoca, se
        # aplica sin consultar al modelo. Cero tokens y respuesta inmediata,
        # frente a los quince segundos y la clasificacion poco fiable que
        # midieron las campanas del 28/10, 6/11 y 9/11. El catalogo ya
        # resolvia la escena por texto; lo que faltaba era hacerlo antes de
        # clasificar y no despues.
        escena = catalogo.escena_explicita(orden)
        if escena is not None:
            t0 = time.perf_counter()
            respuesta = self._actuar(rol, orden, "LUZ_MODO", None)
            respuesta.confianza = 1.0
            respuesta.latencia = time.perf_counter() - t0
            self.almacen.anotar_decision(
                orden, "LUZ_MODO", 1.0, respuesta.ejecutada,
                "resuelta por coincidencia de texto, sin consultar al modelo")
            return respuesta

        # Segunda ruta rapida, por la misma razon que la de escenas: la
        # palabra "pendiente" identifica la consulta de entregas sin
        # ambiguedad en este dominio. Medido el 10 de noviembre desde el
        # celular, el ejemplo que la propia interfaz sugiere —"¿Que tengo
        # pendiente?"— era rechazado por falta de confianza tras quince
        # segundos de espera.
        accion_texto = catalogo.operacion_explicita(orden)
        if accion_texto is not None:
            t0 = time.perf_counter()
            respuesta = self._actuar(rol, orden, accion_texto, None)
            respuesta.confianza = 1.0
            respuesta.latencia = time.perf_counter() - t0
            self.almacen.anotar_decision(
                orden, accion_texto, 1.0, respuesta.ejecutada,
                "resuelta por coincidencia de texto, sin consultar al modelo")
            return respuesta

        # La zona se resuelve por texto antes de clasificar y se entrega a la
        # inferencia, que entonces omite su segunda llamada. Son 7,6 s menos
        # en toda orden de iluminacion, y acierta donde el modelo fallaba.
        zona_texto = catalogo.zona_explicita(orden)

        # "apaga todas las luces" no nombra una zona: nombra el aula entera.
        # Declararlo explicitamente evita la segunda llamada tambien en ese
        # caso, y es lo que hace que la confirmacion por alcance se dispare
        # sin haber consultado al modelo por la zona.
        if zona_texto is None and catalogo.menciona_todo(orden):
            zona_texto = "NINGUNA"

        t0 = time.perf_counter()
        try:
            decision = inferencia.clasificar(orden, zona_conocida=zona_texto)
        except inferencia.ErrorInferencia as e:
            return Respuesta(Parada.ERROR, str(e), latencia=time.perf_counter() - t0)

        # Abstencion: con una precision base publicada de 63,5 %, reconocer
        # cuando no se debe confiar en el propio modelo es un requisito
        # funcional, no un manejo de error.
        if not decision.confiable:
            self.almacen.anotar_decision(orden, decision.accion, decision.confianza,
                                         False, "confianza bajo umbral")
            return Respuesta(
                Parada.SIN_CONFIANZA,
                "No entendi la orden con suficiente seguridad. "
                "Probá decirlo de otra forma.",
                accion=decision.accion,
                confianza=decision.confianza,
                latencia=decision.latencia,
            )

        if decision.accion == "NADA":
            self.almacen.anotar_decision(orden, "NADA", decision.confianza,
                                         False, "fuera de alcance")
            return Respuesta(
                Parada.CUMPLIDA,
                "Eso está fuera de lo que puedo hacer.",
                accion="NADA",
                confianza=decision.confianza,
                latencia=decision.latencia,
            )

        # Se anuncia y se espera confirmacion (RF-09), por irreversibilidad o
        # por alcance. Los dos casos necesitan mensajes distintos: anunciar
        # "voy a hacerlo, confirma" sin decir que se va a hacer convierte la
        # confirmacion en un tramite y deja de proteger de nada.
        if catalogo.requiere_confirmacion(decision.accion, decision.zona):
            datos = {}
            if decision.accion == "ENTREGA_REG":
                datos = self._extraer_entrega(orden)
                if not datos:
                    self.almacen.anotar_decision(orden, decision.accion,
                                                 decision.confianza, False, "falta dato")
                    return Respuesta(
                        Parada.FALTA_DATO,
                        "¿De quién es la entrega y de qué trabajo?",
                        accion=decision.accion,
                        confianza=decision.confianza,
                        latencia=decision.latencia,
                    )
                aviso = (f"Voy a registrar la entrega de {datos['estudiante']} "
                         f"({datos['trabajo']}). ¿Confirmás?")
            else:
                aviso = ("Eso apaga la iluminación de todo el aula. "
                         "¿Confirmás?")

            self._pendiente[rol] = _Pendiente(decision.accion, orden,
                                              decision.zona, time.monotonic())
            self.almacen.anotar_decision(orden, decision.accion,
                                         decision.confianza, False, "espera confirmacion")
            return Respuesta(
                Parada.CONFIRMACION,
                aviso,
                accion=decision.accion,
                confianza=decision.confianza,
                latencia=decision.latencia,
                datos=datos,
            )

        respuesta = self._actuar(rol, orden, decision.accion, decision.zona)
        respuesta.confianza = decision.confianza
        respuesta.latencia = decision.latencia + (time.perf_counter() - t0 - decision.latencia)
        respuesta.zona = decision.zona
        self.almacen.anotar_decision(orden, decision.accion, decision.confianza,
                                     respuesta.ejecutada, respuesta.motivo.value)
        return respuesta

    # -- actuacion ----------------------------------------------------------

    def _actuar(self, rol: str, orden: str, accion: str,
                zona: Optional[str]) -> Respuesta:
        try:
            if accion == "LUZ_ON":
                return self._luz(zona, encender=True, color=_color_en(orden))
            if accion == "LUZ_OFF":
                return self._luz(zona, encender=False)
            if accion == "LUZ_MODO":
                return self._escena(orden)
            if accion == "AMBIENTE":
                return self._ambiente()
            if accion == "ENTREGA_VER":
                return self._ver_entregas(rol, orden)
            if accion == "AGENDA":
                return self._agenda()
            if accion == "RESUMEN":
                return self._resumen()
        except Exception as e:  # una herramienta fallo, no el modelo
            return Respuesta(Parada.ERROR, f"No pude completar la acción: {e}",
                             accion=accion)
        return Respuesta(Parada.ERROR, f"Acción sin implementar: {accion}", accion=accion)

    def _zonas_objetivo(self, zona: Optional[str]) -> list:
        disponibles = sorted(self.luces.available(), key=lambda r: r.name)
        if zona in (None, "NINGUNA", "AMBAS"):
            return disponibles
        try:
            objetivo = Room[zona]
        except KeyError:
            return disponibles
        return [objetivo] if objetivo in disponibles else []

    def _luz(self, zona: Optional[str], encender: bool,
             color: Optional[str] = None) -> Respuesta:
        objetivo = self._zonas_objetivo(zona)
        if not objetivo:
            return Respuesta(Parada.FALTA_DATO,
                             "Esa zona no está disponible en esta instalación.",
                             accion="LUZ_ON" if encender else "LUZ_OFF")
        for r in objetivo:
            if encender and color is not None:
                # Encender con un color nombrado es una sola operacion, no
                # encender y luego recolorear: dos llamadas producirian un
                # destello del color anterior.
                self.luces.set(r, PALETTE[color], 1.0)
            elif encender:
                self.luces.turn_on(r)
            else:
                self.luces.turn_off(r)
            estado = self.luces.get(r)
            if estado:
                nombre = next((k for k, v in PALETTE.items() if v == estado.color), "color")
                self.almacen.guardar_zona(r.name, nombre, estado.brightness)
        nombres = ", ".join(r.value for r in objetivo)
        verbo = "Encendí" if encender else "Apagué"
        detalle = f" en {color}" if (encender and color) else ""
        return Respuesta(Parada.CUMPLIDA,
                         f"{verbo} la iluminación de: {nombres}{detalle}.",
                         accion="LUZ_ON" if encender else "LUZ_OFF",
                         datos={"zonas": [r.name for r in objetivo],
                                "color": color})

    def _escena(self, orden: str) -> Respuesta:
        escena = catalogo.escena_en(orden)
        if not escena:
            return Respuesta(
                Parada.FALTA_DATO,
                "¿Qué escena querés? Tengo: " + ", ".join(catalogo.ESCENAS_CONOCIDAS) + ".",
                accion="LUZ_MODO")
        self.luces.apply_scene(escena)
        for r, st in self.luces.snapshot().items():
            nombre = next((k for k, v in PALETTE.items() if v == st.color), "color")
            self.almacen.guardar_zona(r.name, nombre, st.brightness)
        return Respuesta(Parada.CUMPLIDA, f"Apliqué la escena {escena}.",
                         accion="LUZ_MODO", datos={"escena": escena})

    def _ambiente(self) -> Respuesta:
        lectura = self.sensor.last_reading()
        if lectura is None:
            return Respuesta(Parada.ERROR,
                             "Todavía no tengo una lectura válida del sensor.",
                             accion="AMBIENTE")
        self.almacen.registrar_ambiente(lectura.temperature_c, lectura.humidity_pct)
        return Respuesta(
            Parada.CUMPLIDA,
            f"En el aula hay {lectura.temperature_c:.1f} grados y "
            f"{lectura.humidity_pct:.0f} por ciento de humedad.",
            accion="AMBIENTE",
            datos={"temperatura": lectura.temperature_c,
                   "humedad": lectura.humidity_pct})

    def _ver_entregas(self, rol: str, orden: str) -> Respuesta:
        # RF-04: el estudiante ve lo suyo, el profesor ve todo.
        filtro = None if rol == "profesor" else self._nombre_en(orden)
        entregas = self.almacen.entregas(filtro)
        if not entregas:
            return Respuesta(Parada.CUMPLIDA, "No hay entregas registradas todavía.",
                             accion="ENTREGA_VER", datos={"entregas": []})
        lista = "; ".join(f"{e.estudiante} ({e.trabajo})" for e in entregas[:10])
        return Respuesta(
            Parada.CUMPLIDA,
            f"Van {len(entregas)} entregas: {lista}.",
            accion="ENTREGA_VER",
            datos={"entregas": [e.__dict__ for e in entregas]})

    def _agenda(self) -> Respuesta:
        """Plan de la sesion: actividades de hoy mas entregas proximas.

        Una entrega con fecha limite es parte del plan tanto como un repaso.
        Mirar solo la tabla de agenda hacia que el agente respondiera "no hay
        nada programado" mientras la pantalla de al lado mostraba dos
        entregas con fecha, que es lo que se observo el 10 de noviembre.
        """
        eventos = self.almacen.agenda_de_hoy()
        tareas = self.almacen.tareas_proximas(3)

        if not eventos and not tareas:
            return Respuesta(
                Parada.CUMPLIDA,
                "No hay nada en la agenda de hoy ni entregas próximas.",
                accion="AGENDA", datos={"eventos": [], "tareas": []})

        partes = []
        if eventos:
            partes.append("En la agenda de hoy: "
                          + "; ".join(e.descripcion for e in eventos) + ".")
        if tareas:
            partes.append("Próximas entregas: "
                          + "; ".join(f"{t['titulo']}, {t['cuando']}"
                                      for t in tareas) + ".")

        return Respuesta(Parada.CUMPLIDA, " ".join(partes), accion="AGENDA",
                         datos={"eventos": [e.__dict__ for e in eventos],
                                "tareas": tareas})

    def _resumen(self) -> Respuesta:
        """Unica operacion que pasa por el carril generativo (RF-07)."""
        contexto = self.almacen.contexto_de_sesion()
        try:
            texto, t = inferencia.generar(
                "Resumi la sesion en una o dos frases.", contexto)
        except inferencia.ErrorInferencia as e:
            return Respuesta(Parada.ERROR, f"No pude redactar el resumen: {e}",
                             accion="RESUMEN")
        return Respuesta(Parada.CUMPLIDA, texto, accion="RESUMEN",
                         latencia=t, datos={"contexto": contexto})

    # -- confirmacion -------------------------------------------------------

    def _resolver_pendiente(self, rol: str, orden: str) -> Optional[Respuesta]:
        pendiente = self._pendiente.get(rol)
        if pendiente is None:
            return None
        if not pendiente.vigente():
            del self._pendiente[rol]
            return None

        t = orden.lower().strip(" .!¡")
        if any(t == a or t.startswith(a + " ") for a in NEGACIONES):
            del self._pendiente[rol]
            return Respuesta(Parada.CUMPLIDA, "Listo, no registré nada.",
                             accion=pendiente.accion)
        if not any(t == a or t.startswith(a + " ") for a in AFIRMACIONES):
            return None  # no es respuesta a la confirmacion: se clasifica normal

        del self._pendiente[rol]

        # Solo el registro de entregas necesita extraer datos de la orden
        # original. El resto se ejecuta por la via normal: asumir que todo
        # lo pendiente es una entrega fue correcto mientras ENTREGA_REG era
        # la unica operacion que se confirmaba.
        if pendiente.accion != "ENTREGA_REG":
            respuesta = self._actuar(rol, pendiente.orden,
                                     pendiente.accion, pendiente.zona)
            respuesta.confianza = 1.0
            self.almacen.anotar_decision(pendiente.orden, pendiente.accion,
                                         1.0, respuesta.ejecutada, "confirmada")
            return respuesta

        datos = self._extraer_entrega(pendiente.orden)
        if not datos:
            return Respuesta(Parada.FALTA_DATO,
                             "Perdí el detalle de la entrega. ¿Me la repetís?",
                             accion=pendiente.accion)
        try:
            self.almacen.registrar_entrega(datos["estudiante"], datos["trabajo"], rol)
        except SinTareas as e:
            # El profesor no ha creado ninguna tarea todavia. No es un fallo
            # del agente ni del modelo: es una precondicion del dominio, y
            # corresponde enunciarla. El portal ya lo capturaba en su
            # subclase; hacerlo aqui evita que el agente base caiga cuando se
            # usa sin el portal, que es como lo ejercita el banco.
            self.almacen.anotar_decision(pendiente.orden, pendiente.accion,
                                         1.0, False, "sin tareas creadas")
            return Respuesta(Parada.FALTA_DATO, str(e),
                             accion=pendiente.accion)
        self.almacen.anotar_decision(pendiente.orden, pendiente.accion, 1.0,
                                     True, "confirmada")
        return Respuesta(
            Parada.CUMPLIDA,
            f"Registré la entrega de {datos['estudiante']} ({datos['trabajo']}).",
            accion=pendiente.accion, confianza=1.0, datos=datos)

    # -- extraccion de datos de la orden ------------------------------------
    # El modelo de decision clasifica, no extrae entidades: pedirle que lo
    # haga seria otra pregunta y otros siete segundos. Para el caso de uso
    # previsto, una heuristica de texto alcanza.

    PALABRAS_VACIAS = {"la", "el", "de", "del", "entrega", "registra", "registrá",
                       "anota", "anotá", "que", "marca", "marcá", "como",
                       "recibida", "mi", "su", "trabajo", "tarea", "laboratorio",
                       "por", "favor", "ya", "entrego", "entregó"}

    def _nombre_en(self, orden: str) -> Optional[str]:
        """Primera palabra capitalizada que no sea inicio de frase."""
        for palabra in orden.split()[1:]:
            limpia = palabra.strip(".,;:")
            if limpia[:1].isupper() and limpia.lower() not in self.PALABRAS_VACIAS:
                return limpia
        return None

    def _extraer_entrega(self, orden: str) -> Optional[Dict[str, str]]:
        nombre = self._nombre_en(orden)
        if not nombre:
            return None
        resto = [p.strip(".,;:") for p in orden.split()
                 if p.strip(".,;:").lower() not in self.PALABRAS_VACIAS
                 and p.strip(".,;:") != nombre]
        trabajo = " ".join(resto) or "trabajo sin especificar"
        return {"estudiante": nombre, "trabajo": trabajo}
