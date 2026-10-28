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

## Entrada 8 — 25 de octubre de 2026

**Actividades realizadas**

- **Contrato de la HAL de iluminación.** Se definió la interfaz `RoomLight` en `app/jarvis/hal_rooms.py` junto con el tipo `Color` (componentes normalizados de 0 a 1), la enumeración `Room`, una paleta nombrada de diez colores y cinco escenas predefinidas. La interfaz expone las operaciones primitivas `set` y `get` como métodos abstractos y construye sobre ellas los métodos derivados `turn_on`, `turn_off`, `dim`, `all_off` y `apply_scene`, de modo que una implementación nueva solo deba proveer las dos primitivas. El método `available()` permite que cada implementación declare qué habitaciones controla realmente, lo que distingue el diseño objetivo de la implementación demostrable.
- **Implementación simulada.** `app/jarvis/hal_fake.py` provee `FakeRoomLight` y `FakeTempHumSensor`, que cumplen los mismos contratos imprimiendo el estado en consola en lugar de operar hardware. Permiten al Rol A desarrollar y probar sin Raspberry Pi, y son también lo que utilizarán las pruebas automáticas en integración continua. `FakeRoomLight` registra cada cambio en un historial para que una prueba pueda verificar el efecto de un comando, y `FakeTempHumSensor.set_base()` permite forzar valores concretos para ejercitar reglas por umbral.
- **Corrección del acceso a GPIO.** La implementación real se reescribió para operar sobre la interfaz de caracteres del núcleo mediante `ioctl`, en lugar de la interfaz sysfs. Se detalla en la sección de problemas.
- **Implementación real de la iluminación.** `app/jarvis/hal_rpi_rooms.py` define `PwmRoomLight` con el reparto de canales documentado: el canal rojo de cada habitación sobre PWM por hardware con intensidad continua, y los canales verde y azul conmutados sobre líneas de propósito general. El parámetro `active_low` acomoda LEDs de ánodo o de cátodo común sin cambios en el resto del código.
- **Script de prueba de hardware.** `app/tools/probar_luces.py` ejecuta un diagnóstico previo —presencia del dispositivo de caracteres de GPIO, canales de PWM disponibles y overlays declarados— antes de tocar los periféricos, de modo que un problema de cableado o configuración se reporte en la primera pantalla. Recorre encendido y apagado por habitación, la paleta completa, una rampa de intensidad y las cinco escenas, verificando en cada paso que el estado reportado coincida con el solicitado.
- **Banco de medición del borde.** `app/tools/medir_borde.py` mide la latencia de cada etapa del router y del carril generativo con percentiles, evalúa la tasa de acierto contra un conjunto de veintiocho frases etiquetadas en español, reporta la distribución de confianza, registra memoria residente, temperatura y limitación térmica, y compara `/proc/diskstats` antes y después para verificar la regla R1. Utiliza únicamente la biblioteca estándar.

**Productos obtenidos**

- `app/jarvis/hal_rooms.py`, `app/jarvis/hal_fake.py`, `app/jarvis/hal_rpi_rooms.py`
- `app/tools/probar_luces.py`, `app/tools/medir_borde.py`

**Datos medidos**

- Prueba de la HAL en modo simulado: las veintiocho verificaciones de estado pasaron, incluidas las cinco escenas y la rampa de intensidad.
- Estructuras del ABI de GPIO verificadas contra la definición del núcleo: `gpio_v2_line_request` empaqueta a 592 bytes y `gpio_v2_line_values` a 16 bytes, con códigos de operación `0xC250B407` y `0xC010B40F`.

**Problemas encontrados y solución**

