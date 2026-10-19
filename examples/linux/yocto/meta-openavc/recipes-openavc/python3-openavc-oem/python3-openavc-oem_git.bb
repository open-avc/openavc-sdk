SUMMARY = "OpenAVC product management and optional server supervision"
require recipes-openavc/openavc-source.inc

inherit python_setuptools_build_meta
RDEPENDS:${PN} += "python3-modules python3-aiohttp (>= 3.14.4) python3-psutil"
