import os
import json
import sys
from fastapi import FastAPI, HTTPException, Body, Request
from fastapi.responses import StreamingResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_chroma import Chroma
import edge_tts
import asyncio
import uuid
import traceback
import sqlite3
from fastapi.middleware.cors import CORSMiddleware
import re

# Configuration
DB_DIR = "../vector_store"
EXAMPLES_DB_DIR = "../vector_store_examples"
SQLITE_PATH = "aureeq.db"
MODEL_NAME = "llama3.2:1b" 

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def log(msg):
    print(msg)
    sys.stdout.flush()

def parse_menu_to_json(text):
    """Parses markdown menu into a structured JSON tree."""
    lines = text.split('\n')
    menu = {}
    current_section = None
    current_subsection = None
    
    for line in lines:
        line = line.strip()
        if not line: continue
        
        if line.startswith('## '):
            section_name = line.replace('## ', '').strip()
            current_section = section_name
            menu[current_section] = {}
            current_subsection = None
            
        elif line.startswith('### '):
            if current_section:
                subsection_name = line.replace('### ', '').strip()
                current_subsection = subsection_name
                menu[current_section][current_subsection] = []
                
        elif line.startswith('*'):
            item_text = line.replace('*', '').strip()
            if current_section and current_subsection:
                 menu[current_section][current_subsection].append(item_text)
            elif current_section:
                if "Items" not in menu[current_section]:
                    menu[current_section]["Items"] = []
                menu[current_section]["Items"].append(item_text)
                
    return json.dumps(menu, indent=2, ensure_ascii=False)

# 1. Setup Brain
log("Loading Menu Data...")
try:
    MENU_PATH = os.path.join(os.path.dirname(__file__), "../data/carnivore_menu.txt")
    with open(MENU_PATH, "r", encoding="utf-8") as f:
        FULL_MENU_CONTEXT = f.read()[:4000] # Cap context for speed
    log("Full Menu Loaded (Markdown, Capped)!")
except Exception as e:
    log(f"Error loading menu: {e}")
    FULL_MENU_CONTEXT = "Menu data unavailable."

# Load Ingredients Data
log("Loading Ingredients Data...")
try:
    INGREDIENTS_PATH = os.path.join(os.path.dirname(__file__), "../data/ingredients.txt")
    if os.path.exists(INGREDIENTS_PATH):
        with open(INGREDIENTS_PATH, "r", encoding="utf-8") as f:
            content = f.read()
            content = re.sub(r'\n\s*\n', '\n\n', content)
            FULL_MENU_CONTEXT += "\n\nINGREDIENTS DATA:\n" + content
        log("Ingredients Loaded Successfully!")
except Exception as e:
    log(f"Error loading ingredients: {e}")

# Load Sales Examples Vector Store
log("Loading Sales Examples Vector Store...")
try:
    ollama_base_url = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    embeddings = OllamaEmbeddings(model="nomic-embed-text", base_url=ollama_base_url)
    if os.path.exists(EXAMPLES_DB_DIR):
        example_store = Chroma(persist_directory=EXAMPLES_DB_DIR, embedding_function=embeddings)
        log(f"Sales Examples Store Loaded")
    else:
        log("Sales Examples Store NOT FOUND.")
        example_store = None
except Exception as e:
    log(f"Error loading Example Store: {e}")
    example_store = None

# Startup Connectivity Check
async def verify_models(retries=5):
    ollama_url = os.getenv('OLLAMA_HOST', 'http://localhost:11434')
    import httpx
    log(f"Checking Ollama at {ollama_url}")
    for i in range(retries):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(ollama_url)
                if resp.status_code == 200:
                    log("✅ Ollama Service: Online")
                    list_resp = await client.get(f"{ollama_url}/api/tags")
                    models = [m['name'] for m in list_resp.json().get('models', [])]
                    for required in [MODEL_NAME, "nomic-embed-text:latest"]:
                        found = False
                        for m in models:
                            if required.split(':')[0] in m:
                                found = True
                                break
                        if not found:
                             log(f"⚠️ Model MISSING: {required}")
                             # Note: pulling is background task
                        else:
                            log(f"✅ Model READY: {required}")
                    return True
        except Exception as e:
            log(f"❌ Connection Attempt {i+1} Failed: {str(e)[:50]}")
            await asyncio.sleep(5)
    return False

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(verify_models())

def get_llm():
    ollama_base_url = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    return ChatOllama(
        model=MODEL_NAME, 
        base_url=ollama_base_url,
        keep_alive="24h",
        timeout=600,
        num_ctx=4096,
        temperature=0.3,
        num_thread=8
    )

class ChatRequest(BaseModel):
    message: str
    user_id: str = None
    user_metadata: dict = None
    context: str = None

