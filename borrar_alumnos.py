from pymongo import MongoClient
from dotenv import load_dotenv
import os

load_dotenv()

uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/gestion_docentes")
client = MongoClient(uri)

db = client.get_default_database()
if db is None:
    # para el caso de URI con base en la ruta
    db = client["gestion_docentes"]

res = db.alumnos.delete_many({})
print(f"Alumnos borrados: {res.deleted_count}")
