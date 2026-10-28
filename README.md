# Proyecto II — Sistema Inteligente de Borde (EdgeAI): "Jarvis"

**Curso:** Sistemas Embebidos · TEC — Escuela de Ingeniería Electrónica
**Facilitador:** Dr. Ing. Johan Carvajal Godínez
**Equipo:** Rol A — Saúl Quesada · Rol B — Daniel Chavarría
**Plataforma:** Raspberry Pi 4 (8 GB) · imagen Yocto Project 6.0 "Wrynose"
**Inferencia local:** Ollama 0.40.6 · modelo de decisión `tev1:0.8b` · modelo generativo `llama3.2:1b`

---

## 0. Cómo leer este documento

Este README describe **qué es el sistema, a quién sirve y por qué está construido así**. No es la propuesta de diseño ni el plan de trabajo: es la definición vigente del proyecto.

El contenido está ordenado como lo pide el método de ingeniería de sistemas del curso: primero la necesidad y los actores, de ahí los requisitos, de ahí el concepto de operaciones, y recién después la arquitectura y la implementación.

**Lo que cambió respecto de versiones anteriores de este documento** está consignado en la sección 10. Varias decisiones se revirtieron por evidencia experimental, incluida la tesis original del proyecto.

---

## 1. Necesidad

Un aula o laboratorio universitario genera, en cada sesión, información que hoy se gestiona de forma dispersa: qué entregó cada estudiante, qué queda pendiente, en qué condiciones ambientales se trabajó, cómo debe configurarse la iluminación según la actividad. Esa información es **institucional y personal a la vez**, y las herramientas comerciales que podrían gestionarla exigen enviarla a servidores de terceros.

El sistema responde a tres restricciones que, juntas, descartan una solución en la nube:

**Privacidad.** La información académica de estudiantes identificables no debe salir de la institución. Un asistente que procese consultas sobre entregas y calificaciones en servidores externos traslada un problema de cumplimiento a un tercero.

**Independencia de red.** El aula debe funcionar aunque la conectividad falle. Un sistema que depende de internet para encender una luz o consultar un registro es inutilizable precisamente cuando más se lo necesita.

**Latencia determinista.** El tiempo de respuesta no debe depender de la carga de un servicio externo ni de la congestión de la red institucional.

El sistema atiende las tres **procesando todo en el dispositivo**: los modelos de inferencia residen en la Raspberry Pi, la información persiste localmente, y el equipo emite su propia red inalámbrica. Ningún dato sale del aula.

---

## 2. Mapeo de actores

| Actor | Rol frente al sistema | Qué necesita | Cómo interactúa |
|---|---|---|---|
| **Estudiante** | Usuario | Consultar su estado de entregas y la agenda del curso sin intermediarios | Portal web desde su dispositivo, conectado a la red del equipo |
| **Profesor** | Operador | Registrar entregas y consultar el estado del grupo durante la sesión, sin interrumpir la clase | Portal web con operaciones de registro |
| **Técnico** | Mantenedor | Verificar el estado del sistema, los servicios y las condiciones del equipo | Acceso por consola y portal de diagnóstico |
| **Coordinador** | Responsable institucional | Confiar en que la información no sale de la institución | No interactúa directamente; es destinatario de la garantía de privacidad |

Los tres primeros son actores operativos. El coordinador es un **actor de restricción**: no usa el sistema, pero su necesidad —que los datos permanezcan locales— condiciona toda la arquitectura.

---

## 3. Requisitos

Los requisitos se derivan de dos fuentes distintas, siguiendo la estructura del método: las **necesidades** producen funciones, y las **restricciones** producen límites de diseño.

### 3.1 Necesidades → funciones

