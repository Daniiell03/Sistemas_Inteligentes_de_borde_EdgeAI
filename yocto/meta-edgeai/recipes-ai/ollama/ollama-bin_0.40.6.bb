SUMMARY = "Ollama: servidor local de modelos (binario oficial arm64, solo CPU)"
DESCRIPTION = "Empaqueta el binario oficial de Ollama para arm64 sin las bibliotecas CUDA, \
que no aplican en la Raspberry Pi. Incluye la API System One (/v1/systemone)."
HOMEPAGE = "https://github.com/ollama/ollama"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

COMPATIBLE_HOST = "aarch64.*-linux"

SRC_URI = "https://github.com/ollama/ollama/releases/download/v${PV}/ollama-linux-arm64.tar.zst;downloadfilename=ollama-linux-arm64-${PV}.tar.zst;subdir=ollama-${PV} \
           file://ollama.service"
SRC_URI[sha256sum] = "0a279d9428ad17b06fdb7dddf3b059b2c148433585fa3b30676a102fc459bb9f"

S = "${UNPACKDIR}/ollama-${PV}"

inherit systemd

SYSTEMD_SERVICE:${PN} = "ollama.service"
SYSTEMD_AUTO_ENABLE = "enable"

do_configure[noexec] = "1"
do_compile[noexec] = "1"

do_install() {
    install -d ${D}${bindir} ${D}${prefix}/lib/ollama
    install -m 0755 ${S}/bin/ollama ${D}${bindir}/ollama
    # lib/ollama completo (con enlaces simbolicos) excepto los backends CUDA
    tar -C ${S}/lib/ollama --exclude='cuda_*' -cf - . | tar -C ${D}${prefix}/lib/ollama --no-same-owner -xf -
    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${UNPACKDIR}/ollama.service ${D}${systemd_system_unitdir}/
}

FILES:${PN} += "${prefix}/lib/ollama"

# Binario precompilado: no se re-procesa ni se registran sus bibliotecas internas
INHIBIT_PACKAGE_STRIP = "1"
INHIBIT_PACKAGE_DEBUG_SPLIT = "1"
EXCLUDE_FROM_SHLIBS = "1"
INSANE_SKIP:${PN} += "already-stripped ldflags dev-so file-rdeps libdir"
