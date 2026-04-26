import os
import sys
from pathlib import Path

api_dir = Path(__file__).resolve().parent

sys.path.insert(0, str(api_dir.parent))

db_path = api_dir / "ai_tools.sqlite3"
os.environ["AI_TOOLS_DB_PATH"] = str(db_path)

from app import app, connect_db, init_db

app.config["DB_PATH"] = str(db_path)

@app.route("/debug")
def debug_info():
    return {
        "db_path": str(db_path),
        "db_exists": db_path.exists(),
        "db_size": db_path.stat().st_size if db_path.exists() else 0,
        "api_dir": str(api_dir),
        "api_dir_files": list(str(f) for f in api_dir.iterdir()) if api_dir.exists() else [],
        "cwd": os.getcwd(),
    }

try:
    if db_path.exists():
        with connect_db(app.config["DB_PATH"]) as conn:
            init_db(conn)
except Exception as e:
    pass

handler = app
