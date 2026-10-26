# Bitácora individual — Líder Técnico / Arquitecto de Plataforma (Rol B)

**Proyecto:** II Proyecto — Sistemas Inteligentes de Borde (EdgeAI): asistente "Jarvis"
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

## Entrada 5 — 21 de octubre de 2026 (noche, continuación)

**Actividades realizadas**
- Decisión de diseño: el LED pasa a manejarse con el driver `gpio-led` del kernel (`dtoverlay=gpio-led,gpio=17,label=jarvis-led`), igual que el DHT22 se maneja con el driver `dht11` por IIO. La aplicación solo lee y escribe archivos en sysfs, por lo que no requiere `python3-gpiod` en la imagen.
- Implementación real de la HAL en `app/jarvis/hal_rpi.py`: `Dht22IioSensor` (hilo de muestreo cada 2.5 s, caché de la última lectura válida, validación de rangos, contador de errores) y `SysfsLed`.
- Script de prueba `app/tools/probar_hal.py` (10 ciclos de lectura con parpadeo del LED) para ejecutar en la Pi.
- Conexión del repositorio local con GitHub; rama `Daniel` unida al historial de `main`.

**Productos obtenidos**
- `config.txt` de la imagen con los dos overlays: `dtoverlay=dht11,gpiopin=4` y `dtoverlay=gpio-led,gpio=17,label=jarvis-led`.
- HAL real, lista para probar con hardware.

**Problemas encontrados y solución**
- Se versionaron por error los archivos `__pycache__/*.pyc` → se agregaron a `.gitignore` y se retiraron del repositorio.
- kas 5.5 marca como obsoleto el valor `excluded` para desactivar una capa → se reemplazó por `disabled`.

---

## Entrada 6 — 23 de octubre de 2026 (tarde)

**Actividades realizadas**

- Revisión del alcance de la aplicación con el Rol A. Se amplió el catálogo de operaciones de 4 a 42, organizadas en seis dominios (agenda, casa, sistema, ambiente, conocimiento y meta), y se decidió que la primera iteración opere por comandos de texto, dejando el pipeline de voz como capacidad disponible en la imagen pero fuera del alcance de la aplicación.
- **Decisión de diseño — router de dos etapas.** El catálogo de 42 operaciones excede el límite de 24 criterios por pregunta `choice` de Tev1, y la precisión del modelo de 0.8B se degrada con muchas opciones semánticamente cercanas. Se adoptó una clasificación jerárquica: primera pregunta de dominio (6 opciones) y segunda de acción dentro del dominio (máximo 12), junto con preguntas auxiliares de destino, completitud y riesgo en la misma solicitud.
- **Decisión de diseño — segundo carril de inferencia.** Las operaciones de resumen del día, preguntas abiertas y reescritura de texto requieren generación de lenguaje, que `/v1/systemone` no provee por servir únicamente modelos de decisión. Se incorporó `llama3.2:1b` sobre `/api/chat` como carril generativo, manteniendo `tev1:0.8b` como carril de decisión.
- **Clase `ollama-model.bbclass`.** Se implementó la lógica común de empaquetado de modelos de Ollama: resolución de la ruta de origen mediante `FILESEXTRAPATHS`, verificación de integridad, instalación en `${datadir}/ollama/models` y asignación de permisos. Cada receta de modelo aporta únicamente el nombre del paquete y su suma SHA-256.
- **Recetas `ollama-model-tev1` (0.8b) y `ollama-model-llama32` (1b).** Dos recetas independientes en lugar de una sola, para permitir retirar el carril generativo sin reconstruir el de decisión y para que el árbol de dependencias distinga componentes obligatorios de opcionales. Ambas heredan `allarch`: los modelos son datos y no varían por arquitectura.
- **Tarea `do_verify_model`.** Se añadió una tarea entre `do_unpack` y `do_install` que compara el SHA-256 del paquete contra el valor declarado en la receta y aborta el build si difieren.
- Empaquetado de ambos modelos con `yocto/scripts/preparar-modelo.sh`: `tev1:0.8b` (795 MB) y `llama3.2:1b` (1.3 GB), ambos fuera del repositorio por su tamaño.
- Ampliación de `IMAGE_ROOTFS_EXTRA_SPACE` a 4 GB para acomodar el binario de Ollama y los dos modelos.
- Construcción de `jarvis-image` con los tres paquetes y extracción del grafo de dependencias y el manifiesto como insumo del ítem 7 de la propuesta.

