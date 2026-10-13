HOMEPAGE = "https://github.com/open-avc/openavc-sdk"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://LICENSE;md5=39472bc166112ea73df967355fa1a309"
PV = "0.0.0+git"

OPENAVC_SDK_REVISION ?= "fee3ff50a794b1436b3749c529b55ce952209c9c"
SRC_URI = "git://github.com/open-avc/openavc-sdk.git;protocol=https;nobranch=1"
SRCREV = "${OPENAVC_SDK_REVISION}"
S = "${UNPACKDIR}/git"
