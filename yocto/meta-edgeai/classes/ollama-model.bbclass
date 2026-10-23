# Instala un modelo de Ollama previamente empaquetado por
# yocto/scripts/preparar-modelo.sh en ${DL_DIR}/jarvis-models/
#
# La receta que hereda esta clase define:
#   OLLAMA_MODEL_TAR  nombre del paquete
#   OLLAMA_MODEL_SHA  sha256 esperado

FILESEXTRAPATHS:prepend := "${DL_DIR}/jarvis-models:"

LICENSE = "CLOSED"
inherit allarch

S = "${UNPACKDIR}"

OLLAMA_MODELS_DIR = "${datadir}/ollama/models"

do_configure[noexec] = "1"
do_compile[noexec] = "1"

# El fetcher file:// no verifica sumas; se verifica explícitamente para
# que el build sea reproducible y detecte un paquete alterado.
do_verify_model() {
    real=$(sha256sum "${DL_DIR}/jarvis-models/${OLLAMA_MODEL_TAR}" | cut -d' ' -f1)
    if [ "$real" != "${OLLAMA_MODEL_SHA}" ]; then
        bbfatal "SHA256 de ${OLLAMA_MODEL_TAR} no coincide.\n  esperado: ${OLLAMA_MODEL_SHA}\n  obtenido: $real"
    fi
    bbnote "SHA256 verificado: ${OLLAMA_MODEL_TAR}"
}
addtask verify_model after do_unpack before do_install

do_install() {
    install -d ${D}${OLLAMA_MODELS_DIR}
    cp -a --no-preserve=ownership ${UNPACKDIR}/manifests ${D}${OLLAMA_MODELS_DIR}/
    cp -a --no-preserve=ownership ${UNPACKDIR}/blobs     ${D}${OLLAMA_MODELS_DIR}/
    chmod -R a+rX ${D}${OLLAMA_MODELS_DIR}
}

FILES:${PN} += "${OLLAMA_MODELS_DIR}"