- **La implementación de GPIO usaba una interfaz obsoleta del núcleo.** La primera versión escribía en `/sys/class/gpio`, interfaz marcada como obsoleta desde Linux 4.8 y que en configuraciones recientes puede no estar compilada. La imagen corre el núcleo 6.18.33, por lo que existía un riesgo concreto de que el control de los LEDs no funcionara. No fue posible confirmarlo inspeccionando la configuración del núcleo porque `rm_work` elimina el directorio de trabajo tras empaquetar. Se evaluaron tres alternativas: incorporar los bindings de Python de libgpiod, descartada al comprobar que la receta de meta-oe solo expone opciones para pruebas, glib y dbus; invocar la herramienta `gpioset` como subproceso, descartada porque en libgpiod 2 la línea se libera al terminar el proceso; y operar directamente sobre la interfaz de caracteres mediante `ioctl`, que fue la adoptada. La implementación reserva las líneas en una única solicitud y los cambios posteriores son un `ioctl` sobre el descriptor devuelto.
- **Los archivos descargados no se habían copiado al repositorio.** Una verificación de importación detectó que los tres módulos permanecían en el directorio de descargas. Se adoptó la práctica de validar cada incorporación importando los módulos y ejecutando su prueba.
- **Rechazo al publicar en la rama remota.** El empuje fue rechazado por contener el remoto trabajo ausente localmente. Se verificó con `git log HEAD..origin/Daniel` que se trataba de un commit propio de una sesión anterior y se integró con `git pull --rebase`.

**Corrección a la entrada anterior**

- El pendiente sobre incorporar una receta de PWM por software queda sin efecto: la implementación adoptada descarta expresamente el PWM por software y resuelve los canales adicionales por conmutación.

**Referencias consultadas**

- Linux kernel — interfaz de caracteres de GPIO, ABI v2: https://docs.kernel.org/userspace-api/gpio/chardev.html
- Linux kernel — obsolescencia de la interfaz sysfs de GPIO: https://docs.kernel.org/userspace-api/gpio/sysfs.html

---

## Entrada 9 — 26 de octubre de 2026

**Actividades realizadas**

- **Retroalimentación del facilitador.** Se recibieron tres indicaciones que modifican el alcance del modelado: incorporar un agente que coordine explícitamente el modelo generativo y el modelo de decisión; verificar que el modelo elegido esté publicado en la biblioteca de Ollama; y expresar los diagramas del sistema como meta-modelos en SysML, abarcando las cuatro ramas del método (estructura, comportamiento, requisitos y paramétricos). Se señaló además la necesidad de un mapeo de actores, una lista de requerimientos derivada de necesidades y restricciones, y un concepto de operaciones.
- **Verificación del modelo en la biblioteca de Ollama.** Se confirmó que `tev1:0.8b` está publicado en la biblioteca oficial, con variantes de cuantización, y que existe también `tev1:4b`. Queda satisfecha la segunda indicación.
- **Meta-modelo del agente coordinador.** Se elaboró el modelo estructural y de comportamiento del agente: las clases `Agente`, `Orden`, `Ciclo`, `Percepción`, `Decisión`, `Herramienta`, `Invocación`, `Resultado`, `MemoriaDeTrabajo` y `PolíticaDeParada`, junto con las interfaces `MotorDeDecisión`, `GeneradorDeTexto` y `EstadoMundo`. El comportamiento se modeló como máquina de estados con cinco motivos de parada, todos ellos resultados legítimos del sistema. El documento distingue explícitamente qué decisiones son firmes por derivar de una restricción técnica y cuáles quedan a criterio del Rol A, que es quien implementa.
- **Meta-modelo de la plataforma.** Se modeló la plataforma en tres planos —construcción, distribución y ejecución— con catorce reglas normativas de composición y una tabla de trazabilidad que cruza propiedades afirmadas del sistema con el plano donde cada una se garantiza. Se incluyó una tabla de instanciación que asocia cada clase del meta-modelo con el componente real que la implementa, de modo que el modelo pueda verificarse contra el sistema construido.
- **Receta de la aplicación.** Se implementó `jarvis-app.bb`, que empaqueta el módulo de Python en el directorio de paquetes del intérprete, instala la configuración en `/etc/jarvis/config.yaml` y registra las unidades de servicio. Se eligió declarar `python3-modules` como dependencia en lugar de enumerar subpaquetes de la biblioteca estándar: Yocto la fragmenta y una omisión solo se manifiesta en ejecución, mientras que el costo de incluirla completa es despreciable frente al peso de los modelos.
- **Servicio de calentamiento de modelos.** Se implementó `jarvis-warmup.service` como unidad de ejecución única que permanece activa tras completarse, junto con el script que espera a que el servidor de inferencia responda y fuerza la carga de ambos modelos. Sin él, la primera orden del usuario pagaría el costo de leer los modelos desde la MicroSD, violando la regla R1 en el momento menos oportuno. El servicio de la aplicación declara dependencia de orden sobre él, de modo que no inicia hasta que los modelos están residentes.
- **Aislamiento del servicio de la aplicación.** La unidad declara restricciones de acceso: sistema de archivos en solo lectura con excepciones explícitas para el subsistema PWM, autorización individual de los dispositivos de caracteres de GPIO, y restricción de familias de direcciones de red.
- **Documentación de datos de desempeño de terceros.** Se localizaron mediciones publicadas de inferencia con llama.cpp sobre Raspberry Pi 4 que sugieren una velocidad de procesamiento de prompt del orden de 9 a 12 tokens por segundo para modelos de esta escala, con una regla empírica que relaciona la velocidad de generación con el tamaño del modelo. Se registró la discrepancia entre fuentes, que difieren en un orden de magnitud, y se decidió no tomar ninguna decisión de diseño hasta disponer de mediciones propias.