| ID | Necesidad del actor | Función del sistema |
|---|---|---|
| RF-01 | El usuario expresa lo que quiere en lenguaje natural | Interpretar una orden escrita y determinar la operación solicitada |
| RF-02 | El sistema no debe actuar cuando no entendió | Reportar la confianza de cada decisión y abstenerse bajo un umbral |
| RF-03 | El profesor registra entregas durante la sesión | Persistir registros académicos de forma local y duradera |
| RF-04 | El estudiante consulta su estado | Recuperar y presentar registros filtrados por usuario |
| RF-05 | El aula se adapta a la actividad | Controlar la iluminación por zonas con color e intensidad |
| RF-06 | Se conocen las condiciones de trabajo | Medir y registrar temperatura y humedad del espacio |
| RF-07 | El usuario necesita una síntesis, no datos crudos | Generar lenguaje natural breve a partir del estado del sistema |
| RF-08 | Una orden puede requerir varias acciones | Ejecutar un ciclo de decisión y acción hasta satisfacer la orden o agotar un criterio de parada |
| RF-09 | Una acción irreversible no debe ejecutarse por error | Exigir confirmación explícita en acciones destructivas |
| RF-10 | El usuario debe saber que el sistema está trabajando | Indicar el estado de procesamiento de forma visible |

### 3.2 Restricciones → límites de diseño

**De hardware (Raspberry Pi 4, 8 GB)**

| ID | Restricción | Origen | Consecuencia de diseño |
|---|---|---|---|
| RH-01 | Inferencia exclusivamente en CPU; sin GPU utilizable | Plataforma | Toda estimación de desempeño se hace sobre CPU ARM |
| RH-02 | Procesamiento de prompt a **18,5 tokens/s** | **Medido** (§6) | El tamaño del prompt es el presupuesto de latencia |
| RH-03 | Generación de texto a **2,6 tokens/s** | **Medido** (§6) | Las respuestas generativas se limitan a 40 tokens |
| RH-04 | 7,8 GB de memoria disponible; ~1,5 GB consumidos por ambos modelos | **Medido** (§6) | La memoria no es la restricción activa |
| RH-05 | Dos canales de PWM por hardware | Documentación del SoC | Dos zonas de iluminación RGB; el resto conmutado |
| RH-06 | El SoC alcanza 70 °C bajo inferencia sostenida | **Medido** (§6) | Se requiere disipación |

**De software**

| ID | Restricción | Origen | Consecuencia de diseño |
|---|---|---|---|
| RS-01 | El endpoint `/v1/systemone` existe desde Ollama 0.35 | Documentación | La receta fija versión ≥ 0.35; se usa 0.40.6 |
| RS-02 | Una pregunta `choice` admite máximo 24 criterios | Documentación de Tev1 | Límite superior del catálogo |
| RS-03 | Precisión publicada de `tev1:0.8b`: 63,5 % | Documentación | El umbral de confianza es obligatorio |
| RS-04 | `tev1:0.8b` solo está publicado en Q8_0 | **Verificado** (§6) | No hay optimización por cuantización disponible |
| RS-05 | `/v1/systemone` no sirve modelos de chat | Documentación | Se requieren dos modelos distintos |
| RS-06 | No existe capa Yocto pública para Ollama | Verificado | Receta propia en `meta-edgeai` |
| RS-07 | La imagen es Linux sin garantías de tiempo real | Arquitectura | El hardware se opera por drivers del núcleo, no por temporización en espacio de usuario |

Los requisitos marcados como **medido** provienen de la sesión experimental del 27 de octubre y están respaldados por el reporte en `docs/benchmark/`.

---

## 4. Concepto de operaciones

```
        ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
        │  Estudiante  │     │   Profesor   │     │    Técnico   │
        └───────┬──────┘     └───────┬──────┘     └───────┬──────┘
                │                    │                    │
                └────────────────────┼────────────────────┘
                                     │  navegador
                                     ▼
                      ╔══════════════════════════════╗
                      ║   Red inalámbrica del equipo ║
                      ║   (punto de acceso local)    ║
                      ╚══════════════╤═══════════════╝
                                     │
                     ┌───────────────▼────────────────┐
                     │      RASPBERRY PI 4 · 8 GB     │
                     │                                │
                     │   Portal web                   │
                     │   Agente coordinador           │
                     │   Modelos de inferencia        │
                     │   Registro local               │
                     └───────┬───────────────┬────────┘
                             │               │
                     ┌───────▼─────┐   ┌─────▼──────┐
                     │ Iluminación │   │  Sensor    │
                     │  por zonas  │   │ ambiental  │
                     └─────────────┘   └────────────┘

                     ╳ Sin enlace a internet ╳
```

