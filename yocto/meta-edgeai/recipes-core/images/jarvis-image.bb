require recipes-core/images/core-image-base.bb

SUMMARY = "Imagen del asistente de voz Jarvis (EdgeAI)"

IMAGE_INSTALL:append = " \
    wifi-config avahi-daemon \
    alsa-utils libgpiod-tools \
    python3 libstdc++ whisper-cpp jarvis-rootfs-config ollama-bin ollama-bin \
    curl htop \
"