**Productos obtenidos**

- `docs/propuesta/metamodelo-agente.md`
- `docs/propuesta/metamodelo-plataforma.md`
- `yocto/meta-edgeai/recipes-jarvis/jarvis-app/jarvis-app.bb`
- `app/config.yaml`, `app/jarvis.service`, `app/jarvis-warmup.service`, `app/jarvis-warmup.sh`

**Datos medidos**

- Construcción de `jarvis-app`: 39 s sobre 4548 tareas, de las cuales 4527 se reutilizaron de la caché.
- Contenido del paquete verificado: módulos de Python en el directorio del intérprete, configuración, script de calentamiento, ambas unidades de servicio y el directorio de estado.

**Problemas encontrados y solución**

- **Desajuste entre el nombre del archivo de receta y el del paquete**, análogo al de la entrada 6. Se verificó con `bitbake-layers show-recipes` antes de construir.
- **La aplicación aún no existe.** La receta se escribió de modo que empaquete el paquete de Python completo tal como esté, para que la incorporación de los módulos del agente por parte del Rol A no requiera modificarla. La receta se construyó de forma aislada en lugar de incorporarla a la imagen, para validar el empaquetado sin dejar un servicio en estado fallido.
- **Elección del servidor HTTP.** La propuesta mencionaba FastAPI, que arrastra a la imagen un conjunto considerable de dependencias de Python, algunas con extensiones compiladas. Se señaló al Rol A que la biblioteca estándar basta para el único endpoint previsto y que, de requerirse un marco de trabajo, Flask tiene un árbol de dependencias sensiblemente menor.

**Referencias consultadas**

- Biblioteca de modelos de Ollama: https://ollama.com/library/tev1:0.8b
- Mediciones de inferencia con llama.cpp sobre Raspberry Pi 4: https://github.com/yosefdenham/pi-llm-bench

---

## Entrada 10 — 27 de octubre de 2026 (sesión de laboratorio)

Primera sesión con hardware. Los resultados obligaron a revisar decisiones de diseño sostenidas desde el inicio del proyecto, incluida la tesis central.

**Actividades realizadas**

- **Escritura de la imagen y arranque.** Se escribió `jarvis-image` sobre MicroSD y se verificó el arranque, el acceso por red, el estado del servidor de inferencia y la presencia de ambos modelos en la ruta que el servicio consulta.
- **Ejecución del banco de medición.** Se ejecutaron los veintiocho casos etiquetados del banco, sin errores, obteniendo latencia por etapa, tasa de acierto por dominio y por acción, distribución de confianza, memoria, temperatura y lecturas del medio de arranque.
- **Pruebas controladas de latencia.** Se diseñaron y ejecutaron cuatro consultas de tamaño de prompt creciente para aislar la variable que gobierna el tiempo de respuesta. El resultado es el hallazgo central de la sesión y se describe en los datos medidos.
- **Caracterización del modelo de decisión.** Se consultaron los parámetros del modelo instalado: arquitectura, número de parámetros, longitud de contexto y cuantización.
- **Verificación de la disponibilidad de cuantizaciones alternativas.** Se intentó obtener una variante de menor precisión numérica del modelo de decisión.
- **Medición del carril generativo con salida acotada.** Se repitió la medición limitando la longitud de la respuesta, que es la condición en que operará el sistema, para obtener una comparación representativa entre carriles.
- **Corrección de la configuración del servidor de inferencia** para evitar el descargue y recarga de modelos.
- **Verificación de los recursos de hardware expuestos por el núcleo.**

**Datos medidos**

*Equipo: Raspberry Pi 4 Model B Rev 1.5, núcleo 6.18.33-v8, 7806 MiB de memoria.*