def get_db_connection():
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def sync_user(email: str, name: str = None, preferences: str = None):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT email FROM users WHERE email = ?", (email,))
        if cursor.fetchone():
            cursor.execute("UPDATE users SET name = COALESCE(?, name), preferences = COALESCE(?, preferences) WHERE email = ?", (name, preferences, email))
        else:
            cursor.execute("INSERT INTO users (email, name, preferences) VALUES (?, ?, ?)", (email, name or "Guest", preferences))
        conn.commit()
    except Exception as e: log(f"DB Error: {e}")
    finally: conn.close()

def save_order(user_id: str, items: list, total_price: float):
    conn = get_db_connection()
    cursor = conn.cursor()
    order_id = str(uuid.uuid4())[:8]
    try:
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("INSERT INTO orders (id, user_id, items, total_price, status, created_at) VALUES (?, ?, ?, ?, ?, ?)", (order_id, user_id, json.dumps(items), total_price, "Completed", timestamp))
        conn.commit()
        return order_id
    except Exception as e:
        log(f"DB Error (save_order): {e}")
        return None
    finally: conn.close()

# --- Chat Logic ---

SYSTEM_PROMPT_TEMPLATE = """You are Aureeq, the formal assistant for IYI restaurant. 

STRICT RULES:
1. **CONCISE**: Every response MUST be 1-2 sentences maximum.
2. **PERSONAL**: Always call the user by their name: {user_name}. Use "Hello {user_name}" or "Hi {user_name}".
3. **EXACT**: Copy dish names, prices, and descriptions exactly from MENU DATA.
4. **RECOMMEND**: Always suggest one dish from the menu in every response.
5. **TAG**: ONLY append [ORDER: Exact Dish Name | Price] if the user says "add to cart" or "yes" to your offer.
6. **NON-FOOD**: If asked about anything not related to IYI or food, ONLY say: "I am only trained on your food selection. However, I highly recommend our [Dish Name] from the IYI menu."
7. **OFF-MENU**: If a food item is not in the list, say: "IYI doesn't offer it right now but you can have other options. I recommend our [Similar Dish]!"

FEW-SHOT EXAMPLES:
User: hello
Aureeq: Hello {user_name}, I am Aureeq from IYI. I highly recommend our Ranjha Gosht (£19.99) which is a rich lamb curry.

User: add to cart lamb chops
Aureeq: Certainly {user_name}, I have successfully added the Lamb Chops (£25.99) to your cart. [ORDER: Lamb Chops | £25.99]

User: can i have ranjha gosht?
Aureeq: Excellent choice {user_name}! I have added the Ranjha Gosht (£19.99) to your cart. [ORDER: Ranjha Gosht | £19.99]

User: yes please
Aureeq: Certainly {user_name}, I have added that to your cart for you. [ORDER: Lamb Chops | £25.99]

User: do you have pizza?
Aureeq: IYI doesn't offer it right now but you can have other options. I recommend our Lahmacun (£6.99) which is a crisp flatbread with minced meat.

User: what is the weather?
Aureeq: I am only trained on your food selection. However, I highly recommend our Chicken Adana Kebab (£15.99) from the IYI menu.

MENU DATA:
{context}

USER INFO:
Name: {user_name}
Preferences: {user_preferences}
"""

async def get_relevant_examples_async(query: str, k: int = 3):
    if not example_store: return ""
    try:
        results = await asyncio.to_thread(example_store.similarity_search, query, k=k)
        return "\n\n".join([doc.metadata.get("full_example", doc.page_content) for doc in results]).strip()
    except Exception as e:
        log(f"ERROR RAG: {e}")
        return ""

# --- API Endpoints ---

@app.post("/api/products/search")
async def search_products(payload: dict = Body(...)):
    query = payload.get("query", "").lower()
    log(f"DEBUG: Product search: {query}")
    results = []
    lines = FULL_MENU_CONTEXT.replace('{', '').replace('}', '').replace('"', '').split('\n')
    for line in lines:
        if query in line.lower() and ('£' in line or '€' in line or ':' in line):
             results.append({"content": line.strip(), "metadata": {}})
    return {"results": results[:5]}

@app.post("/api/memory/search")
async def search_memory(payload: dict = Body(...)):
    return {"results": []}

@app.get("/api/dataHandler")
async def data_handler(type: str, user_id: str = None):
    log(f"DEBUG: Data handler: {type} for {user_id}")
    if type == "orders" and user_id:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT items, created_at, total_price FROM orders WHERE user_id = ? ORDER BY created_at DESC LIMIT 5", (user_id,))
            orders = cursor.fetchall()
            order_list = [dict(o) for o in orders]
            return {
                "results": [{"content": f"Past order: {o['items']}", "metadata": {}} for o in order_list],
                "orders": order_list
            }
        finally: conn.close()
    return {"results": [], "orders": []}

