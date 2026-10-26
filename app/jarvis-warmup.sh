#!/bin/sh
# Calentamiento de los modelos de inferencia de Jarvis.
#
# Ollama carga un modelo en memoria la primera vez que se le consulta, no al
# arrancar el servicio. Sin este calentamiento, la primera orden del usuario
# pagaria el costo de leer el modelo desde la MicroSD, lo que ademas violaria
# la regla R1 en el momento menos oportuno: durante la demostracion.
#
# Este script espera a que Ollama responda y fuerza la carga de ambos modelos
# con una consulta trivial. Con OLLAMA_KEEP_ALIVE=-1 quedan residentes.
#
# Se ejecuta una sola vez al arranque, antes de jarvis.service.

set -u

SERVIDOR="${OLLAMA_HOST:-127.0.0.1:11434}"
case "$SERVIDOR" in
    http*) BASE="$SERVIDOR" ;;
    *)     BASE="http://$SERVIDOR" ;;
esac

MODELO_DECISION="${JARVIS_MODELO_DECISION:-tev1:0.8b}"
MODELO_GENERATIVO="${JARVIS_MODELO_GENERATIVO:-llama3.2:1b}"

ESPERA_MAX=120
INTERVALO=2

log() { echo "jarvis-warmup: $*"; }

# --- esperar a que el servidor responda -----------------------------------
log "esperando a Ollama en $BASE"
transcurrido=0
while [ "$transcurrido" -lt "$ESPERA_MAX" ]; do
    if curl -sf -o /dev/null --max-time 3 "$BASE/api/tags"; then
        log "Ollama responde tras ${transcurrido}s"
        break
    fi
    sleep "$INTERVALO"
    transcurrido=$((transcurrido + INTERVALO))
done

if [ "$transcurrido" -ge "$ESPERA_MAX" ]; then
    log "ERROR: Ollama no respondio en ${ESPERA_MAX}s"
    exit 1
fi

# --- carril de decision (obligatorio) -------------------------------------
log "cargando el modelo de decision $MODELO_DECISION"
inicio=$(date +%s)
respuesta=$(curl -sf --max-time 180 "$BASE/v1/systemone" \
    -H 'Content-Type: application/json' \
    -d "{
          \"model\": \"$MODELO_DECISION\",
          \"state\": \"calentamiento del sistema\",
          \"questions\": {
            \"listo\": {
              \"type\": \"noul\",
              \"instructions\": \"El sistema esta listo para operar\"
            }
          }
        }" 2>&1)
estado=$?
fin=$(date +%s)

if [ "$estado" -ne 0 ]; then
    log "ERROR: no se pudo cargar $MODELO_DECISION (curl $estado)"
    log "       $respuesta"
    exit 1
fi
log "modelo de decision residente en $((fin - inicio))s"

# --- carril generativo (opcional) -----------------------------------------
# Su ausencia reduce el catalogo pero no impide la operacion del sistema,
# por lo que un fallo aqui no aborta el arranque.
if [ -n "$MODELO_GENERATIVO" ]; then
    log "cargando el modelo generativo $MODELO_GENERATIVO"
    inicio=$(date +%s)
    if curl -sf -o /dev/null --max-time 300 "$BASE/api/chat" \
        -H 'Content-Type: application/json' \
        -d "{
              \"model\": \"$MODELO_GENERATIVO\",
              \"messages\": [{\"role\": \"user\", \"content\": \"ok\"}],
              \"stream\": false,
              \"options\": {\"num_predict\": 1}
            }"; then
        fin=$(date +%s)
        log "modelo generativo residente en $((fin - inicio))s"
    else
        log "AVISO: no se pudo cargar $MODELO_GENERATIVO"
        log "       el sistema operara solo con el carril de decision"
    fi
fi

log "calentamiento completo"
exit 0
