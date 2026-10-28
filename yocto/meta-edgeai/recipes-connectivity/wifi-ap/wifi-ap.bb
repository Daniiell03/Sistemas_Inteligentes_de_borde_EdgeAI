SUMMARY = "Punto de acceso inalambrico del asistente Jarvis"
DESCRIPTION = "Configura el equipo para emitir su propia red inalambrica y \
asignar direcciones a los clientes del aula. Los usuarios se conectan a esa \
red y acceden al portal sin instalar nada y sin que exista enlace a internet. \
\
INCOMPATIBLE con wifi-config: una misma interfaz no puede ser punto de acceso \
y cliente simultaneamente. Instalar ambos deja la interfaz en un estado \
indeterminado."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit allarch systemd

SRC_URI = "file://hostapd.conf \
           file://dnsmasq-jarvis.conf \
           file://wlan0-ap.network \
           file://jarvis-ap.service"

S = "${UNPACKDIR}"

RDEPENDS:${PN} = "hostapd dnsmasq"

# wifi-config pone la interfaz en modo cliente: los dos paquetes no pueden
# convivir. Declararlo evita que una imagen los incluya a ambos por error.
RCONFLICTS:${PN} = "wifi-config"

SYSTEMD_SERVICE:${PN} = "jarvis-ap.service"
SYSTEMD_AUTO_ENABLE = "enable"

do_install() {
    # Configuracion del punto de acceso
    install -d ${D}${sysconfdir}
    install -m 0600 ${S}/hostapd.conf ${D}${sysconfdir}/hostapd.conf

    # Asignacion de direcciones. Se instala como fragmento para no reemplazar
    # la configuracion que trae el paquete base.
    install -d ${D}${sysconfdir}/dnsmasq.d
    install -m 0644 ${S}/dnsmasq-jarvis.conf ${D}${sysconfdir}/dnsmasq.d/

    # Direccion estatica de la interfaz
    install -d ${D}${sysconfdir}/systemd/network
    install -m 0644 ${S}/wlan0-ap.network ${D}${sysconfdir}/systemd/network/

    # Unidad que agrupa el arranque de ambos servicios
    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${S}/jarvis-ap.service ${D}${systemd_system_unitdir}/

    # El archivo de concesiones vive en memoria: el medio de arranque no se
    # escribe durante la operacion.
    install -d ${D}${sysconfdir}/tmpfiles.d
    echo "d /run/dnsmasq 0755 root root -" \
        > ${D}${sysconfdir}/tmpfiles.d/jarvis-ap.conf
}

FILES:${PN} += "${sysconfdir}/hostapd.conf \
                ${sysconfdir}/dnsmasq.d \
                ${sysconfdir}/systemd/network \
                ${sysconfdir}/tmpfiles.d"

CONFFILES:${PN} = "${sysconfdir}/hostapd.conf ${sysconfdir}/dnsmasq.d/dnsmasq-jarvis.conf"
