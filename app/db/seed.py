from datetime import date, time
from app.db.models import Doctor, Patient, Specialty
from app.db.session import SessionLocal


def seed_data():
    session = SessionLocal()
    try:
        # 1. Specialties (3 specialties)
        specialties_data = [
            "General Practice",
            "Cardiology",
            "Dermatology",
        ]
        specialties_map = {}
        for name in specialties_data:
            spec = session.query(Specialty).filter_by(name=name).first()
            if not spec:
                spec = Specialty(name=name)
                session.add(spec)
                session.flush()
            specialties_map[name] = spec

        # 2. Doctors (4 doctors with realistic working hours)
        doctors_data = [
            {
                "name": "Dr. Sarah Connor",
                "specialty_name": "General Practice",
                "working_days": [1, 2, 3, 4, 5],
                "working_start_time": time(9, 0),
                "working_end_time": time(17, 0),
            },
            {
                "name": "Dr. John Smith",
                "specialty_name": "Cardiology",
                "working_days": [1, 2, 3, 4, 5],
                "working_start_time": time(9, 0),
                "working_end_time": time(17, 0),
            },
            {
                "name": "Dr. Emily Wong",
                "specialty_name": "Dermatology",
                "working_days": [1, 2, 3, 4, 5],
                "working_start_time": time(10, 0),
                "working_end_time": time(18, 0),
            },
            {
                "name": "Dr. Michael Brown",
                "specialty_name": "General Practice",
                "working_days": [1, 2, 3, 4, 5],
                "working_start_time": time(8, 0),
                "working_end_time": time(16, 0),
            },
        ]
        for doc_info in doctors_data:
            doc = session.query(Doctor).filter_by(name=doc_info["name"]).first()
            if not doc:
                doc = Doctor(
                    name=doc_info["name"],
                    specialty_id=specialties_map[doc_info["specialty_name"]].id,
                    working_days=doc_info["working_days"],
                    working_start_time=doc_info["working_start_time"],
                    working_end_time=doc_info["working_end_time"],
                )
                session.add(doc)

        # 3. Exactly 3 sample patients
        patients_data = [
            {
                "first_name": "John",
                "last_name": "Doe",
                "date_of_birth": date(1985, 5, 15),
                "phone": "555-0101",
            },
            {
                "first_name": "Jane",
                "last_name": "Smith",
                "date_of_birth": date(1992, 8, 22),
                "phone": "555-0102",
            },
            {
                "first_name": "Alice",
                "last_name": "Johnson",
                "date_of_birth": date(1978, 12, 3),
                "phone": "555-0103",
            },
        ]
        sample_patients = []
        for p_info in patients_data:
            pat = (
                session.query(Patient)
                .filter_by(
                    first_name=p_info["first_name"],
                    last_name=p_info["last_name"],
                    date_of_birth=p_info["date_of_birth"],
                )
                .first()
            )
            if not pat:
                pat = Patient(
                    first_name=p_info["first_name"],
                    last_name=p_info["last_name"],
                    date_of_birth=p_info["date_of_birth"],
                    phone=p_info["phone"],
                )
                session.add(pat)
                session.flush()
            sample_patients.append(pat)

        session.commit()

        print("=== Database Seeding Complete ===")
        print("\nSample Patient Identities for Verification:")
        for p in sample_patients:
            print(f"- Name: {p.first_name} {p.last_name} | DOB: {p.date_of_birth.isoformat()} | Phone: {p.phone}")

    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


if __name__ == "__main__":
    seed_data()