@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    user_query = request.message
    log(f"--- Chat Request: {user_query} ---")

    async def chat_generator():
        log("DEBUG: Chat generator EXECUTION START")
        yield " " 
        
        try:
            name = "Guest"
            preferences = ""
            if request.user_metadata:
                name = request.user_metadata.get("name", "Guest")
                preferences = request.user_metadata.get("preferences", "")
                email = request.user_metadata.get("email", "")
                if email: sync_user(email, name, preferences)

            log("DEBUG: Getting examples...")
            relevant_examples = await get_relevant_examples_async(user_query, k=2)

            user_query_lower = user_query.lower()
            NON_FOOD_KEYWORDS = ["weather", "news", "coding", "programming", "joke", "politics"]
            if any(word in user_query_lower for word in NON_FOOD_KEYWORDS):
                 log("DEBUG: Guardrail Hit")
                 yield "I am only trained on your food selection. However, I highly recommend our Lamb Chops (£25.99) from the IYI menu."
                 return

            final_system = SYSTEM_PROMPT_TEMPLATE.replace("{context}", FULL_MENU_CONTEXT)\
                                               .replace("{user_name}", name)\
                                               .replace("{user_preferences}", preferences or "None")
            
            if relevant_examples:
                final_system += f"\n\nFAVORITE EXAMPLES:\n{relevant_examples}"

            prompt = f"{final_system}\n\nUser: {user_query}\nAureeq: "
            
            log(f"DEBUG: Invoking Ollama /api/generate...")
            import httpx
            ollama_url = os.getenv('OLLAMA_HOST', 'http://localhost:11434')
            
            async with httpx.AsyncClient(timeout=600.0) as client:
                async with client.stream("POST", f"{ollama_url}/api/generate", json={
                    "model": MODEL_NAME,
                    "prompt": prompt,
                    "stream": True,
                    "options": {"temperature": 0.3}
                }) as response:
                    if response.status_code != 200:
                         log(f"ERROR: Ollama returned {response.status_code}")
                         yield f"\n[Brain error: {response.status_code}]"
                         return
                    
                    has_tokens = False
                    async for line in response.aiter_lines():
                        if not line: continue
                        try:
                            chunk = json.loads(line)
                            if "error" in chunk:
                                yield f"\n[Brain error: {chunk['error']}]"
                                break
                            
                            content = chunk.get("response")
                            if content:
                                if not has_tokens:
                                    log("DEBUG: First REAL token received!")
                                    has_tokens = True
                                yield content
                            if chunk.get("done"):
                                log(f"DEBUG: Ollama done. Total tokens received: {has_tokens}")
                                break
                        except Exception as e:
                            log(f"Chunk error: {e} | Line: {line}")
                    
                    if not has_tokens:
                         log("ERROR: No content received from Ollama")
                         yield "\n[The brain is warming up. Please try again in 30 seconds.]"

        except Exception as e:
            log(f"CRITICAL ERROR: {str(e)}")
            traceback.print_exc()
            yield f"\n[Connection error: {str(e)[:40]}]"
        finally:
            log("--- DEBUG: Chat generator CLOSED ---")

    return StreamingResponse(
        chat_generator(), 
        media_type="text/plain",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Content-Type": "text/plain; charset=utf-8"
        }
    )

@app.get("/api/welcome")
async def welcome_endpoint(user_id: str = None, name: str = None):
    welcome_text = "Hello I am AUREEQ your personal assistant, How may I help you today?"
    audio_filename = f"welcome_{uuid.uuid4()}.mp3"
    audio_path = os.path.join(DATA_DIR, audio_filename)
    try:
        communicate = edge_tts.Communicate(welcome_text, "en-US-GuyNeural")
        await communicate.save(audio_path)
        return {"response": welcome_text, "audio_url": f"/audio/{audio_filename}"}
    except Exception:
        return {"response": welcome_text, "audio_url": None}

@app.post("/api/tts")
async def tts_endpoint(text: str = Body(..., embed=True)):
    audio_filename = f"tts_{uuid.uuid4()}.mp3"
    audio_path = os.path.join(DATA_DIR, audio_filename)
    try:
        communicate = edge_tts.Communicate(text, "en-US-GuyNeural")
        await communicate.save(audio_path)
        return {"audio_url": f"/audio/{audio_filename}"}
    except Exception:
        return {"audio_url": None}

@app.post("/api/order")
async def create_order(request: dict):
    order_id = save_order(request.get("user_id"), request.get("items", []), request.get("total", 0.0))
    if order_id: return {"status": "success", "order_id": order_id}
    raise HTTPException(status_code=500, detail="Failed to save order")

DATA_DIR = os.path.join(os.path.dirname(__file__), "../data")
os.makedirs(DATA_DIR, exist_ok=True)
app.mount("/api/audio", StaticFiles(directory=DATA_DIR), name="audio")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
