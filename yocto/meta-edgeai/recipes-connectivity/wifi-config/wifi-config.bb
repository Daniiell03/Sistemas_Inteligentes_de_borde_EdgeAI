SUMMARY = "Configuracion Wi-Fi (wpa_supplicant + systemd-networkd) para Jarvis"
DESCRIPTION = "Instala la lista de redes conocidas con prioridad. El archivo \
wpa_supplicant-wlan0.conf contiene credenciales y no se versiona: copie \
wpa_supplicant-wlan0.conf.example y complete sus redes."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit allarch

SRC_URI = "file://wlan0.network \
           file://wpa_supplicant-wlan0.conf"
S = "${UNPACKDIR}"

RDEPENDS:${PN} = "wpa-supplicant"

do_install() {
    install -d ${D}${sysconfdir}/wpa_supplicant
    install -m 0600 ${S}/wpa_supplicant-wlan0.conf ${D}${sysconfdir}/wpa_supplicant/

    install -d ${D}${sysconfdir}/systemd/network
    install -m 0644 ${S}/wlan0.network ${D}${sysconfdir}/systemd/network/

    install -d ${D}${sysconfdir}/systemd/system/multi-user.target.wants
    ln -sf ${systemd_system_unitdir}/wpa_supplicant@.service \
        ${D}${sysconfdir}/systemd/system/multi-user.target.wants/wpa_supplicant@wlan0.service
}

FILES:${PN} += "${sysconfdir}"
