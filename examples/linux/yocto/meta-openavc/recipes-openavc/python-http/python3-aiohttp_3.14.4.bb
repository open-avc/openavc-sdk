SUMMARY = "Async http client/server framework"
DESCRIPTION = "Asynchronous HTTP client/server framework for asyncio and Python"
HOMEPAGE = "https://github.com/aio-libs/aiohttp"
LICENSE = "Apache-2.0 & MIT"
LIC_FILES_CHKSUM = "file://LICENSE.txt;md5=b5e9e27554f71f0f132919037cb967b2 file://vendor/llhttp/LICENSE;md5=9e4e583cb28b7e0c2ef8df4832706e96"

SRC_URI[sha256sum] = "831fc5bd39ec2517851e348f613ddb5447a47cf4b71cb09845af7ad7ed45d8f9"

CVE_PRODUCT = "aiohttp:aiohttp"
inherit python_setuptools_build_meta pypi

DEPENDS = "python3-pkgconfig-native"

PACKAGECONFIG ??= ""
PACKAGECONFIG[extras] = ",,,python3-aiodns python3-brotli"

RDEPENDS:${PN} = "\
    python3-aiohappyeyeballs \
    python3-aiosignal \
    python3-async-timeout \
    python3-attrs \
    python3-frozenlist \
    python3-misc \
    python3-multidict \
    python3-propcache \
    python3-yarl (>= 1.25.1) \
"

CFLAGS:append:toolchain-gcc:arm = " -flax-vector-conversions"

BBCLASSEXTEND = "native nativesdk"