**Hallazgo central — la latencia es lineal en el tamaño del prompt.** Tres consultas controladas con el modelo ya cargado:

| Prompt | Tokens de entrada | Tiempo |
|---|---:|---:|
| Tres criterios breves | 130 | 7,06 s |
| Seis criterios con descripción | 269 | 14,51 s |
| Idéntica, con contexto reducido a 1024 | 269 | 14,51 s |

El doble de tokens produce exactamente el doble de tiempo. La velocidad de procesamiento resulta de **18,5 tokens por segundo**, y el modelo lineal predice la etapa 1 del banco con un error inferior al 1 %: 14,55 s predichos contra 14,44 s medidos. La longitud de contexto configurada no tiene efecto alguno.

**Banco de veintiocho casos, catálogo de 42 operaciones con router de dos etapas:**

| Magnitud | Valor |
|---|---|
| Etapa 1 (p50 / p95) | 14,44 s / 14,77 s |
| Etapa 2 (p50 / p95) | 21,27 s / 26,72 s |
| Total por comando (p50 / p95 / máximo) | 35,60 s / 41,49 s / 41,75 s |
| Acierto de dominio | 22 de 28 (78,6 %) |
| Acierto de acción | 17 de 28 (60,7 %) |
| Confianza mínima en aciertos | 0,21 |

**Carril generativo:**

| Magnitud | Valor |
|---|---|
| Latencia sin acotar (p50) | 30,69 s |
| Con salida acotada a 40 tokens | 17,1 s efectivos |
| Procesamiento de prompt | 11,9 tokens/s |
| Generación | 2,6 tokens/s |

**Comparación de carriles con el diseño original:** decisión 35,60 s contra generativo 30,69 s. **El carril de decisión resultó más lento que el generativo.**

**Catálogo reducido.** Una sola etapa con ocho acciones y descripciones de dos palabras: 243 tokens, **13,36 s**, con confianza de 0,88 en la clasificación.

**Modelo de decisión instalado:** arquitectura qwen35, 772,85 M de parámetros, longitud de contexto 262144 con 8192 configurados, cuantización **Q8_0**.

**Recursos:** memoria disponible de 6108 a 4622 MiB, lo que sitúa el consumo de ambos modelos residentes en 1486 MiB. Temperatura del SoC de 38,9 °C en reposo a 70,1 °C tras la corrida completa.

**Regla R1:** 744 lecturas y 1254,52 MiB leídos del medio de arranque durante la corrida. **No se cumple.**

**Problemas encontrados y solución**

