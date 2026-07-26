from fastapi import FastAPI
from app.api.routes.doctors import router as doctors_router

app = FastAPI(title="Voice Appointment Assistant API")

app.include_router(doctors_router)


@app.get("/health")
def health_check():
    return {"status": "ok"}