**Productos obtenidos**

- `yocto/meta-edgeai/classes/ollama-model.bbclass`
- `yocto/meta-edgeai/recipes-ai/ollama-models/ollama-model-tev1_0.8b.bb`
- `yocto/meta-edgeai/recipes-ai/ollama-models/ollama-model-llama32_1b.bb`
- `jarvis-image` de 2.3 GB comprimida, con Ollama 0.40.6 y ambos modelos horneados en `/usr/share/ollama/models`. Build de 6428 tareas, todas exitosas, sin advertencias.
- Análisis de dependencias con árbol, peso por componente y dependencias en tiempo de ejecución (`docs/dependencias/`).
- Ampliación del swap del host de 4 a 8 GB.

**Datos medidos**

- Primer build con los modelos: 27 min 29 s (422 tareas nuevas de 6428; el resto reutilizado de `sstate-cache`).
- Reconstrucción tras corregir la variable `S`: 16 min 11 s.
- Imagen: 2.3 GB comprimida. Los modelos representan el 97 % del peso total; el sistema base, el núcleo y los servicios suman menos de 80 MB.
- `ollama-model-llama32` 1204.9 MB · `ollama-model-tev1` 755.2 MB · `whisper-models` 192.1 MB · `ollama-bin` 16.0 MB.

**Problemas encontrados y solución**

- **Desajuste entre el nombre de archivo de la receta y el nombre del paquete.** Las recetas `ollama-model-tev1_0.8b.bb` y `ollama-model-llama32_1b.bb` producen los paquetes `ollama-model-tev1` y `ollama-model-llama32` (la porción posterior al guion bajo es `PV`, no parte de `PN`), pero `IMAGE_INSTALL` los referenciaba incluyendo la versión. Se verificó con `bitbake-layers show-recipes 'ollama*'` antes de lanzar el build y se corrigieron los nombres, evitando un fallo de resolución de dependencias.
- **Advertencia `do_unpack` por la variable `S` no definida.** La clase no declaraba `S`, por lo que BitBake asumió `${UNPACKDIR}/${BP}`, un directorio inexistente: el paquete de modelos se descomprime directamente en `UNPACKDIR` sin subdirectorio propio. Se añadió `S = "${UNPACKDIR}"` a la clase y la reconstrucción terminó sin advertencias.
- **Paquete `ollama-bin` duplicado** en el `IMAGE_INSTALL` de `jarvis-image.bb`. Sin efecto funcional, corregido por higiene.
- **Dos directorios de build coexistentes.** El build anterior residía en `~/yocto-work/build` y kas escribe en `~/edgeai-jarvis/build`, lo que llevó a inspeccionar artefactos obsoletos del 22 de octubre y concluir erróneamente que el build de hoy había fallado. Se verificó la fecha y el contenido del manifiesto para confirmar que la construcción sí había sido correcta. Pendiente: eliminar el directorio obsoleto.
- **El fetcher `file://` no verifica sumas de verificación**, a diferencia de un `SRC_URI` remoto con `SRC_URI[sha256sum]`. Dado que los paquetes de modelos se mantienen fuera del repositorio, un paquete corrupto o sustituido pasaría inadvertido. Resuelto con la tarea `do_verify_model` descrita arriba.

**Riesgos abiertos**

