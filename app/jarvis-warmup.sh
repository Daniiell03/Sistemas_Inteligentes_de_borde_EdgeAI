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
# REINTENTOS Y DIAGNOSTICO
# ------------------------
# La version anterior fallaba en el arranque del 9 de noviembre con un codigo
# de curl que indica error HTTP, mientras la misma peticion ejecutada a mano
# devolvia 200. El registro del sistema mostro por que no se pudo saber mas:
# la Raspberry Pi no tiene reloj de tiempo real, arranca con una fecha falsa,
# y systemd-timesyncd sincronizo justo entre el envio de la consulta y el
# error. Un salto de meses a mitad de una peticion con tiempo limite basta
# para abortarla.
#
# De ahi tres cambios:
#
#   1. La unidad espera a time-sync.target, de modo que el reloj ya este
#      ajustado antes de la primera consulta.
#   2. Cada carga se reintenta tres veces con espera creciente. Un
#      calentamiento que no reintenta es fragil cualquiera sea la causa.
#   3. Se registra el codigo HTTP y el cuerpo de la respuesta. La version
#      anterior usaba 'curl -sf', que oculta el cuerpo: el servicio fallaba
#      sin dejar rastro de por que.
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
INTENTOS=3

log() { echo "jarvis-warmup: $*"; }

# Envia una peticion y devuelve 0 solo si el codigo HTTP es 200. Registra el
# codigo y el cuerpo cuando no lo es, que es lo que faltaba para diagnosticar.
consultar() {
    ruta="$1"
    cuerpo="$2"
    limite="$3"
    salida=$(curl -s -w '\n%{http_code}' --max-time "$limite" \
        "$BASE$ruta" -H 'Content-Type: application/json' -d "$cuerpo" 2>&1)
    estado=$?
    codigo=$(printf '%s' "$salida" | tail -n 1)
    texto=$(printf '%s' "$salida" | sed '$d')
    if [ "$estado" -ne 0 ]; then
        log "       curl fallo con codigo $estado"
        return 1
    fi
    if [ "$codigo" != "200" ]; then
        log "       HTTP $codigo: $(printf '%s' "$texto" | head -c 200)"
        return 1
    fi
    return 0
}

# Reintenta una carga con espera creciente entre intentos.
cargar() {
    nombre="$1"
    ruta="$2"
    cuerpo="$3"
    limite="$4"
    intento=1
    while [ "$intento" -le "$INTENTOS" ]; do
        inicio=$(date +%s)
        if consultar "$ruta" "$cuerpo" "$limite"; then
            fin=$(date +%s)
            log "$nombre residente en $((fin - inicio))s (intento $intento)"
            return 0
        fi
        log "$nombre: intento $intento de $INTENTOS sin exito"
        intento=$((intento + 1))
        [ "$intento" -le "$INTENTOS" ] && sleep $((intento * 5))
    done
    return 1
}

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

# Que /api/tags responda significa que el servidor escucha, no que este listo
# para cargar un modelo. Un margen corto evita competir con el final de su
# propio arranque.
sleep 3

# --- carril de decision (obligatorio) -------------------------------------
log "cargando el modelo de decision $MODELO_DECISION"
PETICION_DECISION=$(printf '{"model":"%s","state":"calentamiento del sistema","questions":{"listo":{"type":"noul","instructions":"El sistema esta listo para operar"}}}' "$MODELO_DECISION")

if ! cargar "modelo de decision" "/v1/systemone" "$PETICION_DECISION" 180; then
    log "ERROR: no se pudo cargar $MODELO_DECISION tras $INTENTOS intentos"
    log "       la primera orden pagara la carga desde la MicroSD (R1)"
    exit 1
fi

# --- carril generativo (opcional) -----------------------------------------
# Su ausencia reduce el catalogo pero no impide la operacion del sistema,
# por lo que un fallo aqui no aborta el arranque.
if [ -n "$MODELO_GENERATIVO" ]; then
    log "cargando el modelo generativo $MODELO_GENERATIVO"
    PETICION_GEN=$(printf '{"model":"%s","messages":[{"role":"user","content":"ok"}],"stream":false,"options":{"num_predict":1}}' "$MODELO_GENERATIVO")
    if ! cargar "modelo generativo" "/api/chat" "$PETICION_GEN" 300; then
        log "AVISO: no se pudo cargar $MODELO_GENERATIVO"
        log "       el sistema operara solo con el carril de decision, y la"
        log "       primera operacion de resumen tardara cerca de un minuto"
    fi
fi

# --- verificacion ---------------------------------------------------------
# Que las consultas respondieran no garantiza que los modelos quedaran
# residentes: eso depende de OLLAMA_KEEP_ALIVE y del limite de modelos
# simultaneos. Se comprueba, porque es la condicion que R1 exige.
residentes=$(curl -sf --max-time 5 "$BASE/api/ps" 2>/dev/null | grep -o '"name"' | wc -l)
log "modelos residentes: $residentes"
if [ "$residentes" -lt 2 ]; then
    log "AVISO: se esperaban 2 modelos residentes."
    log "       Revisar OLLAMA_MAX_LOADED_MODELS y OLLAMA_KEEP_ALIVE en"
    log "       ollama.service: con uno solo, cambiar de carril obliga a"
    log "       releer el otro modelo desde la MicroSD."
fi

log "calentamiento completo"
exit 0