- **La tesis del proyecto quedó refutada por los datos.** El diseño afirmaba que separar el carril de decisión del generativo reduce la latencia de segundos a décimas. La medición muestra lo contrario con el diseño implementado. La causa es estructural: el modelo de decisión no genera tokens, pero debe procesar un prompt que contiene todos los criterios con sus descripciones, y el router de dos etapas paga ese costo dos veces. El carril generativo, en cambio, recibe un prompt breve. Se reformuló la tesis en términos de la ley medida, que es cuantitativa y permite predecir el costo de un diseño antes de implementarlo.
- **Incumplimiento de la regla R1.** La magnitud de la lectura detectada coincide aproximadamente con el tamaño del modelo generativo, lo que identificó la causa: el servidor descargaba un modelo para cargar el otro, pese a tener configurada la permanencia indefinida en memoria. Se añadió `OLLAMA_MAX_LOADED_MODELS=2` mediante un archivo de extensión de la unidad de servicio, y se verificó que la variable quedara efectivamente en el entorno del proceso. Queda pendiente reverificar la regla con el banco completo. La variable debe incorporarse a la receta para que sobreviva a la próxima escritura de la imagen.
- **No existe una cuantización de menor precisión del modelo de decisión.** Se intentó obtener la variante Q4_K_M, que habría reducido el tamaño en torno al 40 % y acelerado proporcionalmente, pero no está publicada. La única variante disponible es la instalada. Con ello queda establecido que el límite inferior de latencia de esta plataforma es del orden de diez a trece segundos por consulta, y no puede reducirse por configuración.
- **La primera medición controlada quedó contaminada.** La consulta con el prompt más corto se ejecutó inmediatamente después de reiniciar el servidor, por lo que incluyó el tiempo de carga del modelo y arrojó un valor mayor que una consulta con más tokens. Se descartó del análisis. El procedimiento de medición debe incluir siempre una consulta de calentamiento previa.
- **El subsistema PWM no está disponible en el sistema arrancado.** El directorio correspondiente aparece vacío, lo que indica que el overlay no se aplicó. La causa es que la imagen escrita corresponde a una construcción anterior a la modificación del BSP: el cambio que retiró el controlador de LED simple e incorporó los dos canales de modulación se realizó con posterioridad y la imagen nunca se reconstruyó. Se requiere reconstruir y reescribir antes de la próxima sesión de hardware.
- **Un comando interactivo bloqueó la sesión de trabajo.** La edición de la unidad de servicio mediante la herramienta de systemd abre un editor en pantalla completa; al no responder a la interrupción, el contenido del archivo se introdujo como órdenes en el intérprete. Se resolvió desde una segunda sesión eliminando el archivo temporal del editor y la copia incompleta de la unidad, que por residir en el directorio de configuración tenía precedencia sobre la original y podría haber impedido el arranque del servicio. El cambio se aplicó después mediante un archivo de extensión generado sin editor.
- **Limitaciones del intérprete de la imagen.** El sistema base no provee `pkill` y las utilidades admiten únicamente la forma estricta de sus opciones. Ambos detalles deben constar en el documento de instalación.
- **El reporte de memoria residente del banco es incorrecto.** El script consulta el proceso principal del servidor de inferencia, que no es el que mantiene los modelos en memoria, y reportó 33 MiB. El valor correcto se obtiene de la variación de memoria disponible del sistema. El script requiere corrección.
- **Procedimiento de escritura de la imagen ineficiente.** Se escribieron 10 GB en 22 minutos a 8 MB/s mediante copia directa, cuando la construcción genera un mapa de bloques que permite escribir únicamente los sectores con datos. El procedimiento correcto debe constar en el documento de instalación.

**Decisiones derivadas**

- Se elimina el router de dos etapas: duplica el costo fijo del prompt.
- El catálogo se reduce de 42 a nueve operaciones, con descripciones de dos palabras, conforme al presupuesto de 277 tokens que impone un objetivo de quince segundos.
- El sistema se replantea como asistente asíncrono sobre portal web, no como asistente conversacional. Una espera de quince segundos es tolerable al enviar un formulario e intolerable en una conversación.
- Se mantiene el modelo de decisión instalado por ausencia de alternativas, y el modelo generativo con longitud de salida acotada.
- La recuperación aumentada sobre documentos queda fuera de alcance: un contexto de mil quinientos tokens costaría más de dos minutos solo en procesamiento.

**Referencias consultadas**

- Documentación de Ollama — variables de entorno del servidor y gestión de modelos en memoria: https://docs.ollama.com/
- Ficha del modelo de decisión y cuantizaciones disponibles: https://ollama.com/library/tev1:0.8b

---

## Entrada 11 — 28 de octubre de 2026

**Actividades realizadas**