El equipo **emite su propia red inalámbrica**. Los usuarios se conectan a ella y acceden al portal desde su navegador. No hay cliente que instalar ni enlace hacia afuera: la ausencia de internet no es una limitación declarada sino una propiedad observable, verificable desconectando cualquier cable durante la demostración.

El usuario escribe una orden en lenguaje natural, el portal indica que el sistema está procesando, y la respuesta llega en el orden de los quince segundos. **El sistema está diseñado como asistente asíncrono, no conversacional**: la justificación está en la sección 6.

---

## 5. Arquitectura

### 5.1 El agente coordinador

El sistema monta dos modelos sobre Ollama, y lo que los une es un **agente**: un componente que no clasifica ni redacta por sí mismo, sino que decide a cuál consultar, con qué, cuántas veces y cuándo detenerse.

| Componente | Facultad | Qué hace | Qué no hace |
|---|---|---|---|
| Modelo de decisión (`tev1:0.8b`) | Decidir | Elige entre opciones tipadas y reporta probabilidad | No produce lenguaje |
| Modelo generativo (`llama3.2:1b`) | Expresar | Redacta lenguaje natural breve | No decide |
| **Agente** | Coordinar | Orquesta el ciclo, ejecuta herramientas, aplica la política de parada | No clasifica ni redacta |

El ciclo del agente es *percibir → decidir → actuar → evaluar*, acotado por un máximo de iteraciones. Una orden que requiere varias acciones las produce en vueltas sucesivas; una orden incompleta se detiene y pregunta.

**Política de parada.** El ciclo termina por una de cinco razones, y las cinco son resultados legítimos: la orden se cumplió, la confianza quedó bajo el umbral, falta información, la acción requiere confirmación, o se agotaron las iteraciones.

El meta-modelo completo del agente está en `docs/propuesta/metamodelo-agente.md`.

### 5.2 Pila del sistema

```
┌──────────────────────────────────────────┐
│  Aplicación — agente coordinador         │
├──────────────────────────────────────────┤
│  Bibliotecas — Ollama, Python            │
├──────────────────────────────────────────┤
│  Sistema operativo — Linux / Yocto       │
├──────────────────────────────────────────┤
│  Hardware — Raspberry Pi 4               │
└──────────────────────────────────────────┘
```

Cada capa se construye desde la de abajo: la imagen Yocto determina qué bibliotecas existen, y estas determinan qué puede hacer la aplicación. El análisis de dependencias que recorre las tres está en `docs/dependencias/analisis.md`.

---

## 6. Resultados medidos

Mediciones del **27 de octubre de 2026** sobre Raspberry Pi 4 Model B Rev 1.5, núcleo 6.18.33-v8, con 28 casos etiquetados y cuatro pruebas controladas. Reporte completo en `docs/benchmark/borde-pi4-2026-10-27.txt`.

### 6.1 La ley de latencia

El costo de una consulta de decisión **es lineal en el tamaño del prompt**:

$$t = \frac{n_{\text{tokens}}}{18{,}5}$$

| Prueba | tokens | tiempo | tok/s |
|---|---:|---:|---:|
| Tres criterios breves | 130 | 7,06 s | 18,4 |
| Seis criterios con descripción | 269 | 14,51 s | 18,5 |
| Idéntica, con contexto reducido | 269 | 14,51 s | 18,5 |

El doble de tokens, exactamente el doble de tiempo. La relación predice el banco de 28 casos con menos del 1 % de error. El tamaño del contexto (`num_ctx`) no influye.

**Consecuencia:** el catálogo de intenciones *es* el presupuesto de latencia. Cada palabra escrita en la descripción de un criterio se paga en cada consulta.

### 6.2 Desempeño de los dos carriles

| Diseño | tokens | tiempo |
|---|---:|---:|
| Router de dos etapas, descripciones ricas (42 operaciones) | 659 | **35,6 s** |
| Una etapa, descripciones breves (8 operaciones) | 243 | **13,4 s** |
| Carril generativo, 35 tokens de salida | 44 + 35 | **17,1 s** |

### 6.3 Precisión

Sobre 28 frases etiquetadas con el catálogo de 42 operaciones: **78,6 %** de acierto en dominio y **60,7 %** en acción. La confianza de los aciertos se distribuyó entre 0,21 y 0,97.

