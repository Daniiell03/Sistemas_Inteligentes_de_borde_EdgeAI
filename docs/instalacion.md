# Generación e instalación de la imagen del sistema

**Proyecto II — Sistemas Inteligentes de Borde (EdgeAI)**
Asistente de aula «Jarvis» sobre Raspberry Pi 4 Model B
Taller de Sistemas Embebidos, II Semestre 2026 — Tecnológico de Costa Rica

---

## 1. Alcance y resultado esperado

Este documento describe el procedimiento completo para sintetizar la imagen de Linux del sistema con el flujo de trabajo de Yocto Project, escribirla en el medio de arranque de la Raspberry Pi, conectar los periféricos y verificar que el sistema quedó operativo.

El procedimiento produce un único artefacto: una imagen comprimida de aproximadamente 2,4 GiB que, descomprimida, ocupa 9,7 GiB y contiene el sistema operativo, el servidor de inferencia, los dos modelos de lenguaje ya incorporados y la aplicación del asistente con sus dependencias.

La instalación se considera correcta cuando, tras el primer arranque y sin intervención alguna, se cumplen simultáneamente estas cinco condiciones:

1. El núcleo expone el subsistema de modulación por ancho de pulso y el de instrumentación.
2. Los tres servicios del sistema —servidor de inferencia, calentamiento de modelos y aplicación— se encuentran activos.
3. Los dos modelos están residentes en memoria con permanencia indefinida.
4. La aplicación responde en el puerto 8000 desde cualquier equipo de la misma red.
5. El medio de arranque no registra lecturas durante la operación, lo que verifica la regla R1.

La sección 10 detalla los comandos de cada comprobación.

---

## 2. Requisitos del equipo de desarrollo

### 2.1 Sistema operativo y herramientas

El procedimiento se desarrolló y verificó sobre Ubuntu 25.10 en arquitectura x86-64. Se requieren:

| Herramienta | Versión verificada | Propósito |
|---|---|---|
| `git` | cualquiera reciente | obtención del repositorio y de las capas |
| `kas` | 5.5 | orquestación del flujo de Yocto |
| `bitbake` | 2.18 | lo obtiene `kas`, no se instala aparte |
| `python3` | 3.10 o superior | scripts auxiliares |
| `bmaptool` | cualquiera | escritura por mapa de bloques |
| `ollama` | **0.40.6 exactamente** | preparación de los modelos |

La versión de Ollama en el equipo de desarrollo debe coincidir con la de la imagen. El formato del almacén de modelos cambia entre versiones, y un modelo preparado con una versión distinta puede no cargarse en el equipo de destino.

### 2.2 Espacio en disco

El requisito es considerablemente mayor que el tamaño de la imagen resultante, y conviene dimensionarlo antes de empezar: quedarse sin espacio a mitad de una construcción de siete horas obliga a repetirla.

| Componente | Tamaño medido | Ubicación |
|---|---:|---|
| Árbol de construcción | 24 GB | `build/` dentro del repositorio |
| Caché de descargas | varía | `$DL_DIR` |
| Caché de estados | varía | `$SSTATE_DIR` |

Los 24 GB del árbol de construcción corresponden a una configuración con `rm_work` activo, que elimina los directorios de trabajo intermedios conforme se completa cada receta. Sin esa opción el árbol crece varias veces ese tamaño.

**Se recomienda disponer de al menos 120 GB libres** contando las cachés.

### 2.3 Memoria

La construcción con el paralelismo por omisión agota la memoria del equipo de desarrollo durante la compilación de la extensión en Rust que incorpora una de las dependencias de la aplicación. La configuración de capas fija explícitamente dos tareas simultáneas y dos hilos de compilación, lo que alarga la construcción pero la hace reproducible en equipos con memoria limitada. No se debe elevar ese valor sin comprobar la memoria disponible.

### 2.4 Variables de entorno

Las cachés de descargas y de estados deben residir **fuera del árbol de construcción**, de modo que sobrevivan a su eliminación y puedan compartirse entre construcciones. Se definen en el perfil del usuario:

```bash
export DL_DIR=~/yocto-cache/downloads SSTATE_DIR=~/yocto-cache/sstate
```

