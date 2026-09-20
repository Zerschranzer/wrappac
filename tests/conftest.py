"""Make the ``src`` directory importable for the test suite.

The application is installed to ``/usr/share/wrappac`` by the PKGBUILD and
uses flat, same-directory imports (``from models import ...``), so there is
no package boundary to rely on. Tests import those modules directly.
"""

import os
import sys

SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
