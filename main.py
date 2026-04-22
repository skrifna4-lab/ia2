import os
import re
import sqlite3
import random
import uvicorn
import asyncio
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from PyCharacterAI import get_client

app = FastAPI(title="Skrifna OS - Multi-Platform Edition")

# ==========================================
# CONFIGURACIÓN DE CORS (CROSS-ORIGIN)
# ==========================================
# Esto permite que cualquier app (Web, Móvil, Desktop) se conecte a tu API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permite cualquier link/plataforma
    allow_credentials=True,
    allow_methods=["*"],  # GET, POST, PUT, DELETE, etc.
    allow_headers=["*"],
)

if not os.path.exists("static"): os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")

DB_PATH = "skrifna.db"

def init_db():
    # Usamos context managers para asegurar que la conexión se cierre siempre
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS usuarios 
                          (user_id TEXT PRIMARY KEY, token TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS recursos 
                          (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                           user_id TEXT, tipo TEXT, alias TEXT, real_id TEXT,
                           FOREIGN KEY(user_id) REFERENCES usuarios(user_id))''')
        conn.commit()

init_db()

# Cache de sesiones
sesiones_activas = {}

# ==========================================
# ENDPOINTS DE GESTIÓN (MULTIPETICIONES)
# ==========================================

@app.get("/api/usuarios")
async def listar_usuarios():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM usuarios")
        return [row[0] for row in cursor.fetchall()]

@app.post("/api/login")
async def login(data: dict):
    try:
        user = data['user']
        token = data['token']
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO usuarios (user_id, token) VALUES (?, ?)", (user, token))
            conn.commit()
        return {"status": "success", "user": user}
    except KeyError:
        raise HTTPException(status_code=400, detail="Faltan campos 'user' o 'token'")

@app.post("/api/add_resource")
async def add_resource(data: dict):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO recursos (user_id, tipo, alias, real_id) VALUES (?, ?, ?, ?)", 
                       (data['user'], data['tipo'], data['alias'], data['real_id']))
        conn.commit()
    return {"status": "ok"}

@app.get("/api/data/{user}")
async def get_user_data(user: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT alias, real_id FROM recursos WHERE user_id = ? AND tipo = 'texto'", (user,))
        modelos = {row['alias']: row['real_id'] for row in cursor.fetchall()}
        cursor.execute("SELECT alias, real_id FROM recursos WHERE user_id = ? AND tipo = 'voz'", (user,))
        voces = {row['alias']: row['real_id'] for row in cursor.fetchall()}
        return {"modelos": modelos, "voces": voces}

# ==========================================
# MOTOR DE IA PARA MULTIPETICIÓN
# ==========================================

async def ejecutar_ia(u, m_alias, v_alias, msg):
    # Abrimos conexión por cada petición para evitar colisiones de hilos
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT token FROM usuarios WHERE user_id = ?", (u,))
        res_u = cursor.fetchone()
        cursor.execute("SELECT real_id FROM recursos WHERE user_id = ? AND alias = ? AND tipo = 'texto'", (u, m_alias))
        res_m = cursor.fetchone()
        cursor.execute("SELECT real_id FROM recursos WHERE user_id = ? AND alias = ? AND tipo = 'voz'", (u, v_alias))
        res_v = cursor.fetchone()

    if not all([res_u, res_m, res_v]):
        return {"error": f"Datos incompletos en DB para el usuario {u}"}

    token, real_char, real_voice = res_u[0], res_m[0], res_v[0]

    try:
        if token not in sesiones_activas:
            sesiones_activas[token] = await get_client(token=token)
        
        client = sesiones_activas[token]
        # Crear chat y enviar mensaje de forma asíncrona para no bloquear
        chat, _ = await client.chat.create_chat(real_char)
        answer = await client.chat.send_message(real_char, chat.chat_id, msg)
        
        texto_limpio = re.sub(r'\*.*?\*', '', answer.get_primary_candidate().text).strip()
        
        audio_url = await client.utils.generate_speech(
            chat.chat_id, 
            answer.turn_id, 
            answer.get_primary_candidate().candidate_id, 
            real_voice, 
            text_override=texto_limpio, 
            return_url=True
        )
        return {"user": u, "texto": texto_limpio, "audio": audio_url}
    except Exception as e:
        return {"error": str(e), "user": u}

@app.post("/models/kompleg")
async def kompleg(request: Request):
    """
    Soporta una sola petición o una lista de peticiones (multipetición).
    Ejemplo JSON: {"u": "...", "m": "...", "v": "...", "msg": "..."}
    O un Array: [{"u": "..."}, {"u": "..."}]
    """
    data = await request.json()
    
    # Si recibimos una lista de peticiones (Multipediciones)
    if isinstance(data, list):
        # Ejecutamos todas en paralelo usando asyncio.gather
        tareas = [ejecutar_ia(p['u'], p['m'], p['v'], p['msg']) for p in data]
        resultados = await asyncio.gather(*tareas)
        return resultados
    
    # Si es una petición única
    return await ejecutar_ia(data['u'], data['m'], data['v'], data['msg'])

@app.get("/", response_class=HTMLResponse)
async def root():
    try:
        with open("static/index.html", "r", encoding="utf-8") as f: 
            return f.read()
    except:
        return "<h1>Skrifna OS API Online</h1><p>CORS Habilitado / Multipetición lista.</p>"

if __name__ == "__main__":
    # Host 0.0.0.0 es clave para recibir peticiones externas
    uvicorn.run(app, host="0.0.0.0", port=8000)