- **Reconstrucción de la imagen con el BSP corregido.** La sesión de laboratorio del día anterior reveló que el subsistema de modulación por ancho de pulso no estaba disponible en el sistema arrancado, porque la imagen escrita correspondía a una construcción anterior a la modificación del BSP. Se reconstruyó verificando previamente que la configuración declarara los dos canales de modulación y el bus serie de dos hilos, y se comprobó el archivo de configuración de arranque generado sin necesidad de escribir la tarjeta.
- **Incorporación a la receta de la variable que corrige la regla R1.** La variable que mantiene ambos modelos residentes se había aplicado en el laboratorio mediante un archivo de extensión de la unidad de servicio, que no sobrevive a una reescritura de la imagen. Se incorporó a la unidad que instala la receta, junto con un comentario que documenta la medición que motivó el cambio.
- **Corrección del banco de medición.** La función que reporta la memoria residente consultaba únicamente el proceso principal del servidor de inferencia, que no es el que mantiene los modelos cargados, y reportaba un valor dos órdenes de magnitud menor que el real. Se reescribió para recorrer la tabla de procesos sumando la memoria de todos los que pertenecen al servidor, excluyendo el proceso del propio banco para evitar un falso positivo.
- **Actualización del banco al catálogo del aula.** Se sustituyó el conjunto de frases etiquetadas del catálogo doméstico anterior por veintisiete frases del dominio académico, tres por cada una de las nueve operaciones, con vocabulario variado para que la tasa de acierto sea representativa y no un eco de las descripciones del catálogo. Se eliminó la segunda etapa de clasificación y se añadió una sección que recalcula la velocidad de procesamiento y la compara contra el valor de referencia obtenido en la sesión anterior.
- **Recetas del punto de acceso inalámbrico.** Se implementó la receta que configura el equipo para emitir su propia red y asignar direcciones a los clientes: configuración del emisor con cifrado, asignación de direcciones en el rango local con resolución del nombre del portal, dirección estática de la interfaz y una unidad de servicio que agrupa el arranque de ambos componentes. Se declaró conflicto explícito con la receta de cliente inalámbrico, dado que una misma interfaz no puede operar en ambos modos.
- **Script de escritura segura de la imagen.** Se implementó un script que verifica, antes de escribir un solo byte, que el destino exista, sea un dispositivo de bloque y no un archivo regular, sea removible, no coincida con el disco del sistema y tenga capacidad suficiente, y que exige al operador escribir el nombre del dispositivo como confirmación. Utiliza el mapa de bloques cuando está disponible.
- **Revisión técnica de la propuesta de diseño redactada por el Rol A.** Se identificaron ocho imprecisiones factuales y dos omisiones respecto de lo solicitado por el facilitador, descritas en la sección de problemas.
- **Elaboración de los tres diagramas faltantes en notación SysML.** Se añadieron el diagrama de requisitos, con relaciones de derivación, satisfacción y verificación entre necesidades, requisitos funcionales, restricciones medidas y bloques del sistema; el diagrama paramétrico, que expresa la ley de latencia, la composición del prompt y el presupuesto del catálogo como bloques de restricción encadenados; y el diagrama de bloques internos, con puertos nombrados y protocolos explícitos en cada conexión. Con ello quedan cubiertas las cuatro ramas del método solicitadas: estructura, comportamiento, requisitos y paramétricos.
- **Ampliación de la bibliografía** de cuatro a quince referencias, correspondientes a las fuentes efectivamente consultadas durante el desarrollo.
- **Generación del documento entregable.** Se renderizaron los ocho diagramas a formato vectorial, se verificó que todos compilaran sin error, y se produjo un documento autocontenido con los diagramas embebidos y estilos de impresión, de modo que la exportación no dependa de servicios externos ni de la instalación de un sistema de composición tipográfica.

**Productos obtenidos**

- `jarvis-image` reconstruida con los overlays de modulación y bus serie.
- `yocto/meta-edgeai/recipes-connectivity/wifi-ap/` con la receta y sus cuatro archivos de configuración.
- `yocto/meta-edgeai/recipes-ai/ollama/files/ollama.service` con la variable de modelos simultáneos.
- `yocto/scripts/flashear.sh` — escritura verificada de la imagen.
- `app/tools/medir_borde.py` actualizado al catálogo del aula, con validación de la ley de latencia.
- `docs/propuesta/propuesta-jarvis.md` — propuesta corregida con los cuatro tipos de diagrama.
- Documento entregable con los diagramas embebidos.

**Datos medidos**

- Reconstrucción de la imagen: 14 min 13 s sobre 6428 tareas, de las cuales 32 requirieron reejecución.
- Construcción de la receta del punto de acceso: 4 min 40 s sobre 4572 tareas.
- Escritura de la imagen por copia directa: 10 GB en 22 minutos a 8 MB/s. El mapa de bloques indica que solo 2,6 GiB de los 9,7 GiB contienen datos, un 27 %, de modo que la escritura por mapa reduciría el tiempo a aproximadamente un cuarto.
- Los ocho diagramas de la propuesta compilan sin error.

**Problemas encontrados y solución**