Con el catálogo reducido a 8 operaciones, la confianza de la prueba subió a 0,88. La reducción del catálogo mejora la precisión además de la latencia.

### 6.4 Recursos

| Magnitud | Valor |
|---|---|
| Memoria consumida por ambos modelos | 1486 MiB de 7806 MiB |
| Temperatura del SoC en reposo → bajo carga | 38,9 °C → 70,1 °C |
| Cuantización disponible de `tev1:0.8b` | Q8_0 únicamente |

### 6.5 La tesis, corregida

La versión anterior de este documento afirmaba que *separar el carril de decisión del generativo reduce la latencia de segundos a décimas*. **Los datos refutan esa afirmación**: con el diseño original, la decisión tardaba 35,6 s y la generación 30,7 s.

La tesis que sí sostienen los datos es más precisa y más útil:

> **En inferencia de decisión sobre CPU restringida, la latencia es lineal en el tamaño del prompt. El diseño del catálogo de intenciones es, por lo tanto, el diseño de la latencia: un catálogo expresivo de 42 operaciones cuesta 36 segundos, mientras que uno mínimo de 8 cuesta 13.**

El valor del resultado no está en que el sistema sea rápido, sino en que **el costo de cualquier diseño puede predecirse antes de implementarlo**.

---

## 7. Catálogo de operaciones

### 7.1 Presupuesto

Con el objetivo de responder en menos de quince segundos y la ley de la sección 6.1, el presupuesto es de **277 tokens**. De ellos, unos 100 corresponden a plantilla fija y unos 10 al estado del usuario, quedando **unos 167 tokens para criterios**. A 16,9 tokens por criterio medidos, el catálogo admite **nueve operaciones**.

| Operaciones | tokens | tiempo estimado |
|---:|---:|---:|
| 8 | 245 | 13,2 s |
| **9** | **262** | **14,2 s** |
| 10 | 279 | 15,1 s |

### 7.2 Las nueve operaciones

Una sola etapa de clasificación. El router jerárquico de dos etapas se eliminó: duplicaba la plantilla fija y costaba 36 segundos.

| ID | Descripción en el criterio | Carril | Actor principal |
|---|---|---|---|
| `LUZ_ON` | Encender iluminación | Decisión | Profesor |
| `LUZ_OFF` | Apagar iluminación | Decisión | Profesor |
| `LUZ_MODO` | Cambiar escena | Decisión | Profesor |
| `AMBIENTE` | Consultar ambiente | Decisión | Todos |
| `ENTREGA_REG` | Registrar entrega | Decisión + confirmación | Profesor |
| `ENTREGA_VER` | Consultar entregas | Decisión | Estudiante, profesor |
| `AGENDA` | Ver agenda | Decisión | Todos |
| `RESUMEN` | Resumir sesión | **Generativo** | Profesor |
| `NADA` | Ninguna anterior | — | — |

Las descripciones son deliberadamente breves: cada palabra adicional multiplica su costo por el número de consultas del sistema.

El catálogo vive en `intents.yaml`, no en el código: incorporar una operación no modifica el agente, pero **sí consume presupuesto de latencia**, y esa es ahora una restricción explícita de diseño.

### 7.3 Casos de uso de la demostración

| # | Qué se muestra | Qué demuestra |
|---|---|---|
| CU-01 | Encender la iluminación de una zona | Decisión → actuación física |
| CU-02 | Consultar las condiciones del aula | Sensor por driver del núcleo |
| CU-03 | Registrar una entrega y consultarla después | Estado persistente local |
| CU-04 | Pedir un resumen de la sesión | Enrutamiento al carril generativo |
| CU-05 | Entrada incomprensible | Confianza bajo umbral: el sistema se abstiene |
| CU-06 | Acción con confirmación requerida | Política de parada por riesgo |
| CU-07 | Desconectar todo cable y repetir | Operación sin red externa |
| CU-08 | Comparar latencia de ambos carriles en vivo | La ley de la sección 6.1 |

CU-05 y CU-07 son tan importantes como los demás: el primero demuestra que el sistema sabe cuándo no confiar en su propio modelo; el segundo, que la independencia de red es real.

