SUMMARY = "OpenAVC native hardware adapter and device service"
require recipes-openavc/openavc-source.inc

inherit cmake python3native deploy
DEPENDS += "openssl"
COMPATIBLE_HOST = "(aarch64|x86_64).*-linux$"

OPENAVC_PRODUCT ?= "${S}/product.json"
EXTRA_OECMAKE += "-DOPENAVC_INSTALL=ON -DOPENAVC_PRODUCT=${OPENAVC_PRODUCT}"
PACKAGECONFIG ??= ""
PACKAGECONFIG[examples] = "-DOPENAVC_BUILD_EXAMPLES=ON,-DOPENAVC_BUILD_EXAMPLES=OFF"

PACKAGES =+ "${PN}-examples"
FILES:${PN}-examples = "${libexecdir}/openavc-examples"
FILES:${PN} += "${datadir}/openavc ${datadir}/licenses/openavc-sdk"
FILES:${PN}-dev += "${libdir}/cmake/OpenAVC"
FILES:${PN}-staticdev += "${libdir}/openavc/*.a"

do_deploy() {
    install -d ${DEPLOYDIR}/openavc-sdk
    install -m 0644 ${B}/generated/* ${DEPLOYDIR}/openavc-sdk/
    install -m 0644 ${B}/build-target.json ${DEPLOYDIR}/openavc-sdk/
}
addtask deploy after do_compile before do_build
