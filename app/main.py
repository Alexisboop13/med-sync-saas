from fastapi import FastAPI, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List
from . import models, schemas, database, services

# Inicialización de la base de datos
models.Base.metadata.create_all(bind=database.engine)

app = FastAPI(title="MedSync SaaS - API")

# --- ENDPOINTS DE DOCTORES ---

@app.post("/doctores/", response_model=schemas.Doctor)
def crear_doctor(doctor: schemas.DoctorCreate, db: Session = Depends(database.get_db)):
    # Aquí podrías usar un servicio también, pero para el ejemplo lo mantenemos directo
    db_doctor = models.Doctor(
        nombre=doctor.nombre, 
        email=doctor.email, 
        hashed_password=doctor.password, 
        especialidad=doctor.especialidad
    )
    db.add(db_doctor)
    db.commit()
    db.refresh(db_doctor)
    return db_doctor

# --- ENDPOINTS DE PACIENTES (Usando Services) ---

@app.post("/pacientes/", response_model=schemas.Paciente)
def crear_nuevo_paciente(
    paciente: schemas.PacienteCreate, 
    doctor_id: int, 
    db: Session = Depends(database.get_db)
):
    return services.crear_paciente(db=db, paciente=paciente, doctor_id=doctor_id)

@app.get("/pacientes/", response_model=List[schemas.Paciente])
def listar_pacientes(
    doctor_id: int, 
    buscar: str = Query(None, description="Busca por nombre o teléfono"),
    db: Session = Depends(database.get_db)
):
    return services.obtener_pacientes(db=db, doctor_id=doctor_id, buscar=buscar)

# --- ENDPOINTS DE CITAS ---

@app.post("/citas/", response_model=schemas.Cita)
def programar_cita(
    cita: schemas.CitaCreate, 
    doctor_id: int, 
    db: Session = Depends(database.get_db)
):
    return services.agendar_cita(db=db, cita=cita, doctor_id=doctor_id)