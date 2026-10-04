"""OpenManus web backend: REST + SSE API, run manager and static SPA hosting.

The web server always runs browsers headless unless explicitly configured otherwise;
the default must be in place before the core configuration is first loaded.
"""

import os


os.environ.setdefault("OPENMANUS_BROWSER_HEADLESS", "1")
