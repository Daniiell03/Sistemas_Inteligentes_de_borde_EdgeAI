#!/bin/sh
# Empaqueta un modelo de Ollama (manifiesto + blobs que referencia) para la imagen de Yocto.
# El paquete queda en $DL_DIR/jarvis-models/ (fuera del repositorio: pesa cientos de MB).
# Requisito: Ollama instalado en el host con la MISMA version que la imagen (0.40.6).
# Uso: yocto/scripts/preparar-modelo.sh [modelo]      (por defecto tev1:0.8b)
set -e
MODELO="${1:-tev1:0.8b}"
ORIGEN="${OLLAMA_MODELS:-/usr/share/ollama/.ollama/models}"
DESTINO="${DL_DIR:?Defina DL_DIR (ver ~/.bashrc)}/jarvis-models"
REPO="${MODELO%%:*}"
TAG="${MODELO#*:}"
NOMBRE="$(echo "$MODELO" | tr ':' '-')-ollama.tar"
MANIFIESTO="manifests/registry.ollama.ai/library/$REPO/$TAG"

ollama pull "$MODELO"
mkdir -p "$DESTINO"

BLOBS=$(sudo python3 -c '
import json, sys
m = json.load(open(sys.argv[1]))
print(" ".join("blobs/" + d["digest"].replace(":", "-") for d in [m["config"]] + m["layers"]))
' "$ORIGEN/$MANIFIESTO")

sudo tar -C "$ORIGEN" -cf "$DESTINO/$NOMBRE" "$MANIFIESTO" $BLOBS
sudo chown "$(id -u):$(id -g)" "$DESTINO/$NOMBRE"

echo "Paquete: $DESTINO/$NOMBRE"
tar -tvf "$DESTINO/$NOMBRE"
