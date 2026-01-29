from sqlalchemy.orm import Session
from sqlalchemy import or_
from . import models, schemas

# --- LÓGICA DE PACIENTES ---

def obtener_pacientes(db: Session, doctor_id: int, buscar: str = None):
    """
    Busca pacientes de un doctor específico. 
    Permite filtrar por nombre o teléfono (el primer paso para tu futuro ETL).
    """
    query = db.query(models.Paciente).filter(models.Paciente.doctor_id == doctor_id)
    if buscar:
        # Esto hace que la búsqueda no sea exacta (ignora mayúsculas/minúsculas)
        query = query.filter(
            or_(
                models.Paciente.nombre.ilike(f"%{buscar}%"),
                models.Paciente.telefono.contains(buscar)
            )
        )
    return query.all()

def crear_paciente(db: Session, paciente: schemas.PacienteCreate, doctor_id: int):
    db_paciente = models.Paciente(**paciente.model_dump(), doctor_id=doctor_id)
    db.add(db_paciente)
    db.commit()
    db.refresh(db_paciente)
    return db_paciente

# --- LÓGICA DE CITAS Y CONSULTORIOS ---

def agendar_cita(db: Session, cita: schemas.CitaCreate, doctor_id: int):
    # Aquí es donde el 'túnel' se vuelve inteligente.
    # Podrías agregar una validación: ¿El doctor ya tiene cita a esa hora?
    db_cita = models.Cita(**cita.model_dump(), doctor_id=doctor_id)
    db.add(db_cita)
    db.commit()
    db.refresh(db_cita)
    return db_cita

def crear_consultorio(db: Session, consultorio: schemas.ConsultorioCreate, doctor_id: int):
    db_consultorio = models.Consultorio(**consultorio.model_dump(), doctor_id=doctor_id)
    db.add(db_consultorio)
    db.commit()
    db.refresh(db_consultorio)
    return db_consultorio