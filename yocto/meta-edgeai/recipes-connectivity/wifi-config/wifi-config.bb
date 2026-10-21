SUMMARY = "Configuracion Wi-Fi (wpa_supplicant + systemd-networkd) para Jarvis"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit allarch

WIFI_SSID ?= ""
WIFI_PSK ?= ""
WIFI_COUNTRY ?= "CR"

SRC_URI = "file://wlan0.network"
S = "${UNPACKDIR}"

RDEPENDS:${PN} = "wpa-supplicant"

do_install() {
    if [ -z "${WIFI_SSID}" ]; then
        bbfatal "WIFI_SSID vacio: construya con el archivo wifi-local.yml"
    fi
    install -d ${D}${sysconfdir}/wpa_supplicant
    cat > ${D}${sysconfdir}/wpa_supplicant/wpa_supplicant-wlan0.conf <<EOT
ctrl_interface=/run/wpa_supplicant
country=${WIFI_COUNTRY}
update_config=1
network={
    ssid="${WIFI_SSID}"
    psk="${WIFI_PSK}"
}
EOT
    chmod 0600 ${D}${sysconfdir}/wpa_supplicant/wpa_supplicant-wlan0.conf

    install -d ${D}${sysconfdir}/systemd/network
    install -m 0644 ${S}/wlan0.network ${D}${sysconfdir}/systemd/network/

    install -d ${D}${sysconfdir}/systemd/system/multi-user.target.wants
    ln -sf ${systemd_system_unitdir}/wpa_supplicant@.service \
        ${D}${sysconfdir}/systemd/system/multi-user.target.wants/wpa_supplicant@wlan0.service
}

FILES:${PN} += "${sysconfdir}"
