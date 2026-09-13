import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from app.web.server import Handler
from app.core import clone
print("Clone module:", clone)
print("Available:", clone.available())
print("Profiles:", clone.list_profiles())
