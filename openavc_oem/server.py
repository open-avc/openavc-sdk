"""Start the installed OpenAVC server with the product's interface controls."""

import os

from openavc import main
from .interfaces import install

install(main.app, os.environ['OPENAVC_DEVICE_ID'])
main.main()
