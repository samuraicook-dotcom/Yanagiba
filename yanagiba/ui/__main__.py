"""Run the Yanagiba UI: python -m yanagiba.ui"""

from yanagiba.ui.app import app

app.run(host="0.0.0.0", port=5000, debug=True)
