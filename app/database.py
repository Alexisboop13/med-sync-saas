from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

# 1. URL de la base de datos. 
# Usaremos SQLite por ahora para que sea 100% gratis y fácil de probar en el Senado.
SQLALCHEMY_DATABASE_URL = "sqlite:///./medical_app.db"

# 2. El "Engine" es el puente entre Python y la base de datos
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)

# 3. Cada vez que hablemos con la DB, usaremos una "Session"
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 4. Esta es la clase base que usaremos para crear nuestras tablas (Modelos)
Base = declarative_base()

# 5. Función útil para obtener la conexión y cerrarla automáticamente
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()