---

## 8. Meta-modelos

El sistema se modela en SysML según las cuatro ramas del método:

| Rama | Diagramas | Documento |
|---|---|---|
| **Estructura** | BDD — bloques del sistema y sus relaciones · IBD — conexiones internas y puertos | `docs/propuesta/metamodelo-plataforma.md` |
| **Comportamiento** | Casos de uso · Secuencia · Máquina de estados del ciclo del agente | `docs/propuesta/metamodelo-agente.md` |
| **Requisitos** | Trazabilidad entre necesidades, funciones y restricciones | §3 de este documento |
| **Paramétricos** | Restricción de latencia $t = n/18{,}5$ y presupuesto de tokens | §6.1 y §7.1 |

La rama paramétrica no es un ejercicio formal: la ley de latencia medida es una **restricción paramétrica real** que gobierna el diseño del catálogo, y el presupuesto de la sección 7.1 es su aplicación directa.

Un meta-modelo describe las *clases de entidades* del sistema y sus relaciones, no las instancias. Por eso en esos documentos no aparecen `tev1:0.8b` ni `jarvis-app`, sino `MotorDeDecisión`, `Agente`, `Herramienta`, `Receta` y `Dispositivo`: el modelo sobrevive a cambiar de modelo, agregar una operación o migrar de placa.

---

## 9. Plataforma

### 9.1 Reglas de la plataforma

> **R1.** Después del arranque, ningún modelo se lee de la MicroSD. Se verifica comparando `/proc/diskstats` antes y después de varias órdenes.
>
> **R2.** Ninguna operación requiere conexión a internet. Se verifica desconectando el equipo durante la demostración.

**Estado de R1:** la medición del 27 de octubre detectó 1254 MiB leídos durante la corrida. La causa fue que Ollama descargaba un modelo para cargar el otro. Se corrigió con `OLLAMA_MAX_LOADED_MODELS=2` y queda pendiente de reverificación.

### 9.2 Composición de la imagen

| Componente | Función | Criticidad |
|---|---|---|
| Ollama 0.40.6 | Servidor de inferencia | Obligatorio |
| `tev1:0.8b` | Modelo de decisión, horneado en la imagen | Obligatorio |
| `llama3.2:1b` | Modelo generativo, horneado en la imagen | Opcional |
| `jarvis-app` | Agente coordinador y portal | Obligatorio |
| Punto de acceso | Red inalámbrica propia del equipo | Obligatorio |
| Configuración del sistema | Registro volátil, `/tmp` en memoria | Obligatorio |

Los modelos se incorporan a la imagen en lugar de descargarse: sin ellos presentes, el primer arranque requeriría red y escritura en la MicroSD, violando ambas reglas.

**Los modelos representan el 97 % del peso de la imagen.** Todo el sistema operativo, el núcleo y los servicios suman menos de 80 MB.

### 9.3 Hardware

| Componente | Detalle |
|---|---|
| Raspberry Pi 4 | 8 GB, MicroSD, disipación obligatoria (70 °C bajo carga) |
| Fuente | 5 V / 3 A |
| 2 × LED RGB | Dos zonas de iluminación; canal rojo por PWM de hardware, verde y azul conmutados |
| DHT22 | Sensor de temperatura y humedad, GPIO4, driver IIO del núcleo |

El límite de dos zonas proviene de RH-05: la Pi 4 expone dos canales de PWM por hardware, y el PWM por software en espacio de usuario produce parpadeo bajo la carga de CPU de una inferencia. Cuatro zonas son el diseño objetivo; la vía de escalamiento identificada es un controlador PCA9685 por I2C, cuyo bus ya está habilitado en la configuración del BSP.

---

## 10. Decisiones revertidas

Registro de lo que cambió y por qué. Todas las reversiones provienen de evidencia, no de cambio de opinión.

