require recipes-core/images/core-image-base.bb

SUMMARY = "Imagen del asistente Jarvis (EdgeAI)"

# Carril de decision (obligatorio) y carril generativo (opcional):
# quitar ollama-model-llama32 reduce la imagen ~1.3 GB y deja el
# sistema operando solo con operaciones de decision.
IMAGE_INSTALL:append = " \
    wifi-config avahi-daemon \
    alsa-utils libgpiod-tools \
    python3 libstdc++ whisper-cpp jarvis-rootfs-config \
    ollama-bin ollama-model-tev1 ollama-model-llama32 \
    jarvis-app \
    curl htop \
"

# Binario de Ollama (~1.45 GB) + modelos (~2.1 GB) + margen de operacion
IMAGE_ROOTFS_EXTRA_SPACE = "4194304"
