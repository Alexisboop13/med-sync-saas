from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Boolean, Time
from sqlalchemy.orm import relationship
from .database import Base
import datetime

class Doctor(Base):
    __tablename__ = "doctors"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    especialidad = Column(String)
    
    # Relaciones
    pacientes = relationship("Paciente", back_populates="doctor")
    horarios = relationship("HorarioLaboral", back_populates="doctor")
    consultorios = relationship("Consultorio", back_populates="doctor")

class Consultorio(Base):
    __tablename__ = "consultorios"
    id = Column(Integer, primary_key=True)
    nombre = Column(String) # Ej: "Clínica Santa María" o "Consultorio Sur"
    direccion = Column(String)
    telefono_recepcion = Column(String, nullable=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id"))
    
    doctor = relationship("Doctor", back_populates="consultorios")
    citas = relationship("Cita", back_populates="consultorio")

class HorarioLaboral(Base):
    __tablename__ = "horarios_laborales"
    id = Column(Integer, primary_key=True)
    dia_semana = Column(Integer) # 0=Lunes, 6=Domingo
    hora_inicio = Column(Time)
    hora_fin = Column(Time)
    doctor_id = Column(Integer, ForeignKey("doctors.id"))
    consultorio_id = Column(Integer, ForeignKey("consultorios.id")) # Para saber dónde está cada día
    
    doctor = relationship("Doctor", back_populates="horarios")

class Paciente(Base):
    __tablename__ = "pacientes"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, index=True)
    email = Column(String)
    telefono = Column(String)
    citas_totales_contratadas = Column(Integer, default=1)
    doctor_id = Column(Integer, ForeignKey("doctors.id"))
    
    doctor = relationship("Doctor", back_populates="pacientes")
    citas = relationship("Cita", back_populates="paciente")
    estudios_pdf = relationship("EstudioPDF", back_populates="paciente")

class Cita(Base):
    __tablename__ = "citas"
    id = Column(Integer, primary_key=True, index=True)
    fecha_hora = Column(DateTime)
    motivo = Column(String)
    estado = Column(String, default="programada")
    es_sugerida = Column(Boolean, default=False)
    google_calendar_id = Column(String, nullable=True)
    
    paciente_id = Column(Integer, ForeignKey("pacientes.id"))
    doctor_id = Column(Integer, ForeignKey("doctors.id"))
    consultorio_id = Column(Integer, ForeignKey("consultorios.id")) # Ubicación de la cita
    
    paciente = relationship("Paciente", back_populates="citas")
    consultorio = relationship("Consultorio", back_populates="citas")

class EstudioPDF(Base):
    __tablename__ = "estudios_pdf"
    id = Column(Integer, primary_key=True)
    nombre_archivo = Column(String)
    url_storage = Column(String)
    fecha_subida = Column(DateTime, default=datetime.datetime.utcnow)
    tipo_estudio = Column(String)
    
    paciente_id = Column(Integer, ForeignKey("pacientes.id"))
    paciente = relationship("Paciente", back_populates="estudios_pdf")