La configuración de capas sitúa además la base de datos del servicio de resúmenes dentro del directorio de la caché de estados, de modo que ambas se conservan juntas.

---

## 3. Obtención del repositorio y preparación de los modelos

### 3.1 Repositorio

```bash
git clone <url-del-repositorio> edgeai-jarvis
cd edgeai-jarvis
```

Las capas de Yocto no se incluyen en el repositorio: las obtiene `kas` a partir de la configuración, fijadas a la rama `wrynose` y a los commits registrados en el archivo de bloqueo.

### 3.2 Por qué los modelos se incorporan a la imagen

Dos requisitos del sistema obligan a ello:

- **R1 — ningún modelo se lee del medio de arranque después del arranque.** Los modelos se cargan una sola vez, durante el calentamiento, y permanecen residentes en memoria.
- **R2 — ninguna operación requiere conectividad externa.** Descargar un modelo en el primer arranque violaría esta regla y además dejaría el sistema inoperante en un aula sin red.

Incorporarlos a la imagen es lo que permite que el sistema arranque, cargue ambos modelos y opere de forma completamente autónoma.

### 3.3 Preparación

El script empaqueta un modelo del almacén local de Ollama —su manifiesto y todos los blobs que referencia— en un archivo que la receta incorpora a la imagen. El paquete queda en `$DL_DIR/jarvis-models/`, fuera del repositorio, porque pesa cientos de megabytes.

```bash
yocto/scripts/preparar-modelo.sh tev1:0.8b
yocto/scripts/preparar-modelo.sh llama3.2:1b
```

El primero es el modelo de decisión, que clasifica las órdenes contra el catálogo de operaciones mediante la interfaz de preguntas tipadas. El segundo es el modelo generativo, empleado únicamente por la operación de resumen.

---

## 4. La configuración de capas

El archivo `yocto/kas/jarvis.yml` declara la totalidad de la síntesis. Conviene entender qué hace cada bloque antes de modificarlo.

### 4.1 Objetivo y capas

```yaml
machine: raspberrypi4-64
distro: poky
target: jarvis-image
```

Las capas provienen de `openembedded-core`, `meta-yocto`, `meta-openembedded` —de la que se emplean `meta-oe`, `meta-python`, `meta-multimedia` y `meta-networking`— y `meta-raspberrypi`, todas en la rama `wrynose`, más la capa propia del proyecto `yocto/meta-edgeai`, que reside en este mismo repositorio.

### 4.2 Parámetros de la imagen

| Variable | Valor | Motivo |
|---|---|---|
| `INIT_MANAGER` | `systemd` | las unidades de servicio del proyecto lo requieren |
| `IMAGE_FSTYPES` | `wic.bz2 wic.bmap` | el mapa de bloques es lo que hace rápida la escritura |
| `INHERIT` | `rm_work` | contiene el tamaño del árbol de construcción |
| `BB_NUMBER_THREADS` | `2` | evita el agotamiento de memoria |
| `PARALLEL_MAKE` | `-j 2` | idem |
| `ENABLE_UART` | `1` | consola serie para diagnóstico de arranque |

### 4.3 Los overlays del árbol de dispositivos

Este es el punto del archivo donde es más fácil equivocarse, y el error no produce ningún mensaje:

```
RPI_EXTRA_CONFIG = "dtoverlay=dht11,gpiopin=4\ndtparam=i2c_arm=on\ndtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4"
RPI_KERNEL_DEVICETREE_OVERLAYS:append = " overlays/dht11.dtbo overlays/pwm-2chan.dtbo"
```

**Declarar un overlay y desplegarlo son dos pasos independientes.** La primera variable escribe líneas en el archivo de configuración de arranque que lee el firmware; la segunda determina qué archivos binarios de overlay se compilan y se copian a la partición de arranque. Con solo la primera, el firmware solicita dos archivos que no existen, continúa el arranque sin señalar error alguno, y los periféricos simplemente no aparecen. El único síntoma es un directorio vacío en el subsistema correspondiente.

La segunda variable se declara con asignación por anexión para conservar la lista de overlays por omisión del BSP, que de otro modo se perdería.

