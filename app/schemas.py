from pydantic import BaseModel, EmailStr
from typing import List, Optional
from datetime import datetime, time

# --- ESQUEMAS PARA CONSULTORIOS ---
class ConsultorioBase(BaseModel):
    nombre: str
    direccion: str
    telefono_recepcion: Optional[str] = None

class ConsultorioCreate(ConsultorioBase):
    pass

class Consultorio(ConsultorioBase):
    id: int
    doctor_id: int

    class Config:
        from_attributes = True

# --- ESQUEMAS PARA ESTUDIOS PDF ---
class EstudioPDFBase(BaseModel):
    nombre_archivo: str
    tipo_estudio: str

class EstudioPDF(EstudioPDFBase):
    id: int
    url_storage: str
    fecha_subida: datetime

    class Config:
        from_attributes = True

# --- ESQUEMAS PARA PACIENTES ---
class PacienteBase(BaseModel):
    nombre: str
    email: EmailStr
    telefono: str
    citas_totales_contratadas: Optional[int] = 1

class PacienteCreate(PacienteBase):
    pass

class Paciente(PacienteBase):
    id: int
    doctor_id: int
    # Esto permite incluir los estudios del paciente cuando lo consultemos
    estudios_pdf: List[EstudioPDF] = [] 

    class Config:
        from_attributes = True

# --- ESQUEMAS PARA CITAS ---
class CitaBase(BaseModel):
    fecha_hora: datetime
    motivo: str
    consultorio_id: int
    estado: Optional[str] = "programada"
    es_sugerida: Optional[bool] = False

class CitaCreate(CitaBase):
    paciente_id: int

class Cita(CitaBase):
    id: int
    doctor_id: int
    google_calendar_id: Optional[str] = None

    class Config:
        from_attributes = True

# --- ESQUEMAS PARA DOCTORES ---
class DoctorBase(BaseModel):
    nombre: str
    email: EmailStr
    especialidad: str

class DoctorCreate(DoctorBase):
    password: str # Solo se usa al crear el doctor

class Doctor(DoctorBase):
    id: int
    
    class Config:
        from_attributes = True