from fastapi import FastAPI
from app.routers import viz_pro, scientific_db

app = FastAPI()

app.include_router(viz_pro.router)
app.include_router(scientific_db.router)