### 4.4 Advertencia sobre las credenciales

```
EXTRA_IMAGE_FEATURES += "ssh-server-openssh allow-empty-password empty-root-password allow-root-login"
```

La imagen permite acceso remoto como administrador **sin contraseña**. Es apropiado para desarrollo y para la demostración en un entorno controlado, e inaceptable para cualquier despliegue real. Retirar estas opciones antes de un uso fuera del laboratorio.

---

## 5. Síntesis de la imagen

### 5.1 Ejecución

```bash
cd ~/edgeai-jarvis
nohup kas build yocto/kas/jarvis.yml > ~/build-$(date +%Y%m%d-%H%M).log 2>&1 &
```

Se lanza desatendido y con registro a archivo porque la primera construcción dura varias horas y no debe depender de que la terminal siga abierta.

Para seguir el avance:

```bash
tail -n 15 $(ls -t ~/build-*.log | head -n 1)
```

### 5.2 Tiempos medidos

| Situación | Tareas | De caché | Duración |
|---|---:|---:|---:|
| Primera construcción | 10 278 | 6 792 | 7 h 43 min |
| Reconstrucción tras cambiar la aplicación | 10 278 | 10 243 | 14 min |

La primera construcción está dominada por la compilación de una extensión en Rust que incorpora una de las dependencias de la aplicación, y que no puede reutilizarse de la caché de estados la primera vez. Las reconstrucciones posteriores, cuando solo cambian archivos de Python, se resuelven casi por completo desde la caché.

### 5.3 Resultado

```
build/tmp/deploy/images/raspberrypi4-64/
    jarvis-image-raspberrypi4-64.rootfs.wic.bz2    → imagen comprimida
    jarvis-image-raspberrypi4-64.rootfs.wic.bmap   → mapa de bloques
```

Ambos son enlaces simbólicos al archivo fechado correspondiente a la construcción más reciente.

---

## 6. Verificación del artefacto contra el commit

**Este paso no es opcional.** Una sesión de laboratorio completa se perdió por escribir una imagen que había sido construida antes del cambio que supuestamente contenía: la receta era correcta y el artefacto no, y el síntoma —un periférico ausente— fue indistinguible de un error de configuración.

### 6.1 Comparación de fechas

```bash
ls -lt --time-style=+'%Y-%m-%d %H:%M' build/tmp/deploy/images/raspberrypi4-64/*.wic.bz2 | head -n 2
git log -n 1 --format='%h  %cd  %s' --date=format:'%Y-%m-%d %H:%M'
```

La imagen debe ser **posterior** al commit que se desea verificar. Si es anterior, la construcción no incorporó el cambio y debe repetirse.

### 6.2 Inspección del contenido

La comparación de fechas es necesaria pero no suficiente. La comprobación directa consiste en buscar dentro del paquete instalado alguna cadena que solo exista desde el cambio:

```bash
RPM=$(ls -t build/tmp/deploy/rpm/*/jarvis-app-*.rpm | head -n 1)
mkdir -p ~/verif-imagen && cd ~/verif-imagen
rpm2cpio "$HOME/edgeai-jarvis/$RPM" | cpio -idm --quiet
grep -rc "<cadena introducida por el cambio>" .
```

Si la cadena aparece, el artefacto corresponde al código esperado.

---

## 7. Instalación física del hardware

### 7.1 Zonas de iluminación

El sistema controla dos zonas, cada una con un diodo emisor de tres colores de **cátodo común**. Los tres ánodos se alimentan desde líneas del procesador a través de resistencias de limitación, y el cátodo retorna a la tierra común del conjunto.

| Zona | Canal rojo | Canal verde | Canal azul | Cátodo |
|---|---|---|---|---|
| Frente | GPIO12 (modulación, canal 0) | GPIO16 | GPIO20 | GND |
| Fondo | GPIO13 (modulación, canal 1) | GPIO21 | GPIO26 | GND |

Consideraciones:

