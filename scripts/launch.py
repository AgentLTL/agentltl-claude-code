"""Entry point of the hooks and the `agentltl` command: ``python -I -s scripts/launch.py MODULE [ARGS]``.

``-I`` makes Python ignore every PYTHON* variable (PYTHONPATH, PYTHONHOME, PYTHONUSERBASE...), the
user's site-packages and the current directory, so nothing in the user's shell or project can
shadow the plugin's own environment. The plugin's code is added to the path here, from this
directory, and runs MODULE's ``main()``. Tests and development add their packages with
AGENTLTL_CC_PYTHONPATH.
"""

import importlib
import os
import sys

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(root, "src"))
sys.path.extend(p for p in os.environ.get("AGENTLTL_CC_PYTHONPATH", "").split(os.pathsep) if p)

module = sys.argv.pop(1)
sys.exit(importlib.import_module(module).main())
