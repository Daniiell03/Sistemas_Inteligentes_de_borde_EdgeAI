SUMMARY = "whisper.cpp: reconocimiento de voz local; incluye whisper-server"
HOMEPAGE = "https://github.com/ggml-org/whisper.cpp"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://LICENSE;md5=223b26b3c1143120c87e2b13111d3e99"

SRC_URI = "git://github.com/ggml-org/whisper.cpp.git;protocol=https;branch=master \
           file://whisper-server.service \
           file://whisper-server.default"
SRCREV = "371b5a7561823ab2bb32142d2751e35e7534727b"

inherit cmake systemd

EXTRA_OECMAKE = " \
    -DBUILD_SHARED_LIBS=OFF \
    -DGGML_NATIVE=OFF \
    -DWHISPER_BUILD_EXAMPLES=ON \
    -DWHISPER_BUILD_SERVER=ON \
    -DWHISPER_BUILD_TESTS=OFF \
    -DWHISPER_SDL2=OFF \
"

RDEPENDS:${PN} = "whisper-models"

SYSTEMD_SERVICE:${PN} = "whisper-server.service"
SYSTEMD_AUTO_ENABLE = "enable"

do_install:append() {
    install -d ${D}${systemd_system_unitdir} ${D}${sysconfdir}/default
    install -m 0644 ${UNPACKDIR}/whisper-server.service ${D}${systemd_system_unitdir}/
    install -m 0644 ${UNPACKDIR}/whisper-server.default ${D}${sysconfdir}/default/whisper-server
}