- **Los diodos no llevan alimentación propia.** La corriente la entrega la propia línea del procesador. El cuarto terminal no queda sin conectar: es el retorno común de los otros tres.
- **Cada ánodo requiere su resistencia de limitación en serie**, del orden de 220 a 330 Ω. Conectar un ánodo directamente a una línea del procesador puede dañar el puerto.
- **Todas las tierras son la misma.** El cátodo de ambos diodos y la tierra del sensor retornan al mismo punto.
- Solo el canal rojo admite intensidad continua, porque está asignado a los dos canales de modulación por ancho de pulso del procesador. Los canales verde y azul son conmutados: se encienden o se apagan por encima de un umbral. Esta limitación condiciona el diseño de las escenas y debe tenerse presente al interpretar su comportamiento.

### 7.2 Sensor ambiental

| Terminal del sensor | Conexión |
|---|---|
| 1 — alimentación | 3,3 V (pin 1 del conector) |
| 2 — datos | GPIO4 (pin 7 del conector) |
| 3 — sin usar | — |
| 4 — tierra | GND (pin 9 del conector) |

Entre la línea de datos y la alimentación de 3,3 V debe instalarse una **resistencia de elevación de 10 kΩ**. Los módulos de tres terminales la incorporan en su placa; el sensor desnudo de cuatro terminales no.

> **Advertencia.** La resistencia de elevación debe conectarse a 3,3 V y nunca a 5 V. Las líneas del procesador no toleran 5 V y el puerto resultaría dañado de forma permanente.

---

## 8. Escritura de la imagen en el medio de arranque

### 8.1 Procedimiento

```bash
cd ~/edgeai-jarvis
sudo yocto/scripts/flashear.sh /dev/sdX
```

El script verifica, antes de escribir un solo byte, que el destino exista y sea un dispositivo de bloque, que sea removible, que no sea el disco donde está montada la raíz del sistema, que su capacidad sea suficiente, y que la imagen y su mapa de bloques existan. Cualquier verificación fallida aborta la ejecución completa.

La escritura emplea el mapa de bloques, de modo que solo se transfieren los sectores con datos: aproximadamente 2,6 GiB de los 9,7 GiB de la imagen descomprimida. El tiempo medido es de **7 min 30 s**, frente a los 22 minutos que tomaba la copia directa.

### 8.2 Expulsión

```bash
sudo eject /dev/sdX
```

### 8.3 Advertencia sobre el directorio temporal

**La imagen no puede descomprimirse en el directorio temporal del sistema.** Es un sistema de archivos en memoria de 3,6 GiB y la imagen descomprimida ocupa 9,7 GiB. Cualquier intento de inspeccionar su contenido descomprimiéndola allí agota el espacio disponible a mitad de la operación. Si se necesita inspeccionar la imagen, debe hacerse en una ubicación respaldada por disco.

---

## 9. Primer arranque

Insertar el medio de arranque en el equipo y conectar la alimentación. En el primer arranque ocurre lo siguiente, sin intervención:

1. El firmware lee la configuración de arranque y carga los overlays del árbol de dispositivos, exponiendo el subsistema de modulación y el de instrumentación.
2. Arranca el servidor de inferencia.
3. El servicio de calentamiento espera a que el servidor responda y fuerza la carga de ambos modelos con una consulta trivial. Con la permanencia indefinida configurada, quedan residentes.
4. Arranca la aplicación, que construye la capa de abstracción de hardware, abre su almacén y comienza a atender peticiones.

**Esperar dos minutos antes de verificar.** La carga de los dos modelos desde el medio de arranque toma ese orden de tiempo, y es la única ocasión en que se leen.

> El equipo **carece de reloj de tiempo real**. Arranca con una fecha falsa y la corrige cuando el servicio de sincronización horaria consigue contactar una fuente. El salto puede ocurrir en cualquier momento de los primeros minutos, de modo que las marcas de tiempo del registro de arranque no son fiables hasta que ocurre. El script de calentamiento contempla esta situación con reintentos.

---

## 10. Verificación posterior a la instalación

Conectar al equipo y ejecutar la comprobación completa:

```bash
ssh root@<direccion> '
  echo "=== overlays ==="
  ls /sys/class/pwm/
  ls /sys/bus/iio/devices/
  echo "=== servicios ==="
  systemctl is-active ollama jarvis-warmup jarvis
  echo "=== modelos ==="
  ollama ps
  echo "=== calentamiento ==="
  journalctl -u jarvis-warmup --no-pager | tail -n 15
'
```

