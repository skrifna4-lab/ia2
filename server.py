import os
import re
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from PyCharacterAI import get_client

# =========================
# CARGA DE ENTORNO
# =========================
load_dotenv()

CHARACTER_TOKEN = os.getenv("CHARACTER_TOKEN")
CHARACTER_ID = os.getenv("CHARACTER_ID")
VOICE_ID = os.getenv("VOICE_ID")

app = FastAPI()

# =========================
# CORS ABIERTO
# =========================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# ARCHIVOS ESTÁTICOS
# =========================
if not os.path.exists("static"):
    os.makedirs("static")

app.mount("/static", StaticFiles(directory="static"), name="static")

# =========================
# CLIENTE CHARACTER AI
# =========================
client = None
chat = None

@app.on_event("startup")
async def startup():
    global client, chat
    try:
        client = await get_client(token=CHARACTER_TOKEN)
        chat, _ = await client.chat.create_chat(CHARACTER_ID)
        print("🚀 SERVIDOR ONLINE — MODO SOLO CONVERSACIÓN")
    except Exception as e:
        print(f"❌ Error conectando a CharacterAI: {e}")

# =========================
# PROMPT BASE (ANTI-ROLEO)
# =========================
BASE_PROMPT = """
Eres una IA conversacional.
NO eres un personaje de rol.

PROHIBIDO ABSOLUTAMENTE:
- Describir acciones, gestos o posturas.
- Usar asteriscos (*), emojis o narración.
- Decir lo que haces, ves o sientes.
- Crear escenas o contexto ficticio.

ESTILO:
- Habla como chica bestia.
- Un poco tontita, pero entiendes bien.
- Usa muletillas suaves como "miau".
- Florea un poco, sin exagerar.

FORMA:
- Solo habla y explica.
- Texto plano.
- Usa saltos de línea.
- Máximo 1000 caracteres.
"""

# =========================
# ROOT (OPCIONAL)
# =========================
@app.get("/", response_class=HTMLResponse)
async def root():
    index_path = os.path.join("static", "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Servidor de voz y texto activo</h1>"

# =========================
# ENDPOINT PRINCIPAL: HABLAR
# =========================
@app.post("/hablar")
async def hablar(request: Request):
    data = await request.json()
    mensaje = data.get("mensaje", "").strip()

    if not mensaje:
        return JSONResponse(
            status_code=400,
            content={"error": "Mensaje vacío"}
        )

    prompt = f"""
{BASE_PROMPT}

Usuario dice:
{mensaje}
"""

    try:
        answer = await client.chat.send_message(
            CHARACTER_ID,
            chat.chat_id,
            prompt
        )

        texto_raw = answer.get_primary_candidate().text

        # =========================
        # LIMPIEZA EXTRA (BLINDAJE)
        # =========================
        texto_limpio = re.sub(r'\*.*?\*', '', texto_raw)
        texto_limpio = re.sub(r'[🌀-🫿]', '', texto_limpio)
        texto_limpio = re.sub(r'[<>#_`|]', '', texto_limpio)
        texto_limpio = texto_limpio.strip()

        audio_url = await client.utils.generate_speech(
            chat.chat_id,
            answer.turn_id,
            answer.get_primary_candidate().candidate_id,
            VOICE_ID,
            text_override=texto_limpio,
            return_url=True
        )

        return {
            "texto": texto_limpio,
            "audio_url": audio_url
        }

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
