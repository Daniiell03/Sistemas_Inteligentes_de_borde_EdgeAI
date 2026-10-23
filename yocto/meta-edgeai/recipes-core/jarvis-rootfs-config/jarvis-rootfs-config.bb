SUMMARY = "Configuracion del sistema para la regla de oro de Jarvis (sin escrituras en la SD)"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit allarch

SRC_URI = "file://10-jarvis-volatile.conf"
S = "${UNPACKDIR}"

do_install() {
    install -d ${D}${sysconfdir}/systemd/journald.conf.d
    install -m 0644 ${S}/10-jarvis-volatile.conf ${D}${sysconfdir}/systemd/journald.conf.d/
}

FILES:${PN} = "${sysconfdir}/systemd/journald.conf.d"
