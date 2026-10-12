OPENAVC_SDK_VERSION = local
OPENAVC_SDK_SITE = $(abspath $(BR2_EXTERNAL_OPENAVC_PATH)/../../..)
OPENAVC_SDK_SITE_METHOD = local
OPENAVC_SDK_LICENSE = MIT
OPENAVC_SDK_LICENSE_FILES = LICENSE licenses/civetweb-LICENSE.md licenses/json-LICENSE.MIT
OPENAVC_SDK_DEPENDENCIES = host-python3 openssl
OPENAVC_SDK_INSTALL_STAGING = YES
OPENAVC_SDK_SUPPORTS_IN_SOURCE_BUILD = NO
# Copy source inputs only, never the developer's private build/test directories.
OPENAVC_SDK_OVERRIDE_SRCDIR_RSYNC_EXCLUSIONS = \
	--include='/CMakeLists.txt' --include='/LICENSE' --include='/product.json' \
	--include='/sdk/***' --include='/licenses/***' --include='/sample/***' --exclude='*'
OPENAVC_SDK_CONF_OPTS = \
	-DOPENAVC_BUILD_EXAMPLES=$(if $(BR2_PACKAGE_OPENAVC_SDK_EXAMPLES),ON,OFF) -DOPENAVC_INSTALL=ON \
	-DPython3_EXECUTABLE=$(HOST_DIR)/bin/python3
ifneq ($(call qstrip,$(BR2_PACKAGE_OPENAVC_SDK_PRODUCT)),)
OPENAVC_SDK_CONF_OPTS += -DOPENAVC_PRODUCT=$(call qstrip,$(BR2_PACKAGE_OPENAVC_SDK_PRODUCT))
endif

$(eval $(cmake-package))