- `ollama-bin` produce un RPM de 16 MB frente a un tarball original de ~1.45 GB. Aunque la exclusión de los backends CUDA explica buena parte de la diferencia, conviene verificar el contenido de `/usr/lib/ollama/` en la primera prueba sobre hardware.
- La viabilidad de `tev1:0.8b` sobre ARM64 continúa sin verificar: el benchmark disponible (16/16 aciertos, 1.57 s de latencia media) se ejecutó sobre el host x86 y no es representativo del desempeño en la Raspberry Pi.
- El diseño de iluminación por habitaciones requiere PWM por canal, incompatible con el driver `gpio-led` actualmente configurado, que solo admite encendido y apagado. Pendiente evaluar un controlador PCA9685 por I2C.

**Referencias consultadas**

- Yocto Project Reference Manual — variables `S`, `UNPACKDIR`, `FILESEXTRAPATHS`, `IMAGE_ROOTFS_EXTRA_SPACE`: https://docs.yoctoproject.org/ref-manual/variables.html
- Yocto Project Development Tasks Manual — creación de clases propias: https://docs.yoctoproject.org/dev-manual/index.html
- Documentación de Ollama — API System One (`/v1/systemone`) y API de chat (`/api/chat`): https://docs.ollama.com/

---

## Entrada 7 — 24 de octubre de 2026

**Actividades realizadas**

- **Verificación del contenido del paquete de Ollama.** Se inspeccionó el RPM generado mediante `rpm2cpio | cpio -idmv` para confirmar que la receta instala las bibliotecas de inferencia y no únicamente el ejecutable. El paquete contiene `/usr/bin/ollama`, las bibliotecas `libllama.so`, `libllama-common.so`, `libmtmd.so` y `libomp.so` con sus enlaces de versión, los binarios auxiliares `llama-server` y `llama-quantize`, la unidad `ollama.service` y su archivo de preset. Con esto queda cerrado el riesgo registrado en la entrada anterior.
- **Decisión de diseño — iluminación con LEDs RGB.** Se sustituyó el LED único por LEDs RGB conectados mediante jumpers a una protoboard, con cada LED representando una habitación. Esto habilita color, intensidad y escenas, que el driver `gpio-led` no permite por admitir únicamente encendido y apagado.
- **Decisión de diseño — dos habitaciones en lugar de cuatro.** La Raspberry Pi 4 dispone de dos canales de PWM por hardware. Cuatro habitaciones en RGB exigirían doce canales, de los cuales diez se generarían por software desde espacio de usuario; el PWM por software depende del planificador de Linux y produce parpadeo perceptible bajo carga sostenida de CPU, que es justamente la condición durante una inferencia. Se limitó la implementación demostrable a dos habitaciones (seis canales, dos de ellos por hardware) y se documentaron cuatro como diseño objetivo, con el controlador PCA9685 por I2C identificado como vía de escalamiento.
- **Actualización del BSP.** Se retiró `dtoverlay=gpio-led`, que reservaba el GPIO17 en modo conmutado, y se incorporaron `dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4` para los dos canales de PWM por hardware y `dtparam=i2c_arm=on` para habilitar el bus I2C de cara a la incorporación futura del PCA9685.
- **Redacción de las secciones 6, 7 y 8 de la propuesta de diseño** (arquitectura del sistema, análisis de dependencias y estrategia de integración), correspondientes al 35 % de la rúbrica del documento. Las afirmaciones se respaldaron con datos extraídos del build y de la inspección de los paquetes; lo no verificado se consignó explícitamente como pendiente de la sesión de laboratorio.
- **Incorporación al repositorio** del análisis de dependencias (`docs/dependencias/analisis.md`), las secciones de la propuesta (`docs/propuesta/secciones-rol-b.md`) y la entrada 6 de esta bitácora.
- **Higiene del repositorio.** Se retiraron del control de versiones los artefactos generados por `bitbake -g` (`task-depends.dot` y `pn-buildlist`, 38 033 líneas en conjunto) por ser reproducibles con un comando, conservando el manifiesto de la imagen por su valor documental. Se añadieron al `.gitignore` las capas que kas clona dentro del repositorio (`bitbake`, `openembedded-core`, `meta-yocto`, `meta-openembedded`, `meta-raspberrypi`) y el directorio `build`, que sumaban decenas de gigabytes sin excluir.

