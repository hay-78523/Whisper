import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from app.web.server import Handler
import json

class DummyHandler:
    def __init__(self):
        self.path = "/api/voices"
        self.headers = {}
    
    def _require(self, role=None):
        return {"user": "admin", "role": "admin"}
        
    def _send(self, code, data, extra_headers=None):
        groups = data.get("groups", [])
        if len(groups) > 0:
            print("First group:", groups[0]["label"])
            print("Number of voices in first group:", len(groups[0]["voices"]))
            print("First 3 voices in first group:")
            for v in groups[0]["voices"][:3]:
                print(f"  - {v['id']} ({v['name']})")
        else:
            print("No groups found.")

def test():
    h = DummyHandler()
    Handler.do_GET(h)

test()
