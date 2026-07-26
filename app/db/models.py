from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    Time,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSTZRANGE
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Specialty(Base):
    __tablename__ = "specialties"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(Text, nullable=False, unique=True)

    doctors = relationship("Doctor", back_populates="specialty")


class Doctor(Base):
    __tablename__ = "doctors"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(Text, nullable=False)
    specialty_id = Column(Integer, ForeignKey("specialties.id"), nullable=False)
    working_days = Column(ARRAY(Integer), nullable=False, server_default="{1,2,3,4,5}")
    working_start_time = Column(Time, nullable=False, server_default="09:00")
    working_end_time = Column(Time, nullable=False, server_default="17:00")

    specialty = relationship("Specialty", back_populates="doctors")
    appointments = relationship("Appointment", back_populates="doctor")


class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(Text, nullable=False)
    last_name = Column(Text, nullable=False)
    date_of_birth = Column(Date, nullable=False)
    phone = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    appointments = relationship("Appointment", back_populates="patient")
    audit_logs = relationship("AuditLog", back_populates="patient")


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    appointment_range = Column(TSTZRANGE, nullable=False)
    status = Column(Text, nullable=False, server_default="booked")
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('booked', 'cancelled')", name="appointments_status_check"),
    )

    doctor = relationship("Doctor", back_populates="appointments")
    patient = relationship("Patient", back_populates="appointments")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True)
    action = Column(Text, nullable=False)
    allowed = Column(Boolean, nullable=False)
    details = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    patient = relationship("Patient", back_populates="audit_logs")
