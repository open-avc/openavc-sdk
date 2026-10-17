SUMMARY = "Reference filesystem for the invented OpenAVC hardware examples"
LICENSE = "MIT"
inherit image

IMAGE_INSTALL = "base-files base-passwd busybox openavc-sdk openavc-sdk-examples openavc-example-consumer python3-openavc-oem openssl-bin"
IMAGE_FSTYPES = "tar"
IMAGE_FEATURES = ""
COPY_LIC_MANIFEST = "1"
COPY_LIC_DIRS = "1"
ROOTFS_LICENSE_DIR = "/usr/share/common-licenses"
# This filesystem is exercised on a native Linux host, without booting a kernel.
IMAGE_CLASSES:remove = "qemuboot"