| Decisión anterior | Estado | Motivo |
|---|---|---|
| Asistente personal doméstico genérico | **Revertida** | Sin actores identificables no hay requisitos derivables |
| Interacción por voz | **Diferida** | Libera la ruta crítica; las interfaces quedan listas |
| Router de dos etapas | **Eliminado** | Duplica la plantilla fija: 36 s medidos |
| Catálogo de 42 operaciones | **Reducido a 9** | Excede el presupuesto de latencia |
| PCA9685 para la iluminación | **Diferido** | No disponible; se usan los dos canales de PWM del SoC |
| Cliente de escritorio | **Reemplazado** | Portal web sobre red propia: sin instalación y demuestra el aislamiento |
| Tesis: separar carriles reduce latencia | **Refutada** | Medición: decisión 35,6 s contra generativo 30,7 s |
| Cuantización Q4 del modelo de decisión | **Descartada** | No publicada para `tev1:0.8b` |
| Recuperación aumentada sobre documentos | **Fuera de alcance** | Un contexto de 1500 tokens cuesta más de dos minutos |

---

## 11. División del trabajo

| Rol | Persona | Responsabilidad |
|---|---|---|
| **A — Director e Ingeniero de Aplicación** | Saúl Quesada | Documento de propuesta, actores y requisitos funcionales, concepto de operaciones, agente coordinador, catálogo, portal, verificación |
| **B — Líder Técnico / Arquitecto de Plataforma** | Daniel Chavarría | Imagen Yocto y recetas, capa de abstracción de hardware, punto de acceso, requisitos de restricción, meta-modelos de estructura, medición, documento de instalación |

El criterio de la división es que cada persona sea dueña de un lado de una interfaz definida: el Rol A trabaja por encima de la capa de abstracción y de los endpoints de inferencia; el Rol B por debajo. Las interfaces congeladas son `hal.py`, el contrato HTTP del portal y los nombres de servicios y rutas de configuración.

La auditoría de calidad rota semanalmente: toda incorporación la aprueba la otra persona, y cada semana una audita formalmente el trabajo de la otra, dejando entrada fechada en su bitácora.

---

## 12. Escalabilidad

**De plataforma.** La capa `meta-edgeai` es agnóstica de máquina: ninguna receta de aplicación referencia la placa. Migrar supone cambiar la máquina objetivo y agregar la capa del BSP correspondiente.

**De modelo.** El agente depende de las interfaces `MotorDeDecisión` y `GeneradorDeTexto`, nunca de implementaciones. Los modelos son parámetros de configuración: cambiarlos no requiere reconstruir la imagen. Una plataforma con más capacidad de cómputo admitiría `tev1:4b`, con mayor precisión publicada.

**De funcionalidad.** Una operación nueva es una entrada en el catálogo y una herramienta registrada. Ahora bien, el resultado de la sección 6 introduce una restricción que antes no existía: **cada operación agregada consume presupuesto de latencia**, y el catálogo no puede crecer indefinidamente sin degradar el tiempo de respuesta.

**De hardware.** Un sensor o actuador nuevo es una implementación más de las interfaces de `hal.py`. El escalamiento a cuatro zonas de iluminación está diseñado y solo requiere el controlador I2C.

**Fuera de alcance, pero contemplado.** Publicación de eventos para varios nodos contra un concentrador, actualizaciones con partición doble, e integración continua con pruebas sobre hardware real.

---

## 13. Estructura del repositorio

```
app/jarvis/          Agente, capa de abstracción de hardware, implementaciones real y simulada
app/tools/           Banco de medición y pruebas de hardware
yocto/kas/           Configuración de construcción con revisiones fijadas
yocto/meta-edgeai/   Capa propia: recetas de Ollama, modelos, aplicación e imagen
docs/propuesta/      Meta-modelos y secciones de la propuesta de diseño
docs/dependencias/   Análisis de dependencias y manifiesto de la imagen
docs/benchmark/      Reportes de medición
bitacoras/           Bitácora por rol
```

---

## 14. Referencias técnicas

- Ollama — notas de versión 0.35 a 0.40.x: endpoint `/v1/systemone` y modelos de decisión
- Documentación de TypeSafe sobre System One y los tipos `choice`, `noul` y `score`
- Together AI — ficha del modelo Tev1-0.8B
- Yocto Project Reference Manual, `meta-raspberrypi` y kas
- Documentación del subsistema PWM y de la interfaz de caracteres de GPIO del núcleo Linux
- Mediciones de inferencia con llama.cpp sobre Raspberry Pi 4
