from fastapi import FastAPI

app = FastAPI(title="Voice Appointment Assistant API")


@app.get("/health")
def health_check():
    return {"status": "ok"}
