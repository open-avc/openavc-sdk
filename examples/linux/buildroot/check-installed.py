"""Confirm that the reference firmware loads its installed product manager."""
import importlib
import json
from pathlib import Path
import sys

import aiohttp
import openavc_oem
import psutil

package = Path(openavc_oem.__file__).resolve()
if not package.is_relative_to(Path('/usr/lib')):
    raise ValueError('The check must load the installed package, not a source copy: ' + str(package))
importlib.import_module('openavc_oem.manager')
print(json.dumps({'package_file': str(package), 'python': sys.version,
                  'aiohttp': aiohttp.__version__, 'psutil': psutil.__version__}))
