"""Start the installed OpenAVC server with the product's interface controls."""

import os

from openavc import main
from .interfaces import install

interfaces = install(main.app, os.environ['OPENAVC_DEVICE_ID'])
main.configure_host_policy(interfaces.host_policy)
main.main()