Resultado esperado:

| Comprobación | Resultado correcto |
|---|---|
| Subsistema de modulación | `pwmchip0` |
| Subsistema de instrumentación | `iio:device0` |
| Servicios | los tres informan `active` |
| Modelos | ambos listados con permanencia `Forever` |
| Calentamiento | la última línea informa `modelos residentes: 2` |

Verificación de la aplicación:

```bash
curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://<direccion>:8000/
```

Debe responder `HTTP 200`.

El registro de la aplicación debe indicar además que se construyó la capa de abstracción **real** y no la simulada:

```bash
ssh root@<direccion> 'journalctl -u jarvis --no-pager | tail -n 10'
```

La línea esperada es `HAL real: PWM, GPIO y sensor del nucleo`. Si informa modo simulado, los overlays no están desplegados o el cableado no es correcto.

### 10.1 Verificación de la regla R1

```bash
ssh root@<direccion> 'grep mmcblk0 /proc/diskstats'
```

Se toma una lectura, se opera el sistema durante varios minutos y se repite. El tercer campo —lecturas completadas— y el sexto —sectores leídos— no deben crecer de forma apreciable. Las mediciones del proyecto registran **cero lecturas y cero sectores** durante corridas completas con ambos modelos residentes.

---

## 11. Acceso al equipo

### 11.1 Dirección

El equipo anuncia su nombre local `jarvis.local` mediante descubrimiento multicast. **Esta resolución no es fiable desde todos los sistemas operativos**: se verificó que algunos equipos con Windows no lo resuelven de forma consistente pese a encontrarse en la misma red. Se recomienda obtener la dirección numérica —desde la consola serie, desde el punto de acceso, o con un barrido de la red— y emplearla como procedimiento principal.

La aplicación escucha en todas las interfaces, de modo que es accesible desde cualquier equipo de la misma red en el puerto 8000. Esto es lo que permite que el profesor y los estudiantes operen simultáneamente desde dispositivos distintos.

### 11.2 Claves de servidor

Cada escritura de una imagen nueva genera claves de servidor nuevas. El cliente de acceso remoto rechazará la conexión señalando un posible ataque de interposición. Es el comportamiento esperado y se resuelve retirando la entrada anterior:

```bash
ssh-keygen -R jarvis.local
ssh-keygen -R <direccion>
```

---

## 12. Operación y configuración

### 12.1 Ubicación de los datos

| Ruta | Contenido |
|---|---|
| `/var/lib/jarvis/jarvis.db` | base de datos única: tareas, entregas, agenda, ambiente y bitácora de decisiones |
| `/var/lib/jarvis/portal_config.json` | parámetros de operación de la aplicación |
| `/var/lib/jarvis/uploads/` | archivos adjuntos a las entregas |
| `/usr/share/ollama/models/` | los dos modelos |

### 12.2 Por qué la configuración no reside en la jerarquía de configuración

La unidad de servicio declara protección estricta del sistema de archivos, que deja toda la jerarquía de configuración en solo lectura para el proceso. Un archivo que la propia aplicación debe poder reescribir no puede residir allí, de modo que se ubica en el directorio de estado del servicio.

### 12.3 Parámetros por variables de entorno

La unidad de servicio fija los parámetros de red y de ubicación de datos. Se prefieren variables de entorno a un archivo de configuración propio: son cinco valores, la unidad ya los declara, y leer un formato estructurado exigiría una dependencia adicional en la imagen sin ninguna ganancia.

| Variable | Valor por omisión |
|---|---|
| `JARVIS_HOST` | `0.0.0.0` |
| `JARVIS_PORT` | `8000` |
| `JARVIS_DATA` | `/var/lib/jarvis` |
| `JARVIS_DB` | `<JARVIS_DATA>/jarvis.db` |
| `JARVIS_CONFIG` | `/var/lib/jarvis/portal_config.json` |

### 12.4 Credenciales de la aplicación

