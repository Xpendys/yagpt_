from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Body
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import os, shutil
import threading
import logging

from backend.database import SessionLocal, init_db
from backend.models import User, UserFile
from backend.auth import verify_password, get_password_hash, create_access_token
from backend.config import YANDEX_API_KEY, YANDEX_FOLDER_ID, FILES_DIR
from backend.bot_manager import BotManager
import requests

from typing import Optional
from pydantic import BaseModel

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
init_db()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    from jose import jwt, JWTError
    from backend.auth import SECRET_KEY, ALGORITHM
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = db.query(User).filter(User.username == username).first()
    if user is None or not user.is_active:
        raise credentials_exception
    return user

class UserCreate(BaseModel):
    username: str
    password: str
    is_admin: Optional[bool] = False

class UserProfile(BaseModel):
    tg_bot_token: Optional[str] = None
    vk_token: Optional[str] = None
    system_prompt: Optional[str] = None

class AskRequest(BaseModel):
    prompt: str
    user_id: Optional[int] = None

@app.post("/token")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User is disabled")
    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/admin/create_user")
def create_user(user: UserCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not enough rights")
    db_user = db.query(User).filter(User.username == user.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Username already registered")
    user_obj = User(
        username=user.username,
        password_hash=get_password_hash(user.password),
        is_admin=user.is_admin
    )
    db.add(user_obj)
    db.commit()
    db.refresh(user_obj)
    return {"msg": "User created", "user_id": user_obj.id}

@app.post("/admin/block_user")
def block_user(username: str = Body(...), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not enough rights")
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = False
    db.commit()
    return {"msg": "User blocked"}

@app.get("/me")
def get_profile(current_user: User = Depends(get_current_user)):
    return {
        "username": current_user.username,
        "tg_bot_token": current_user.tg_bot_token,
        "vk_token": current_user.vk_token,
        "system_prompt": current_user.system_prompt
    }

@app.post("/me")
def update_profile(profile: UserProfile, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if profile.tg_bot_token is not None:
        current_user.tg_bot_token = profile.tg_bot_token
    if profile.vk_token is not None:
        current_user.vk_token = profile.vk_token
    if profile.system_prompt is not None:
        current_user.system_prompt = profile.system_prompt
    db.commit()
    return {"msg": "Profile updated"}

@app.post("/upload_file/")
def upload_file(file: UploadFile = File(...), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    os.makedirs(FILES_DIR, exist_ok=True)
    file_location = os.path.join(FILES_DIR, file.filename)
    with open(file_location, "wb") as f:
        shutil.copyfileobj(file.file, f)
    user_file = UserFile(
        user_id=current_user.id,
        filename=file.filename,
        filetype=file.content_type,
        filepath=file_location
    )
    db.add(user_file)
    db.commit()
    return {"filename": file.filename}

@app.get("/my_files/")
def list_files(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    files = db.query(UserFile).filter(UserFile.user_id == current_user.id).all()
    return [{"filename": f.filename, "filetype": f.filetype, "filepath": f.filepath} for f in files]

def get_yandex_gpt_response(prompt, system_prompt):
    try:
        response = requests.post(
            "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
            headers={
                "Authorization": f"Api-Key {YANDEX_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt",
                "completionOptions": {
                    "temperature": 0.7,
                    "maxTokens": 2000
                },
                "messages": [
                    {
                        "role": "system",
                        "text": system_prompt
                    },
                    {
                        "role": "user",
                        "text": prompt
                    }
                ]
            }
        )
        response.raise_for_status()
        return response.json()["result"]["alternatives"][0]["message"]["text"]
    except Exception as e:
        logger.error(f"Ошибка при обращении к Yandex GPT: {e}")
        return "Извините, произошла ошибка при генерации ответа."

@app.post("/ask/")
def ask_question(request: AskRequest, db: Session = Depends(get_db)):
    if request.user_id:
        user = db.query(User).filter(User.id == request.user_id).first()
        if user and user.system_prompt:
            # Получаем ответ от Yandex GPT с учетом системного промпта
            response = get_yandex_gpt_response(request.prompt, user.system_prompt)
            return {"answer": response}
    
    # Default response if no system prompt or invalid format
    return {"answer": "Извините, я не знаю, как ответить на это сообщение."}

# Запуск менеджера ботов в отдельном потоке при старте приложения
def start_bot_manager():
    manager = BotManager()
    manager.run()

bot_manager_thread = threading.Thread(target=start_bot_manager, daemon=True)
bot_manager_thread.start()