SUMMARY = "Example product application linked against the installed OpenAVC SDK"
require recipes-openavc/openavc-source.inc

inherit cmake
DEPENDS += "openavc-sdk openssl"
COMPATIBLE_HOST = "(aarch64|x86_64).*-linux$"
OECMAKE_SOURCEPATH = "${S}/examples/installed"
EXTRA_OECMAKE += "-DOPENAVC_ADAPTER_SOURCE=${S}/sample/example.c"

do_install() {
    install -d ${D}${libexecdir}/openavc-examples
    install -m 0755 ${B}/hardware ${D}${libexecdir}/openavc-examples/installed-product
}
FILES:${PN} = "${libexecdir}/openavc-examples/installed-product"
