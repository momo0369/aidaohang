import os
import sys
from pathlib import Path

api_dir = Path(__file__).resolve().parent
project_dir = api_dir.parent

os.chdir(project_dir)
sys.path.insert(0, str(project_dir))

os.environ["AI_TOOLS_DB_PATH"] = str(project_dir / "ai_tools.sqlite3")

from app import app, connect_db, init_db

app.config["DB_PATH"] = str(project_dir / "ai_tools.sqlite3")

try:
    with connect_db(app.config["DB_PATH"]) as conn:
        init_db(conn)
except Exception:
    pass

handler = app
