import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from app.web.server import Handler
import json

class DummyRequest:
    def makefile(self, *args, **kwargs):
        import io
        return io.BytesIO(b"GET /api/voices HTTP/1.1\r\nHost: localhost\r\n\r\n")
    def sendall(self, data):
        pass

class DummyHandler:
    def __init__(self):
        self.path = "/api/voices"
        self.headers = {}
    
    def _require(self, role=None):
        return {"user": "admin", "role": "admin"}
        
    def _send(self, code, data, extra_headers=None):
        print(f"Status: {code}")
        print("JSON Data:")
        print(json.dumps(data, indent=2, ensure_ascii=False))

def test():
    h = DummyHandler()
    Handler.do_GET(h)

test()
