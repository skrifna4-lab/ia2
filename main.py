import os
import re
import sqlite3
import asyncio
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from PyCharacterAI import Client

app = FastAPI(title="Skrifna OS - Enterprise Edition")

# --- CONFIGURACIÓN DE RUTAS ABSOLUTAS ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "skrifna.db")
STATIC_DIR = os.path.join(BASE_DIR, "static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if not os.path.exists(STATIC_DIR): os.makedirs(STATIC_DIR)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# --- BASE DE DATOS OPTIMIZADA ---
def init_db():
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

# Cache de clientes para no saturar CharacterAI
clientes_activos = {}

async def obtener_cliente(token):
    """Retorna un cliente autenticado o crea uno nuevo con headers de navegador."""
    if token in clientes_activos:
        return clientes_activos[token]
    
    try:
        # PyCharacterAI usa curl_cffi por debajo para saltar protecciones
        client = Client()
        await client.authenticate_with_token(token)
        clientes_activos[token] = client
        return client
    except Exception as e:
        print(f"Error de autenticación para token {token[:10]}: {e}")
        return None

# --- MOTOR DE IA RECONSTRUIDO ---
async def ejecutar_ia(u, m_alias, v_alias, msg):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT token FROM usuarios WHERE user_id = ?", (u,))
        res_u = cursor.fetchone()
        # Buscamos los IDs reales
        cursor.execute("SELECT real_id FROM recursos WHERE user_id = ? AND alias = ? AND tipo = 'texto'", (u, m_alias))
        res_m = cursor.fetchone()
        cursor.execute("SELECT real_id FROM recursos WHERE user_id = ? AND alias = ? AND tipo = 'voz'", (u, v_alias))
        res_v = cursor.fetchone()

    if not all([res_u, res_m, res_v]):
        return {"error": "Configuración incompleta en la base de datos.", "user": u}

    token, char_id, voice_id = res_u[0], res_m[0], res_v[0]
    
    client = await obtener_cliente(token)
    if not client:
        return {"error": "Token inválido o IP Bloqueada por CharacterAI", "user": u}

    try:
        # Enviar mensaje y limpiar formato de la IA
        # PyCharacterAI maneja los chats internamente, enviamos directo al char_id
        chat = await client.chat.create_chat(char_id)
        answer = await client.chat.send_message(char_id, chat.chat_id, msg)
        
        raw_text = answer.get_primary_candidate().text
        texto_final = re.sub(r'\*.*?\*', '', raw_text).strip()

        # Generar voz (Usando el método oficial de la librería)
        audio_url = await client.generate_voice(voice_id, texto_final)
        
        return {
            "user": u, 
            "texto": texto_final, 
            "audio": audio_url,
            "status": "success"
        }
    except Exception as e:
        # Si hay error, limpiamos el cliente para que la próxima vez intente reconectar
        clientes_activos.pop(token, None)
        return {"error": f"Fallo en la comunicación: {str(e)}", "user": u}

@app.post("/models/kompleg")
async def kompleg(request: Request):
    data = await request.json()
    if isinstance(data, list):
        tareas = [ejecutar_ia(p['u'], p['m'], p['v'], p['msg']) for p in data]
        return await asyncio.gather(*tareas)
    return await ejecutar_ia(data['u'], data['m'], data['v'], data['msg'])

@app.get("/")
async def root():
    return {"message": "Skrifna OS API is running", "db_status": "connected"}

if __name__ == "__main__":
    import uvicorn
    # Importante: host 0.0.0.0 para que el VPS sea accesible
    uvicorn.run(app, host="0.0.0.0", port=8000)
