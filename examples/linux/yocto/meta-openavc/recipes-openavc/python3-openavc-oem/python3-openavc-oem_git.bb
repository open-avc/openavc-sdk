SUMMARY = "OpenAVC product management and optional server supervision"
require recipes-openavc/openavc-source.inc

inherit python_setuptools_build_meta
RDEPENDS:${PN} += "python3-modules python3-aiohttp python3-psutil"
