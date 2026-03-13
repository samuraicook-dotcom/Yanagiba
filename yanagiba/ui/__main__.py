"""Run the Yanagiba UI: python -m yanagiba.ui"""

import os

from yanagiba.ui.app import app

port = int(os.environ.get("YANAGIBA_UI_PORT", 5000))
app.run(host="0.0.0.0", port=port, debug=False)
