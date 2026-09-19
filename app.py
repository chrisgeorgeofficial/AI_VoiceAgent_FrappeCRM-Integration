from fastapi import FastAPI, Request
from fastapi.responses import Response

app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "ok", "message": "FastAPI server is running on port 5000"}

@app.get("/health")
def health_check():
    return {"status": "healthy"}

@app.post("/voice/incoming")
async def incoming_call(request: Request):
    host = request.headers["host"]
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="wss://{host}/voice/media"/>
    </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")
