#!/usr/bin/env python3
"""Banco de pruebas del agente coordinador, de punta a punta.

Ejercita el ciclo completo —clasificar, actuar sobre la capa de abstraccion
de hardware, persistir, responder— contra la implementacion simulada. No
requiere Raspberry Pi ni periferico alguno.

MODOS
-----
    simulado   (por defecto si no hay servidor de inferencia)
        Sustituye el carril de decision por un clasificador de prueba que
        devuelve la accion esperada. Verifica la logica del agente: caminos
        de actuacion, umbral de abstencion, flujo de confirmacion, falta de
        datos y resolucion de escenas. Corre en menos de un segundo.

    real       (--real, o automatico si el servidor responde)
        Clasifica con tev1:0.8b. Verifica ademas que el modelo acierte. A
        quince segundos por orden, la corrida completa toma varios minutos.

El modo simulado no valida que el modelo clasifique bien —para eso esta
medir_borde.py— sino que el agente haga lo correcto con una clasificacion
dada. Son dos preguntas distintas y conviene no confundirlas: un fallo en
este banco es un defecto del codigo, y un fallo en el otro es una
limitacion del modelo.

Uso:
    python3 -m tools.probar_agente              # desde app/
    python3 app/tools/probar_agente.py --real
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from typing import List, Optional, Tuple

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

from jarvis import catalogo, inferencia  # noqa: E402
from jarvis.agente import Agente, Parada  # noqa: E402
from jarvis.hal_fake import FakeRoomLight, FakeTempHumSensor  # noqa: E402
from jarvis.hal_rooms import Room  # noqa: E402
from jarvis.persistencia import Almacen  # noqa: E402

VERDE, ROJO, GRIS, FIN = "\033[32m", "\033[31m", "\033[90m", "\033[0m"


# ---------------------------------------------------------------------------
# Clasificador de prueba
# ---------------------------------------------------------------------------

class _Falso:
    """Sustituye a inferencia.clasificar con respuestas predeterminadas.

    Se instala monkey-patching el modulo, que es la unica forma de aislar el
    agente sin introducir una inyeccion de dependencia que solo existiria
    para las pruebas.
    """

    def __init__(self):
        self.guion: List[Tuple[str, float, Optional[str]]] = []
        self.consultas: List[str] = []

    def programar(self, accion: str, confianza: float = 0.9,
                  zona: Optional[str] = None) -> None:
        self.guion.append((accion, confianza, zona))

    def clasificar(self, orden: str) -> inferencia.Decision:
        self.consultas.append(orden)
        if not self.guion:
            raise AssertionError(f"clasificacion no programada para {orden!r}")
        accion, confianza, zona = self.guion.pop(0)
        return inferencia.Decision(
            accion=accion,
            confianza=confianza,
            zona=zona,
            riesgo=catalogo.requiere_confirmacion(accion),
            latencia=0.0,
            tokens=277,
        )

    def generar(self, instruccion: str, contexto: str = "",
                max_tokens: int = 40) -> Tuple[str, float]:
        return ("Hoy se registraron entregas y se reviso la agenda.", 0.0)


# ---------------------------------------------------------------------------
# Marco de pruebas
# ---------------------------------------------------------------------------

class Banco:
    def __init__(self):
        self.ok = 0
        self.fallas: List[str] = []

    def verificar(self, nombre: str, condicion: bool, detalle: str = "") -> None:
        if condicion:
            self.ok += 1
            print(f"  {VERDE}OK{FIN}    {nombre}")
        else:
            self.fallas.append(nombre)
            print(f"  {ROJO}FALLA{FIN} {nombre}")
            if detalle:
                print(f"        {GRIS}{detalle}{FIN}")

    def seccion(self, titulo: str) -> None:
        print(f"\n{titulo}")
        print("-" * 70)

    def resumen(self) -> int:
        total = self.ok + len(self.fallas)
        print("\n" + "=" * 70)
        if self.fallas:
            print(f"{ROJO}{len(self.fallas)} de {total} verificaciones fallaron:{FIN}")
            for f in self.fallas:
                print(f"  - {f}")
            return 1
        print(f"{VERDE}Las {total} verificaciones pasaron.{FIN}")
        return 0


def _nuevo_agente(db: str) -> Tuple[Agente, FakeRoomLight, FakeTempHumSensor, Almacen]:
    luces = FakeRoomLight(silencioso=True) if _acepta_silencio() else FakeRoomLight()
    sensor = FakeTempHumSensor()
    almacen = Almacen(db)
    return Agente(luces, sensor, almacen), luces, sensor, almacen


def _acepta_silencio() -> bool:
    import inspect
    return "silencioso" in inspect.signature(FakeRoomLight.__init__).parameters


# ---------------------------------------------------------------------------
# Pruebas en modo simulado
# ---------------------------------------------------------------------------

def pruebas_simuladas(b: Banco, db: str) -> None:
    falso = _Falso()
    original_clasificar = inferencia.clasificar
    original_generar = inferencia.generar
    inferencia.clasificar = falso.clasificar
    inferencia.generar = falso.generar

    try:
        agente, luces, sensor, almacen = _nuevo_agente(db)

        # -- iluminacion ---------------------------------------------------
        b.seccion("Iluminacion")

        falso.programar("LUZ_ON", 0.96, "FRENTE")
        r = agente.procesar("profesor", "encende la luz del frente")
        b.verificar("LUZ_ON enciende la zona indicada",
                    r.motivo is Parada.CUMPLIDA and luces.get(Room.FRENTE).is_on,
                    f"motivo={r.motivo.value} mensaje={r.mensaje!r}")

        falso.programar("LUZ_OFF", 0.95, "AMBAS")
        r = agente.procesar("profesor", "apaga todas las luces")
        apagadas = all(not (luces.get(z) and luces.get(z).is_on)
                       for z in luces.available())
        b.verificar("LUZ_OFF con zona AMBAS apaga todo",
                    r.motivo is Parada.CUMPLIDA and apagadas,
                    f"motivo={r.motivo.value}")

        falso.programar("LUZ_ON", 0.9, "LATERAL")
        r = agente.procesar("profesor", "prende el lateral")
        b.verificar("zona declarada pero no disponible devuelve falta de dato",
                    r.motivo is Parada.FALTA_DATO,
                    f"motivo={r.motivo.value} mensaje={r.mensaje!r}")

        # -- escenas -------------------------------------------------------
        b.seccion("Escenas")

        falso.programar("LUZ_MODO", 0.88)
        r = agente.procesar("profesor", "pone el aula en modo proyeccion")
        b.verificar("LUZ_MODO resuelve la escena por texto",
                    r.motivo is Parada.CUMPLIDA and r.datos.get("escena") == "proyeccion",
                    f"datos={r.datos}")

        b.verificar("la escena proyeccion deja el frente apagado",
                    not luces.get(Room.FRENTE).is_on,
                    f"frente={luces.get(Room.FRENTE)}")

        falso.programar("LUZ_MODO", 0.88)
        r = agente.procesar("profesor", "cambia el modo")
        b.verificar("LUZ_MODO sin escena reconocible pide la escena",
                    r.motivo is Parada.FALTA_DATO,
                    f"motivo={r.motivo.value} mensaje={r.mensaje!r}")

        # -- ambiente ------------------------------------------------------
        b.seccion("Ambiente")

        falso.programar("AMBIENTE", 0.82)
        r = agente.procesar("estudiante", "que temperatura hay en el aula")
        b.verificar("AMBIENTE devuelve una lectura",
                    r.motivo is Parada.CUMPLIDA and "temperatura" in r.datos,
                    f"motivo={r.motivo.value} datos={r.datos}")

        # -- entregas ------------------------------------------------------
        b.seccion("Entregas y confirmacion")

        falso.programar("ENTREGA_REG", 0.97)
        r = agente.procesar("profesor", "registra la entrega de Maria del laboratorio 3")
        b.verificar("ENTREGA_REG pide confirmacion antes de persistir",
                    r.motivo is Parada.CONFIRMACION,
                    f"motivo={r.motivo.value} mensaje={r.mensaje!r}")
        b.verificar("la entrega no se persiste antes de confirmar",
                    almacen.contar_entregas() == 0,
                    f"entregas={almacen.contar_entregas()}")
        b.verificar("la confirmacion identifica al estudiante",
                    r.datos.get("estudiante") == "Maria",
                    f"datos={r.datos}")

        antes = len(falso.consultas)
        r = agente.procesar("profesor", "si")
        b.verificar("la confirmacion persiste la entrega",
                    r.motivo is Parada.CUMPLIDA and almacen.contar_entregas() == 1,
                    f"motivo={r.motivo.value} entregas={almacen.contar_entregas()}")
        b.verificar("confirmar no vuelve a consultar al modelo",
                    len(falso.consultas) == antes,
                    f"consultas antes={antes} despues={len(falso.consultas)}")

        falso.programar("ENTREGA_REG", 0.97)
        agente.procesar("profesor", "anota que Jose entrego el laboratorio tres")
        r = agente.procesar("profesor", "no")
        b.verificar("la negacion cancela sin persistir",
                    r.motivo is Parada.CUMPLIDA and almacen.contar_entregas() == 1,
                    f"entregas={almacen.contar_entregas()}")

        falso.programar("ENTREGA_REG", 0.95)
        r = agente.procesar("profesor", "registra la entrega")
        b.verificar("ENTREGA_REG sin estudiante pide el dato",
                    r.motivo is Parada.FALTA_DATO,
                    f"motivo={r.motivo.value} mensaje={r.mensaje!r}")

        falso.programar("ENTREGA_VER", 0.9)
        r = agente.procesar("profesor", "quien ya entrego")
        b.verificar("ENTREGA_VER lista las entregas",
                    r.motivo is Parada.CUMPLIDA and len(r.datos.get("entregas", [])) == 1,
                    f"datos={r.datos}")

        # -- agenda y resumen ----------------------------------------------
        b.seccion("Agenda y resumen")

        almacen.agregar_evento("Repaso de Yocto")
        falso.programar("AGENDA", 0.85)
        r = agente.procesar("estudiante", "que hay programado hoy")
        b.verificar("AGENDA devuelve los eventos del dia",
                    r.motivo is Parada.CUMPLIDA and r.datos.get("eventos"),
                    f"datos={r.datos}")

        falso.programar("RESUMEN", 0.9)
        r = agente.procesar("profesor", "resumime la sesion")
        b.verificar("RESUMEN usa el carril generativo",
                    r.motivo is Parada.CUMPLIDA and r.mensaje,
                    f"motivo={r.motivo.value} mensaje={r.mensaje!r}")

        # -- abstencion ----------------------------------------------------
        b.seccion("Abstencion y fuera de alcance")

        falso.programar("LUZ_ON", 0.31, "FRENTE")
        r = agente.procesar("profesor", "asdfgh qwerty")
        b.verificar("confianza bajo el umbral produce abstencion",
                    r.motivo is Parada.SIN_CONFIANZA,
                    f"motivo={r.motivo.value} conf={r.confianza}")

        falso.programar("NADA", 0.84)
        r = agente.procesar("estudiante", "cuanto cuesta un carro en Japon")
        b.verificar("NADA responde fuera de alcance sin error",
                    r.motivo is Parada.CUMPLIDA and r.accion == "NADA",
                    f"motivo={r.motivo.value}")

        r = agente.procesar("profesor", "   ")
        b.verificar("orden vacia no consulta al modelo",
                    r.motivo is Parada.FALTA_DATO,
                    f"motivo={r.motivo.value}")

        # -- trazabilidad ---------------------------------------------------
        b.seccion("Bitacora de decisiones")

        decisiones = almacen.decisiones()
        b.verificar("cada orden queda registrada en la bitacora",
                    len(decisiones) >= 10,
                    f"registradas={len(decisiones)}")
        abstenciones = [d for d in decisiones if not d["ejecutada"]]
        b.verificar("las abstenciones quedan registradas",
                    len(abstenciones) >= 1,
                    f"abstenciones={len(abstenciones)}")

        # -- contrato con el portal ------------------------------------------
        b.seccion("Contrato con el portal")

        falso.programar("AMBIENTE", 0.8)
        d = agente.procesar("estudiante", "a cuanto estamos").dict()
        b.verificar("la respuesta trae las claves que consume el frontend",
                    all(k in d for k in ("status", "message", "intent", "confidence")),
                    f"claves={sorted(d)}")

    finally:
        inferencia.clasificar = original_clasificar
        inferencia.generar = original_generar


# ---------------------------------------------------------------------------
# Pruebas con el modelo real
# ---------------------------------------------------------------------------

CASOS_REALES = [
    ("encende la luz del frente", "LUZ_ON"),
    ("apaga todas las luces", "LUZ_OFF"),
    ("pone el aula en modo proyeccion", "LUZ_MODO"),
    ("que temperatura hay en el aula", "AMBIENTE"),
    ("registra la entrega de Maria", "ENTREGA_REG"),
    ("quien ya entrego", "ENTREGA_VER"),
    ("que hay programado hoy", "AGENDA"),
    ("resumime la sesion", "RESUMEN"),
    ("asdfgh qwerty zxcvb", "NADA"),
]


def pruebas_reales(b: Banco, db: str) -> None:
    b.seccion("Clasificacion con el modelo real")
    residentes = inferencia.modelos_residentes()
    print(f"  modelos residentes: {', '.join(residentes) or 'ninguno'}")
    if len(residentes) < 2:
        print(f"  {GRIS}aviso: con menos de dos modelos residentes la primera"
              f" orden paga la carga desde el medio de arranque{FIN}")

    agente, _, _, almacen = _nuevo_agente(db)
    aciertos = 0
    latencias = []

    for orden, esperada in CASOS_REALES:
        t0 = time.perf_counter()
        try:
            r = agente.procesar("profesor", orden)
        except Exception as e:
            b.verificar(f"{orden!r}", False, f"excepcion: {e}")
            continue
        t = time.perf_counter() - t0
        latencias.append(t)
        acerto = r.accion == esperada
        aciertos += acerto
        marca = f"{VERDE}OK   {FIN}" if acerto else f"{ROJO}FALLA{FIN}"
        print(f"  {marca} {t:5.1f}s  conf={r.confianza:.2f}  "
              f"{r.accion or '-':<12} esperado {esperada:<12} {orden!r}")

    if latencias:
        latencias.sort()
        p50 = latencias[len(latencias) // 2]
        print(f"\n  acierto: {aciertos}/{len(CASOS_REALES)}"
              f"  latencia p50: {p50:.1f}s  max: {latencias[-1]:.1f}s")
        presupuesto = 277 / 18.5
        b.verificar(f"la latencia mediana respeta la ley ({presupuesto:.1f}s esperados)",
                    p50 < presupuesto * 1.6,
                    f"p50={p50:.1f}s")
    b.verificar("el modelo acierta al menos dos tercios de los casos",
                aciertos >= len(CASOS_REALES) * 2 // 3,
                f"aciertos={aciertos}/{len(CASOS_REALES)}")


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--real", action="store_true",
                    help="clasificar con el modelo en lugar del guion de prueba")
    ap.add_argument("--db", default=None,
                    help="ruta de la base de datos (por defecto, temporal)")
    args = ap.parse_args()

    print("=" * 70)
    print("BANCO DEL AGENTE COORDINADOR — Jarvis Aula")
    print("=" * 70)

    temporal = None
    if args.db:
        db = args.db
    else:
        temporal = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temporal.close()
        db = temporal.name
    print(f"almacen de prueba: {db}")

    b = Banco()
    try:
        if args.real:
            if not inferencia.disponible():
                print(f"\n{ROJO}El servidor de inferencia no responde en "
                      f"{inferencia.SERVIDOR}{FIN}")
                return 1
            pruebas_reales(b, db)
        else:
            pruebas_simuladas(b, db)
        return b.resumen()
    finally:
        if temporal:
            try:
                os.unlink(db)
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
