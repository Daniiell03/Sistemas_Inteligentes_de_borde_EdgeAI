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
from .persistencia import Almacen

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
    """Accion irreversible esperando confirmacion del usuario."""

    accion: str
    orden: str
    zona: Optional[str]
    momento: float

    def vigente(self, ventana: float = 180.0) -> bool:
        return (time.monotonic() - self.momento) < ventana


AFIRMACIONES = ("si", "sí", "dale", "confirmo", "correcto", "adelante",
                "hacelo", "hazlo", "ok", "de una", "afirmativo")
NEGACIONES = ("no", "cancela", "cancelar", "olvidalo", "mejor no", "negativo")


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

        t0 = time.perf_counter()
        try:
            decision = inferencia.clasificar(orden)
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

        # Accion irreversible: se anuncia y se espera confirmacion (RF-09).
        if catalogo.requiere_confirmacion(decision.accion):
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
            self._pendiente[rol] = _Pendiente(decision.accion, orden,
                                              decision.zona, time.monotonic())
            self.almacen.anotar_decision(orden, decision.accion,
                                         decision.confianza, False, "espera confirmacion")
            return Respuesta(
                Parada.CONFIRMACION,
                f"Voy a registrar la entrega de {datos['estudiante']} "
                f"({datos['trabajo']}). ¿Confirmás?",
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
                return self._luz(zona, encender=True)
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

    def _luz(self, zona: Optional[str], encender: bool) -> Respuesta:
        objetivo = self._zonas_objetivo(zona)
        if not objetivo:
            return Respuesta(Parada.FALTA_DATO,
                             f"Esa zona no está disponible en esta instalación.",
                             accion="LUZ_ON" if encender else "LUZ_OFF")
        for r in objetivo:
            if encender:
                self.luces.turn_on(r)
            else:
                self.luces.turn_off(r)
            estado = self.luces.get(r)
            if estado:
                nombre = next((k for k, v in PALETTE.items() if v == estado.color), "color")
                self.almacen.guardar_zona(r.name, nombre, estado.brightness)
        nombres = ", ".join(r.value for r in objetivo)
        verbo = "Encendí" if encender else "Apagué"
        return Respuesta(Parada.CUMPLIDA, f"{verbo} la iluminación de: {nombres}.",
                         accion="LUZ_ON" if encender else "LUZ_OFF",
                         datos={"zonas": [r.name for r in objetivo]})

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
        eventos = self.almacen.agenda_de_hoy()
        if not eventos:
            return Respuesta(Parada.CUMPLIDA, "No hay nada en la agenda de hoy.",
                             accion="AGENDA", datos={"eventos": []})
        lista = "; ".join(e.descripcion for e in eventos)
        return Respuesta(Parada.CUMPLIDA, f"En la agenda de hoy: {lista}.",
                         accion="AGENDA",
                         datos={"eventos": [e.__dict__ for e in eventos]})

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
        datos = self._extraer_entrega(pendiente.orden)
        if not datos:
            return Respuesta(Parada.FALTA_DATO,
                             "Perdí el detalle de la entrega. ¿Me la repetís?",
                             accion=pendiente.accion)
        self.almacen.registrar_entrega(datos["estudiante"], datos["trabajo"], rol)
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
