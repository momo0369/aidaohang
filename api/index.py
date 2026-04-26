import os
import sys
from pathlib import Path

vercel_root = Path(__file__).resolve().parent.parent
os.chdir(vercel_root)
sys.path.insert(0, str(vercel_root))

from app import app

handler = app