- **Imprecisiones en la propuesta de diseño.** La revisión identificó: confusión entre la precisión publicada del modelo y el umbral de confianza, presentados como el mismo valor; descripción de los modelos como archivos de pesos sueltos, cuando se incorporan en el formato nativo del servidor; afirmación de que el servidor de inferencia se compila desde fuente, cuando se empaqueta precompilado por decisión explícita de diseño; presentación de la regla R1 como satisfecha, cuando la medición detectó lecturas del medio de arranque y la corrección está pendiente de verificación; nombres de recetas que no corresponden a los del repositorio, con los dos paquetes de modelo representados como uno solo; descripción de las tres componentes de color como gobernadas por modulación de hardware, cuando solo la componente roja lo está; atribución al Rol A de la campaña de medición y de los meta-modelos de plataforma, realizados por el Rol B; e inclusión de un valor de confianza no proveniente de ninguna medición en un diagrama de secuencia. Todas se corrigieron contrastando contra el repositorio y el reporte de medición.
- **Omisión de dos ramas del método de modelado.** La propuesta incluía diagramas de comportamiento y de estructura, pero carecía de diagrama de requisitos y de diagrama paramétrico, ambos solicitados explícitamente por el facilitador. Se elaboraron a partir de la información ya disponible: la trazabilidad entre necesidades y requisitos, y la ley de latencia medida como restricción paramétrica que gobierna el presupuesto del catálogo.
- **El diagrama de bloques no constituía una vista interna.** El documento presentaba un diagrama de flujo en lugar de un diagrama de bloques internos con puertos e interfaces. Se elaboró la vista interna, que expone una restricción invisible en la vista de definición: la conexión de la componente roja al puerto de modulación por hardware y de las componentes verde y azul al puerto conmutado.
- **Instalación fallida de herramientas de composición tipográfica.** El gestor de paquetes de Node rechazó la instalación global por falta de privilegios, y el conversor de documentos no encontró el archivo de entrada por ejecutarse desde un directorio distinto. Se descartó esa vía: en su lugar se renderizaron los diagramas a formato vectorial y se generó un documento autocontenido que se exporta desde el propio navegador, sin dependencias adicionales.
- **La herramienta de escritura de imágenes crea un archivo regular cuando el destino no existe.** Al ejecutar la escritura sin la tarjeta insertada, la herramienta no falló: creó un archivo con el nombre del dispositivo y lo llenó hasta agotar el espacio del sistema de archivos en memoria. El comportamiento se repitió al ejecutar un bloque de comandos donde la verificación y la escritura figuraban en líneas separadas, de modo que la verificación informó el error pero no impidió la ejecución del comando siguiente. Se resolvió con el script de escritura verificada, que aborta ante cualquier verificación fallida.

**Pendientes derivados**

- Reverificar la regla R1 con el banco completo y la configuración corregida.
- Incorporar el punto de acceso a la lista de instalación de la imagen, decisión que debe tomarse con el hardware disponible, dado que sustituye el modo cliente de la interfaz inalámbrica y modifica la vía de acceso para desarrollo.
- Trasladar los tres diagramas nuevos a los documentos de meta-modelo del repositorio, que hoy los expresan en notación de clases.

**Referencias consultadas**

- OMG. *Systems Modeling Language (SysML): diagramas de requisitos, paramétricos y de bloques internos.* https://www.omg.org/spec/SysML/
- hostapd. *Documentación de configuración del punto de acceso IEEE 802.11.* https://w1.fi/hostapd/
- dnsmasq. *Manual de configuración.* https://thekelleys.org.uk/dnsmasq/doc.html
- bmaptool. *Escritura de imágenes mediante mapa de bloques.* https://github.com/intel/bmap-tools

---

## Pendientes

**Requieren hardware (sesión de laboratorio)**

- Escribir la imagen reconstruida con el BSP corregido y verificar que el subsistema de modulación por ancho de pulso aparece expuesto por el núcleo.
- Cablear el sensor ambiental y las dos zonas de iluminación; ejecutar la prueba de hardware.
- Reverificar la regla R1 con el banco completo y la configuración corregida del servidor de inferencia.
- Verificar la regla R2 desconectando el equipo de toda red externa.
- Repetir el banco con el catálogo de nueve operaciones y calibrar el umbral de confianza con la distribución resultante.
- Decidir la incorporación del punto de acceso a la imagen, que sustituye el modo cliente de la interfaz inalámbrica.

**No requieren hardware**

- Documento de instalación paso a paso, entregable del 9 de noviembre. Debe incluir el procedimiento de escritura por mapa de bloques, la verificación previa del dispositivo de destino y las limitaciones del intérprete de órdenes de la imagen.
- Trasladar los diagramas de requisitos, paramétrico y de bloques internos a los documentos de meta-modelo del repositorio.
- Incorporar la aplicación a la lista de instalación de la imagen cuando el Rol A entregue el punto de entrada.
- Imagen candidata a liberación y versión final.