**Productos obtenidos**

- `docs/propuesta/secciones-rol-b.md` — secciones 6, 7 y 8 de la propuesta de diseño.
- `docs/dependencias/analisis.md` — árbol de dependencias, peso por componente y dependencias en tiempo de ejecución.
- `yocto/kas/jarvis.yml` con el BSP actualizado para PWM de dos canales e I2C.
- `.gitignore` ampliado; repositorio sin archivos sin rastrear.
- Cuatro commits publicados en la rama `Daniel`: `cef4ee4`, `ababaa8`, `e386f80` y `f26bce2`.

**Datos medidos**

- Paquete `ollama-bin`: 16 MB comprimido, aproximadamente 49 MB una vez extraído, frente a un tarball oficial de 1.5 GB. La diferencia corresponde a los backends CUDA excluidos por la receta, inaplicables en la Raspberry Pi 4.

**Problemas encontrados y solución**

- **`rpm` no está disponible en el host.** La verificación del paquete con `rpm -qlp` devolvió una salida vacía porque Ubuntu no incluye la herramienta. Se resolvió con `rpm2cpio | cpio -idmv`, que extrae el paquete sin requerirla.
- **Capas de Yocto sin excluir del control de versiones.** kas clona las cinco capas base dentro del directorio del repositorio, y ninguna figuraba en `.gitignore`. Un `git add -A` habría intentado versionar decenas de gigabytes en un repositorio público. Se añadieron al `.gitignore` junto con el directorio de build.
- **Incompatibilidad entre el overlay configurado y el nuevo diseño de iluminación.** El driver `gpio-led` reserva el pin en modo conmutado y no expone control de ciclo de trabajo, por lo que resultaba incompatible con el control de color e intensidad. Se sustituyó por el overlay de PWM.

**Pendiente derivado**

- La imagen aún no incluye una receta para el PWM por software (`pigpio` o equivalente), necesaria para los cuatro canales que no cubren los dos de hardware.

**Referencias consultadas**

- Raspberry Pi — overlays del árbol de dispositivos (`pwm-2chan`, `i2c_arm`): https://github.com/raspberrypi/firmware/blob/master/boot/overlays/README
- Documentación del subsistema PWM de Linux (`/sys/class/pwm/`): https://docs.kernel.org/driver-api/pwm.html

---

## Pendientes para la siguiente sesión (laboratorio, con hardware)

**Verificación de la plataforma**
- Flashear `jarvis-image` y verificar arranque, Wi-Fi, servicios y RAM disponible.
- Comprobar que ambos modelos quedaron en `/usr/share/ollama/models` y que `ollama.service` los carga sin acceder a la MicroSD.
- Medir `/proc/diskstats` antes y después de varias órdenes para verificar la regla de oro R1.
- Desconectar la red y repetir las órdenes para verificar la regla de oro R2.

**Medición del carril de inferencia (riesgo principal)**
- Medir `tev1:0.8b` sobre ARM64: latencia por etapa, memoria residente y temperatura del SoC.
- Medir `llama3.2:1b` en las mismas condiciones y evaluar si ambos modelos residentes caben en memoria.
- Comparar contra el benchmark del host x86 (16/16 aciertos, 1.57 s de latencia media).

**Hardware**
- Cablear y probar el DHT22 por IIO.
- Cablear los dos LEDs RGB y probar el control por PWM.
- Incorporar a la imagen la receta del PWM por software.

**Entregables pendientes**
- Documento de instalación paso a paso (entrega del 9 de noviembre).
- Imagen release candidate y versión final.
