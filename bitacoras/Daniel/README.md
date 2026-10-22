# Bitácora individual — Líder Técnico / Arquitecto de Plataforma (Rol B)

**Proyecto:** II Proyecto — Sistemas Inteligentes de Borde (EdgeAI): asistente de voz "Jarvis"
**Curso:** Sistemas Embebidos · TEC
**Responsable:** Daniel
**Plataforma objetivo:** Raspberry Pi 4 (8 GB) · Yocto Project 6.0 "Wrynose"

---

## Entrada 1 — 20 de octubre de 2026 (noche)

**Actividades realizadas**
- Análisis de viabilidad de la propuesta "Jarvis" (wake word, Whisper.cpp, Ollama + API Jev, DHT22, LED) frente al instructivo y la rúbrica.
- Verificación de la API Jev: Ollama 0.35 incorpora `/v1/systemone`, que sirve modelos de decisión (`tev1:0.8b`, `tev1`, `nimble`) y no modelos de chat. Se descarta Llama 3.2 para la decisión y se adopta `tev1:0.8b` con umbral de confianza.
- Elaboración del plan de trabajo (README) para un equipo de dos personas, con división en Rol A (dirección y aplicación de IA) y Rol B (plataforma).
- Evaluación de Jenkins: se descarta por costo frente a beneficio en el plazo del proyecto. Se adopta kas para builds reproducibles y GitHub Actions para las pruebas de la aplicación; Jenkins queda como trabajo futuro.
- Preparación del host de desarrollo: Ubuntu 26.04 LTS, dependencias de Yocto, locale `en_US.UTF-8`, instalación de kas con pipx.

**Productos obtenidos**
- README con el plan de trabajo y el cronograma.
- Host listo para compilar Yocto.

**Problemas encontrados y solución**
- `liblz4-tool` no existe en Ubuntu 26.04 y abortó toda la instalación de `apt` → se reemplazó por el paquete `lz4`.
- Ubuntu 24.04 en adelante restringe con AppArmor los *user namespaces* sin privilegios que BitBake usa para aislar la red → se agregó `kernel.apparmor_restrict_unprivileged_userns = 0` en `/etc/sysctl.d/60-bitbake-userns.conf`.

**Referencias consultadas**
- Notas de versión de Ollama 0.35–0.40: https://github.com/ollama/ollama/releases
- kas, Getting Started: https://kas.readthedocs.io/en/5.4/userguide/getting-started.html

---

## Entrada 2 — 21 de octubre de 2026 (madrugada)

**Actividades realizadas**
- Selección de la versión de Yocto: 6.0 "Wrynose" (LTS hasta abril de 2030, Go 1.26).
- Se identificó que el repositorio combinado "poky" quedó obsoleto en Wrynose: las capas base se obtienen por separado (openembedded-core, BitBake 2.18 y meta-yocto).
- Se identificó que `meta-raspberrypi` tiene rama `wrynose` y que `master` ya apunta a la siguiente versión (blacksail), por lo que no es compatible.
- Creación del repositorio `edgeai-jarvis`, la capa `meta-edgeai` (`layer.conf`, compatible con `wrynose`) y la configuración `yocto/kas/jarvis.yml` (máquina `raspberrypi4-64`, distro `poky`, init systemd, SSH, caché de descargas y `sstate` fuera del repositorio).
- Receta `wifi-config`: wpa_supplicant + systemd-networkd, con lista de redes por prioridad y código de país `CR`.

**Productos obtenidos**
- Capa `meta-edgeai` y archivo kas versionados.
- Receta `wifi-config` (conexión automática a la red disponible de mayor prioridad).

**Problemas encontrados y solución**
- **Error de parseo** "unparsed line: 'EOT'" en `wifi-config.bb`: BitBake interpreta un `}` sin sangría como el fin de la función de shell, y el texto heredoc de wpa_supplicant contenía uno → el contenido se movió a un archivo aparte que la receta instala.
- **Credenciales en un repositorio público:** el archivo con contraseñas no puede versionarse → `wpa_supplicant-wlan0.conf` queda en `.gitignore` y se versiona solo un `.example`.
- **Aviso de `sstate` compartida:** la base de datos de equivalencia de hashes quedaba dentro del directorio de build e impedía reutilizar la caché → `BB_HASHSERVE_DB_DIR = "${SSTATE_DIR}"`.

