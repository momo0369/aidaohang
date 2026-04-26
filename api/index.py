import sys
import os
from pathlib import Path

api_dir = Path(__file__).resolve().parent
project_dir = api_dir.parent
sys.path.insert(0, str(project_dir))

os.chdir(project_dir)

from app import app

handler = app
