#!/bin/bash
# Escritura segura de la imagen de Jarvis a una tarjeta MicroSD.
#
#   sudo ./flashear.sh /dev/sdX
#
# Verifica, antes de escribir un solo byte:
#   - que el destino exista y sea un dispositivo de bloque (no un archivo),
#   - que sea removible (nunca el disco del sistema),
#   - que no sea el disco donde esta montada la raiz,
#   - que su tamano sea suficiente,
#   - que la imagen y su mapa de bloques existan.
#
# Cualquier verificacion fallida aborta el script completo. No hay forma de
# que la escritura ocurra sobre un destino equivocado.

set -euo pipefail

IMAGEN_DIR="build/tmp/deploy/images/raspberrypi4-64"
IMAGEN="$IMAGEN_DIR/jarvis-image-raspberrypi4-64.rootfs.wic.bz2"
BMAP="$IMAGEN_DIR/jarvis-image-raspberrypi4-64.rootfs.wic.bmap"

rojo()  { printf '\033[1;31m%s\033[0m\n' "$*"; }
verde() { printf '\033[1;32m%s\033[0m\n' "$*"; }
info()  { printf '  %s\n' "$*"; }

abortar() { rojo "ABORTA: $*"; exit 1; }

# --- argumento ------------------------------------------------------------
if [ $# -ne 1 ]; then
    rojo "Uso: sudo $0 /dev/sdX"
    echo
    echo "Dispositivos removibles detectados:"
    lsblk -dno NAME,SIZE,RM,MODEL | awk '$3==1 {printf "  /dev/%s  %s  %s\n", $1, $2, $4}'
    exit 1
fi

DESTINO="$1"

echo
echo "=============================================="
echo " Escritura de imagen — verificaciones previas"
echo "=============================================="
echo

# --- 1. la imagen existe --------------------------------------------------
[ -f "$IMAGEN" ] || abortar "no se encuentra la imagen en $IMAGEN
       Ejecute el script desde la raiz del repositorio."
info "imagen:  $(du -h "$IMAGEN" | cut -f1)  $IMAGEN"

if [ -f "$BMAP" ]; then
    info "mapa de bloques presente: escritura rapida"
    USAR_BMAP=1
else
    info "sin mapa de bloques: escritura completa (mas lenta)"
    USAR_BMAP=0
fi

# --- 2. el destino es un dispositivo de bloque ----------------------------
# Esta es la verificacion que evita que bmaptool cree un archivo regular
# cuando el destino no existe, llenando el sistema de archivos.
[ -e "$DESTINO" ] || abortar "$DESTINO no existe. ¿Esta insertada la tarjeta?"
[ -b "$DESTINO" ] || abortar "$DESTINO existe pero NO es un dispositivo de bloque.
       Si es un archivo regular creado por un intento anterior, eliminelo:
         sudo rm -f $DESTINO"

NOMBRE="$(basename "$DESTINO")"

# --- 3. es removible ------------------------------------------------------
REMOVIBLE="$(lsblk -ndo RM "$DESTINO" 2>/dev/null || echo 0)"
[ "$REMOVIBLE" = "1" ] || abortar "$DESTINO NO es removible.
       Escribir ahi destruiria un disco fijo del sistema."

# --- 4. no es el disco de la raiz ----------------------------------------
RAIZ_DEV="$(findmnt -no SOURCE / || true)"
RAIZ_DISCO="$(lsblk -ndo PKNAME "$RAIZ_DEV" 2>/dev/null || echo '')"
[ "$NOMBRE" != "$RAIZ_DISCO" ] || abortar "$DESTINO es el disco del sistema."

# --- 5. tamano suficiente -------------------------------------------------
TAM_BYTES="$(blockdev --getsize64 "$DESTINO")"
MIN_BYTES=$((10 * 1024 * 1024 * 1024))
[ "$TAM_BYTES" -ge "$MIN_BYTES" ] || abortar "la tarjeta tiene $(numfmt --to=iec "$TAM_BYTES"), se requieren al menos 10 GiB."

verde "Todas las verificaciones pasaron."
echo
info "destino: $DESTINO"
info "modelo:  $(lsblk -ndo MODEL "$DESTINO" | xargs)"
info "tamano:  $(lsblk -ndo SIZE "$DESTINO" | xargs)"
echo

# --- 6. confirmacion del operador ----------------------------------------
rojo "TODO EL CONTENIDO DE $DESTINO SE PERDERA."
printf 'Escriba el nombre del dispositivo para confirmar (%s): ' "$NOMBRE"
read -r CONFIRMACION
[ "$CONFIRMACION" = "$NOMBRE" ] || abortar "confirmacion incorrecta."

# --- 7. desmontar particiones --------------------------------------------
echo
info "desmontando particiones montadas..."
for particion in "${DESTINO}"*; do
    if mountpoint -q "$(findmnt -nro TARGET -S "$particion" 2>/dev/null || echo /nonexistent)" 2>/dev/null; then
        umount "$particion" && info "  desmontada $particion"
    fi
done

# --- 8. escribir ----------------------------------------------------------
echo
info "escribiendo..."
INICIO=$(date +%s)

if [ "$USAR_BMAP" = "1" ] && command -v bmaptool >/dev/null 2>&1; then
    bmaptool copy --bmap "$BMAP" "$IMAGEN" "$DESTINO"
else
    bzcat "$IMAGEN" | dd of="$DESTINO" bs=4M status=progress conv=fsync
fi

info "sincronizando..."
sync

FIN=$(date +%s)
echo
verde "Listo en $((FIN - INICIO)) segundos."

# --- 9. verificacion posterior -------------------------------------------
echo
info "particiones resultantes:"
partprobe "$DESTINO" 2>/dev/null || true
sleep 1
lsblk "$DESTINO"

echo
verde "La tarjeta esta lista."
echo
echo "Siguiente paso opcional — configurar la red sin reconstruir la imagen:"
echo "  sudo mount ${DESTINO}2 /mnt"
echo "  sudo nano /mnt/etc/wpa_supplicant/wpa_supplicant-wlan0.conf"
echo "  sudo umount /mnt"
echo