**Referencias consultadas**
- Yocto Project 6.0 "Wrynose" is here!: https://www.yoctoproject.org/blog/2026/05/13/yocto-project-6-0-wrynose-is-here/
- Migración de capas base a Wrynose (BitBake 2.18): https://github.com/aws4embeddedlinux/meta-aws-demos/pull/469
- Rama wrynose de meta-raspberrypi: https://github.com/agherzan/meta-raspberrypi/pull/1625

---

## Entrada 3 — 21 de octubre de 2026 (tarde)

**Actividades realizadas**
- Primer build completo de `core-image-base` para `raspberrypi4-64`: 6192 tareas, todas exitosas.
- Fijación de las revisiones exactas de todas las capas con `kas lock` (`jarvis.lock.yml`).

**Productos obtenidos**
- Primera imagen funcional de Yocto para Raspberry Pi 4.
- Build reproducible: commits fijados de bitbake, openembedded-core, meta-yocto, meta-openembedded y meta-raspberrypi.

**Problemas encontrados y solución**
- Falló la descarga de `yocto-kernel-cache` desde git.yoctoproject.org → BitBake la obtuvo automáticamente de un servidor espejo (MIRRORS); no requirió intervención.

**Datos medidos**
- Duración del primer build: _[completar]_ · Host: laptop ASUS X510UQ, 8 GB RAM.

---

## Entrada 4 — 21 de octubre de 2026 (noche)

**Actividades realizadas**
- Generación del grafo de dependencias de la imagen (`bitbake -g`: `pn-buildlist`, `task-depends.dot`) y del manifiesto de paquetes, como insumo para el análisis de dependencias de la propuesta.
- Receta de imagen propia `jarvis-image.bb` (basada en `core-image-base`) con herramientas de prueba: `alsa-utils`, `libgpiod-tools`, `python3`, `libstdc++`, `curl`, `htop` y `avahi-daemon` (acceso por `jarvis.local`).
- Configuración del BSP: `RPI_EXTRA_CONFIG = "dtoverlay=dht11,gpiopin=4"` (el driver del kernel también maneja el DHT22 y lo expone como dispositivo IIO); 6 GB de espacio libre en la partición raíz para las pruebas de Ollama.
- Contrato de la HAL (`contracts/hal.py`): interfaces `TempHumSensor` (lectura en caché, no bloqueante) y `Actuator`, entregado al Rol A.
- Recetas `whisper-cpp_1.9.3.bb` (compilación estática con CMake, servicio `whisper-server` en systemd y configuración en `/etc/default/whisper-server`) y `whisper-models.bb` (modelos multilingües `tiny` y `base`, verificados con SHA-256).
- Inicio del análisis del binario oficial de Ollama 0.40.6 para `arm64`.

**Productos obtenidos**
- `jarvis-image` con whisper.cpp 1.9.3, whisper-server como servicio y modelos de voz; build exitoso.
- Verificación en el manifiesto: `kernel-module-dht11`, `libstdc++6`, Python 3.14.7, `whisper-cpp 1.9.3`, `whisper-models`, `wpa-supplicant`, `avahi-daemon`, `alsa-utils`, `libgpiod-tools`.

**Problemas encontrados y solución**
- Paquetes aparentemente ausentes en el manifiesto → Yocto los renombra al estilo Debian (`libstdc++` → `libstdc++6`, `python3` → `libpython3.14-1.0` + `python3-core`) y los módulos del kernel llevan la versión en el nombre.
- Un build terminó en segundos sin compilar whisper.cpp → la receta existía, pero no estaba agregada a `IMAGE_INSTALL` de `jarvis-image`.

**Referencias consultadas**
- Releases de whisper.cpp: https://github.com/ggml-org/whisper.cpp/releases

---

## Pendientes para la siguiente sesión (laboratorio, con hardware)

- Flashear `jarvis-image` y verificar arranque, Wi-Fi, servicios y RAM disponible.
- Cablear y probar el DHT22 (IIO) y el LED (`gpioset`).
- Probar el micrófono y el parlante (`arecord` / `aplay`).
- Probar whisper-server con voz en español (tiny contra base).
- Medir Ollama + `tev1:0.8b` en la Pi: latencia, RAM, temperatura y lecturas de la SD.
- Escribir la receta de Ollama (ruta crítica; corte el 30 de octubre).
