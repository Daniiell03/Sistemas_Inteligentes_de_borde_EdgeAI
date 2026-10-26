#!/usr/bin/env python3
"""Prueba de la iluminacion por habitaciones sobre hardware real (Rol B).

Se instala en app/tools/probar_luces.py y se ejecuta EN LA RASPBERRY PI:

    sudo python3 -m tools.probar_luces              # catodo comun
    sudo python3 -m tools.probar_luces --anodo      # anodo comun
    sudo python3 -m tools.probar_luces --simular    # sin hardware, en la laptop

Recorre cada habitacion y cada color, aplica las escenas y verifica que el
estado reportado coincide con el solicitado. Imprime un diagnostico previo
del hardware para localizar rapido un problema de cableado o de overlay.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.hal_rooms import PALETTE, Room, RoomLight  # noqa: E402

PAUSA = 1.2


def diagnostico() -> bool:
    """Revisa que el kernel exponga lo que la implementacion real necesita."""
    print("Diagnostico del hardware")
    print("-" * 56)
    ok = True

    chip = Path("/dev/gpiochip0")
    if chip.exists():
        print(f"  [ok]    {chip} presente")
    else:
        print(f"  [FALLA] {chip} no existe: el kernel no expone GPIO por chardev")
        ok = False

    pwm = Path("/sys/class/pwm/pwmchip0")
    if pwm.exists():
        canales = (pwm / "npwm").read_text().strip() if (pwm / "npwm").exists() else "?"
        print(f"  [ok]    {pwm} presente, {canales} canales")
    else:
        print(f"  [FALLA] {pwm} no existe: revise dtoverlay=pwm-2chan en config.txt")
        ok = False

    cfg = Path("/boot/config.txt")
    if not cfg.exists():
        cfg = Path("/boot/firmware/config.txt")
    if cfg.exists():
        texto = cfg.read_text()
        for clave in ("pwm-2chan", "dht11", "i2c_arm"):
            marca = "ok   " if clave in texto else "AVISO"
            print(f"  [{marca}] {clave} en {cfg.name}")
    else:
        print("  [aviso] no se encontro config.txt para revisar los overlays")

    print()
    return ok


def verificar(luces: RoomLight, room: Room, color_esperado, brillo_esperado) -> bool:
    estado = luces.get(room)
    if estado is None:
        print(f"      FALLA: get({room.value}) devolvio None")
        return False
    if estado.color != color_esperado or abs(estado.brightness - brillo_esperado) > 1e-6:
        print(f"      FALLA: estado reportado no coincide con el solicitado")
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--anodo", action="store_true",
                    help="LEDs de anodo comun (los pines drenan corriente)")
    ap.add_argument("--simular", action="store_true",
                    help="usar la HAL falsa, sin hardware")
    ap.add_argument("--pausa", type=float, default=PAUSA,
                    help=f"segundos entre pasos (por defecto {PAUSA})")
    args = ap.parse_args()

    if args.simular:
        from jarvis.hal_fake import FakeRoomLight
        luces: RoomLight = FakeRoomLight()
        print("Modo simulado: no se toca hardware\n")
    else:
        if not diagnostico():
            print("El diagnostico fallo. Corrija lo anterior antes de continuar.")
            return 1
        from jarvis.hal_rpi_rooms import PwmRoomLight
        try:
            luces = PwmRoomLight(active_low=args.anodo)
        except RuntimeError as e:
            print(f"No se pudo inicializar la iluminacion:\n  {e}")
            return 1

    fallas = 0
    try:
        habitaciones = sorted(luces.available(), key=lambda r: r.value)
        print(f"Habitaciones disponibles: {', '.join(r.value for r in habitaciones)}\n")

        print("1. Encendido y apagado por habitacion")
        for room in habitaciones:
            print(f"   {room.value}: encender")
            luces.turn_on(room)
            if not verificar(luces, room, PALETTE["calido"], 1.0):
                fallas += 1
            time.sleep(args.pausa)
            print(f"   {room.value}: apagar")
            luces.turn_off(room)
            time.sleep(args.pausa / 2)

        print("\n2. Paleta de colores")
        for nombre in ("rojo", "verde", "azul", "amarillo", "morado", "blanco"):
            print(f"   todas en {nombre}")
            for room in habitaciones:
                luces.set(room, PALETTE[nombre], 1.0)
                if not verificar(luces, room, PALETTE[nombre], 1.0):
                    fallas += 1
            time.sleep(args.pausa)
        luces.all_off()

        print("\n3. Rampa de intensidad (canal rojo, PWM por hardware)")
        for pct in (10, 30, 50, 70, 100, 70, 50, 30, 10):
            for room in habitaciones:
                luces.set(room, PALETTE["rojo"], pct / 100)
            print(f"   {pct:3d}%")
            time.sleep(0.4)
        luces.all_off()

        print("\n4. Escenas")
        for escena in ("cine", "despertar", "dormir", "enfoque", "salir"):
            print(f"   escena '{escena}'")
            luces.apply_scene(escena)
            time.sleep(args.pausa * 1.5)

        print("\n5. Apagado final")
        luces.all_off()
        for room in habitaciones:
            estado = luces.get(room)
            if estado and estado.is_on:
                print(f"      FALLA: {room.value} sigue encendida")
                fallas += 1

    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario")
    finally:
        cerrar = getattr(luces, "close", None)
        if callable(cerrar):
            cerrar()

    print()
    if fallas:
        print(f"Resultado: {fallas} verificacion(es) fallida(s)")
        return 1
    print("Resultado: todas las verificaciones pasaron")
    return 0


if __name__ == "__main__":
    sys.exit(main())
