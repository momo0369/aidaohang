import sys
from pathlib import Path

api_dir = Path(__file__).resolve().parent
project_dir = api_dir.parent
sys.path.insert(0, str(project_dir))

from app import app, connect_db, init_db, DEFAULT_HOME_URL

app.config["DB_PATH"] = str(project_dir / "ai_tools.sqlite3")
app.config["HOME_SOURCE"] = DEFAULT_HOME_URL
app.config["AUTO_SYNC"] = False

try:
    with connect_db(app.config["DB_PATH"]) as conn:
        init_db(conn)
except Exception:
    pass

handler = app
