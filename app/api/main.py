from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes.appointments import router as appointments_router
from app.api.routes.doctors import router as doctors_router
from app.api.routes.identity import router as identity_router

app = FastAPI(title="Voice Appointment Assistant API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(doctors_router)
app.include_router(identity_router)
app.include_router(appointments_router)


@app.get("/health")
def health_check():
    return {"status": "ok"}

