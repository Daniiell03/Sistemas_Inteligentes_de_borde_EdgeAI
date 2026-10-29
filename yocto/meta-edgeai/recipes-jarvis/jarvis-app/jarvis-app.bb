SUMMARY = "Agente coordinador de Jarvis: aplicacion de borde"
DESCRIPTION = "Agente que coordina el modelo de decision (/v1/systemone) y el \
modelo generativo (/api/chat) servidos por Ollama, ejecuta las herramientas del \
catalogo sobre la capa de abstraccion de hardware y persiste el estado en SQLite. \
Incluye la unidad de servicio y el calentamiento de modelos al arranque."
HOMEPAGE = "https://github.com/Daniiell03/Sistemas_Inteligentes_de_borde_EdgeAI"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit allarch python3-dir systemd

# El codigo vive en app/ del mismo repositorio, cuatro niveles arriba de esta receta
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../app:"

SRC_URI = "file://jarvis \
           file://config.yaml \
           file://jarvis.service \
           file://jarvis-warmup.service \
           file://jarvis-warmup.sh"

S = "${UNPACKDIR}"

# python3-modules trae la biblioteca estandar completa (~50 MB). Se prefiere a
# enumerar subpaquetes porque Yocto fragmenta la stdlib y una omision solo se
# manifiesta en ejecucion. El costo es despreciable: los modelos de inferencia
# son el 97 % del peso de la imagen.
RDEPENDS:${PN} = "python3-core python3-modules ollama-bin ollama-model-tev1"

# El carril generativo es opcional: su ausencia reduce el catalogo a las
# operaciones de decision sin impedir la operacion del sistema.
RRECOMMENDS:${PN} = "ollama-model-llama32"

SYSTEMD_SERVICE:${PN} = "jarvis.service jarvis-warmup.service"
SYSTEMD_AUTO_ENABLE = "enable"

do_install() {
    # Paquete de Python en site-packages: 'import jarvis' funciona sin PYTHONPATH
    install -d ${D}${PYTHON_SITEPACKAGES_DIR}/jarvis
    install -m 0644 ${S}/jarvis/*.py ${D}${PYTHON_SITEPACKAGES_DIR}/jarvis/

    # El portal sirve la interfaz desde templates/, junto al modulo. Sin esto
    # el servicio arranca pero responde 404 en la pagina principal: el fallo
    # no aparece en la construccion sino en el primer acceso.
    install -d ${D}${PYTHON_SITEPACKAGES_DIR}/jarvis/templates
    install -m 0644 ${S}/jarvis/templates/* ${D}${PYTHON_SITEPACKAGES_DIR}/jarvis/templates/

    # Configuracion: modelos, umbral y limites son parametros, no constantes del codigo
    install -d ${D}${sysconfdir}/jarvis
    install -m 0644 ${S}/config.yaml ${D}${sysconfdir}/jarvis/config.yaml

    # Calentamiento de modelos
    install -d ${D}${libexecdir}/jarvis
    install -m 0755 ${S}/jarvis-warmup.sh ${D}${libexecdir}/jarvis/jarvis-warmup.sh

    # Unidades de systemd
    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${S}/jarvis.service ${D}${systemd_system_unitdir}/
    install -m 0644 ${S}/jarvis-warmup.service ${D}${systemd_system_unitdir}/

    # Directorio de estado. systemd lo crea por StateDirectory, pero se declara
    # aqui para que exista aunque el servicio no haya arrancado todavia.
    install -d ${D}${localstatedir}/lib/jarvis
}

FILES:${PN} += "${PYTHON_SITEPACKAGES_DIR}/jarvis \
                ${sysconfdir}/jarvis \
                ${libexecdir}/jarvis \
                ${localstatedir}/lib/jarvis"

CONFFILES:${PN} = "${sysconfdir}/jarvis/config.yaml"

# El paquete contiene solo fuentes de Python: no hay binarios que revisar
INSANE_SKIP:${PN} += "already-stripped"
