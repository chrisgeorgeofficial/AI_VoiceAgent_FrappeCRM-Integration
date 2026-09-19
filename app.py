from fastapi import FastAPI

app = FastAPI(title="Local Voice Agent Test")

@app.get("/")
def read_root():
    return {"status": "ok", "message": "FastAPI server is running on port 8000"}

@app.get("/health")
def health_check():
    return {"status": "healthy"}
