SUMMARY = "Modelos ggml multilingues de Whisper (tiny y base)"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit allarch

SRC_URI = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin;name=tiny \
           https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin;name=base"
SRC_URI[tiny.sha256sum] = "be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21"
SRC_URI[base.sha256sum] = "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe"

S = "${UNPACKDIR}"

do_install() {
    install -d ${D}${datadir}/whisper
    install -m 0644 ${UNPACKDIR}/ggml-tiny.bin ${UNPACKDIR}/ggml-base.bin ${D}${datadir}/whisper/
}

FILES:${PN} = "${datadir}/whisper"