El servicio informa en su registro, al arrancar, la clave de acceso del rol docente. Se modifica en el archivo de configuración. No confundirla con el acceso remoto como administrador, que en esta imagen no tiene contraseña.

---

## 13. Problemas conocidos y limitaciones

### 13.1 Utilidades reducidas del sistema de destino

La imagen emplea una implementación compacta de las utilidades del sistema, cuyas opciones no coinciden con las de las versiones completas. En particular:

- `head -5` falla; la forma correcta es `head -n 5`. Lo mismo aplica a `tail`.
- `pkill` no existe.
- `timeout` no existe.

Esto afecta a cualquier script que se escriba para ejecutarse en el equipo de destino, y es una causa frecuente de fallos que parecen del programa y son de la utilidad.

### 13.2 Exclusividad de las líneas de entrada y salida

Las líneas del procesador y los canales de modulación son recursos exclusivos: el proceso que los reserva primero los retiene. En consecuencia, **el servicio de la aplicación y cualquier herramienta de prueba que acceda al hardware no pueden ejecutarse simultáneamente.** El segundo en arrancar falla al reservarlos. Por la misma razón la aplicación corre con un único trabajador.

### 13.3 Ausencia de reloj de tiempo real

Descrita en la sección 9. Además de afectar al calentamiento, implica que las marcas de tiempo de los primeros segundos de cada arranque corresponden a una fecha falsa, lo que puede desconcertar al leer el registro del sistema.

### 13.4 Permanencia de los modelos

El cumplimiento de la regla R1 depende de dos variables de entorno del servidor de inferencia:

```
OLLAMA_MAX_LOADED_MODELS = 2
OLLAMA_KEEP_ALIVE = -1
```

Con un solo modelo admitido simultáneamente, cambiar de carril obliga a releer el otro desde el medio de arranque, lo que viola R1 en el momento menos oportuno. El script de calentamiento comprueba explícitamente que ambos quedaron residentes y lo advierte en el registro si no es así.

### 13.5 Sensor ambiental

El controlador del núcleo decodifica la trama del sensor midiendo tiempos por software, lo que lo hace sensible a la calidad de la línea de datos. Un sensor degradado puede entregar tramas íntegras —con su suma de comprobación válida— y valores imposibles. La capa de abstracción filtra las lecturas contra el rango físicamente posible de un aula, hace caducar la última lectura válida y expone un diagnóstico con el último valor rechazado y los contadores de fallos separados por tipo. Si la aplicación informa que no dispone de una lectura válida, debe consultarse ese diagnóstico antes de suponer un problema de software.

---

## Anexo A — Dependencias incorporadas a la imagen

Además del servidor de inferencia y los modelos, la imagen incorpora los siguientes paquetes para la capa de aplicación:

| Paquete | Versión |
|---|---|
| `python3-fastapi` | 0.135.3 |
| `python3-uvicorn` | 0.44.0 |
| `python3-starlette` | 1.0.0 |
| `python3-pydantic` | — |
| `python3-pydantic-core` | 2.41.5 |
| `python3-python-multipart` | 0.0.26 |
| `python3-itsdangerous` | 2.2.0 |
| `python3-anyio` | 4.13.0 |
| `python3-sniffio` | 1.3.1 |
| `python3-fastapi-cli` | — |
| `jarvis-app` | 1.0 |

`python3-pydantic-core` incluye una extensión compilada en Rust y es la responsable de la duración de la primera construcción.

---

## Anexo B — Lista de comprobación rápida

Para una instalación desde cero sobre un equipo de desarrollo ya preparado:

1. `git clone` del repositorio.
2. `yocto/scripts/preparar-modelo.sh` para cada uno de los dos modelos.
3. `kas build yocto/kas/jarvis.yml`, desatendido y con registro.
4. Verificar el artefacto contra el commit: fecha e inspección del contenido.
5. Cablear las dos zonas de iluminación y el sensor conforme a la sección 7.
6. `sudo yocto/scripts/flashear.sh /dev/sdX` y expulsar.
7. Arrancar el equipo y esperar dos minutos.
8. Ejecutar la comprobación de la sección 10 y confirmar las cinco condiciones.
