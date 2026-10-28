import os
import json
import uuid
import math
import httpx
from typing import Optional, List
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from dotenv import load_dotenv

from database import SessionLocal, init_db, Place, Booking, Poll
from osm_sync import sync_rostov_places, seed_fallback_rostov

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

app = FastAPI(title="VibeWalk API — Ростов-на-Дону")

# Разрешаем запросы с GitHub Pages и локального фронтенда
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.on_event("startup")
async def startup_event():
    init_db()
    db = SessionLocal()
    if db.query(Place).count() == 0:
        seed_fallback_rostov(db)
    db.close()


# --- СХЕМЫ ЗАПРОСОВ ---
class ChatRequest(BaseModel):
    message: str
    guests_count: int = 2


class RouteRequest(BaseModel):
    route_type: str
    guests_count: int = 2


class BookingRequest(BaseModel):
    place_name: str
    guest_name: str
    guests_count: int
    visit_time: str


class PollCreateRequest(BaseModel):
    guests_count: int
    places: List[dict]


class PollVoteRequest(BaseModel):
    place_name: str


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (ИИ + ГЕОМЕТРИЯ) ---
def haversine_km(lat1, lon1, lat2, lon2) -> float:
    """Расчет реального расстояния между двумя точками в Ростове (в км)."""
    if not all([lat1, lon1, lat2, lon2]):
        return 1.2
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


async def analyze_query_with_ai(query: str, guests_count: int) -> dict:
    """Обращается к бесплатному Gemini API или Groq API для разбора запроса пользователя."""
    system_prompt = f"""
    Ты — Волчок, дружелюбный ИИ-гид по Ростову-на-Дону в сервисе VibeWalk.
    Проанализируй запрос пользователя (сейчас выбрано гостей: {guests_count}) и верни СТРОГО валидный JSON без маркдауна:
    {{
      "category": "Кафе/Кофейня" | "Ресторан" | "Бар" | "Развлечения" | "Необычные места" | null,
      "max_budget_per_person": число (в рублях на 1 человека) или 10000,
      "guests_detected": число гостей (если упомянуто в тексте, иначе {guests_count}),
      "keywords": ["список", "ключевых", "слов", "или", "улиц"],
      "wolf_intro": "Короткий живой ответ от лица Волчка (1-2 предложения) с объяснением подбора"
    }}
    """

    # 1. Пробуем Google Gemini Flash (если задан ключ GEMINI_API_KEY)
    if GEMINI_API_KEY:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
            payload = {
                "contents": [{"parts": [{"text": f"{system_prompt}\nЗапрос: {query}"}]}],
                "generationConfig": {"responseMimeType": "application/json"}
            }
            async with httpx.AsyncClient(timeout=12.0) as client:
                r = await client.post(url, json=payload)
                if r.status_code == 200:
                    text_resp = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                    return json.loads(text_resp)
        except Exception as e:
            print(f"Gemini fallback: {e}")

    # 2. Пробуем Groq Llama 3.3 (если задан ключ GROQ_API_KEY)
    if GROQ_API_KEY:
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
            payload = {
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query}
                ],
                "response_format": {"type": "json_object"}
            }
            async with httpx.AsyncClient(timeout=12.0) as client:
                r = await client.post(url, headers=headers, json=payload)
                if r.status_code == 200:
                    return json.loads(r.json()["choices"][0]["message"]["content"])
        except Exception as e:
            print(f"Groq fallback: {e}")

    # 3. Умный локальный NLP-разбор (работает без ключей)
    q = query.lower()
    detected_guests = guests_count
    if any(w in q for w in ["один", "одна", "самому"]):
        detected_guests = 1
    elif any(w in q for w in ["вдвоем", "двое", "свидан", "девушк"]):
        detected_guests = 2
    elif any(w in q for w in ["трое", "втроем", "нас 3"]):
        detected_guests = 3
    elif any(w in q for w in ["четверо", "вчетвером", "нас 4", "компан"]):
        detected_guests = max(4, guests_count)

    cat = None
    if any(w in q for w in ["кофе", "кафе", "завтрак", "круассан", "десерт"]):
        cat = "Кафе/Кофейня"
    elif any(w in q for w in ["ресторан", "ужин", "поесть", "паст", "мяс", "вин"]):
        cat = "Ресторан"
    elif any(w in q for w in ["бар", "выпить", "крафт", "коктейл", "паб", "танц"]):
        cat = "Бар"
    elif any(w in q for w in ["парк", "кино", "квест", "развлеч", "активн"]):
        cat = "Развлечения"
    elif any(w in q for w in ["необычн", "секрет", "арт", "тиферблат"]):
        cat = "Необычные места"

    import re
    max_b = 10000
    m = re.search(r"(\d{3,5})", q)
    if m:
        val = int(m.group(1))
        max_b = round(val / detected_guests) if (detected_guests > 1 and val >= 1600 and "чел" not in q) else val
    elif any(w in q for w in ["недорог", "дешев", "бюджет"]):
        max_b = 900

    keywords = [w for w in ["пушкинская", "садовая", "суворова", "тихо", "уютно", "свидание", "веранда"] if w in q]

    return {
        "category": cat,
        "max_budget_per_person": max_b,
        "guests_detected": detected_guests,
        "keywords": keywords,
        "wolf_intro": f"Я проверил актуальную базу Ростова-на-Дону для компании из {detected_guests} чел. и выбрал места, которые лучше всего подходят под твой вайб 🐾"
    }


# --- API ЭНДПОИНТЫ ---

@app.get("/api/stats")
def get_stats(db: Session = Depends(get_db)):
    total = db.query(Place).count()
    bookings = db.query(Booking).count()
    return {"total_places": total, "total_bookings": bookings}


@app.post("/api/sync")
async def trigger_osm_sync(db: Session = Depends(get_db)):
    """Запускает живую выгрузку всех заведений Ростова-на-Дону из OpenStreetMap."""
    total = await sync_rostov_places(db)
    return {
        "status": "ok",
        "total_places": total,
        "message": f"База успешно синхронизирована с OpenStreetMap! Доступно заведений Ростова: {total}"
    }


@app.get("/api/places")
def list_places(
    category: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 60,
    db: Session = Depends(get_db)
):
    query = db.query(Place)
    if category and category != "Все":
        query = query.filter(Place.category == category)
    if search:
        like_str = f"%{search.strip()}%"
        query = query.filter((Place.name.ilike(like_str)) | (Place.address.ilike(like_str)))

    places = query.order_by(Place.is_partner.desc(), Place.rating.desc()).limit(limit).all()
    return {"total": query.count(), "items": [p.to_dict() for p in places]}


@app.post("/api/chat")
async def chat_with_wolf(req: ChatRequest, db: Session = Depends(get_db)):
    parsed = await analyze_query_with_ai(req.message, req.guests_count)
    guests = parsed.get("guests_detected") or req.guests_count
    max_b = parsed.get("max_budget_per_person") or 10000
    cat = parsed.get("category")
    keywords = parsed.get("keywords") or []

    all_places = db.query(Place).all()
    scored = []

    for p in all_places:
        score = 0
        reasons = []

        if p.budget_per_person <= max_b:
            score += 4
        else:
            score -= 4

        if cat and p.category == cat:
            score += 6
            reasons.append(f"формат «{p.category}»")

        p_text = f"{p.name} {p.address} {' '.join(p.tags)}".lower()
        for kw in keywords:
            if kw.lower() in p_text:
                score += 4
                reasons.append(f"совпадение «{kw}»")

        if p.is_partner and score > 0:
            score += 1.5  # Контекстный буст партнера при совпадении с запросом

        scored.append((score, p, reasons))

    scored.sort(key=lambda x: (x[0], x[1].rating), reverse=True)
    top = [item for item in scored if item[0] > 0][:3]
    if not top:
        top = scored[:3]

    results = []
    for idx, (sc, place, r_list) in enumerate(top):
        d = place.to_dict()
        d["reasonText"] = ", ".join(r_list) if r_list else f"высокий рейтинг ({place.rating}⭐) и отличный чек"
        d["is_sponsor_card"] = (idx == 0 and place.is_partner)
        results.append(d)

    return {
        "wolf_intro": parsed.get("wolf_intro"),
        "guests_count": guests,
        "max_budget_per_person": max_b,
        "places": results
    }


@app.post("/api/routes")
def generate_smart_route(req: RouteRequest, db: Session = Depends(get_db)):
    """Строит маршрут из 3 реальных точек Ростова с расчетом расстояния пешком и общего бюджета."""
    t = req.route_type
    if t == "Гастротур":
        title = "🗺️ Маршрут «Гастротур по Ростову»"
        cats = ["Кафе/Кофейня", "Ресторан", "Бар"]
    elif t == "Романтический вечер":
        title = "❤️ Маршрут «Романтический вечер»"
        cats = ["Необычные места", "Ресторан", "Кафе/Кофейня"]
    elif t == "Барный маршрут":
        title = "🍹 Маршрут «Барный тур по центру»"
        cats = ["Бар", "Бар", "Бар"]
    else:
        title = "🎭 Маршрут «Культурный вайб Ростова»"
        cats = ["Развлечения", "Кафе/Кофейня", "Ресторан"]

    selected = []
    used_ids = set()

    for step_idx, c in enumerate(cats):
        candidates = db.query(Place).filter(Place.category == c, ~Place.id.in_(used_ids)).all()
        if not candidates:
            candidates = db.query(Place).filter(~Place.id.in_(used_ids)).all()
        if not candidates:
            break

        if step_idx == 0:
            chosen = sorted(candidates, key=lambda x: x.rating, reverse=True)[0]
            dist_text = "Старт прогулки в центре города"
        else:
            prev = selected[-1]["place_obj"]
            # Ищем ближайшую локацию по координатам
            candidates.sort(key=lambda x: haversine_km(prev.lat, prev.lon, x.lat, x.lon))
            chosen = candidates[0]
            km = haversine_km(prev.lat, prev.lon, chosen.lat, chosen.lon)
            walk_min = max(4, min(25, int(km * 12)))
            dist_text = f"Около {walk_min} мин. пешком ({km:.1f} км) от предыдущей точки"

        used_ids.add(chosen.id)
        d = chosen.to_dict()
        d["stepTitle"] = f"Точка {step_idx + 1}"
        d["reasonText"] = dist_text
        d["place_obj"] = chosen
        selected.append(d)

    for item in selected:
        item.pop("place_obj", None)

    return {
        "title": title,
        "guests_count": req.guests_count,
        "places": selected
    }


@app.post("/api/bookings")
def create_booking(req: BookingRequest, db: Session = Depends(get_db)):
    b = Booking(
        place_name=req.place_name,
        guest_name=req.guest_name,
        guests_count=req.guests_count,
        visit_time=req.visit_time
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return {
        "status": "confirmed",
        "booking_id": b.id,
        "message": f"Бронь #{b.id} в «{b.place_name}» успешно сохранена в базе данных!"
    }


@app.post("/api/polls")
def create_poll(req: PollCreateRequest, db: Session = Depends(get_db)):
    poll_id = str(uuid.uuid4())[:8]
    votes = {p["name"]: 0 for p in req.places}
    poll = Poll(
        id=poll_id,
        guests_count=req.guests_count,
        places_json=json.dumps(req.places, ensure_ascii=False),
        votes_json=json.dumps(votes, ensure_ascii=False)
    )
    db.add(poll)
    db.commit()
    return {"poll_id": poll_id, "votes": votes}


@app.post("/api/polls/{poll_id}/vote")
def vote_in_poll(poll_id: str, req: PollVoteRequest, db: Session = Depends(get_db)):
    poll = db.query(Poll).filter(Poll.id == poll_id).first()
    if not poll:
        raise HTTPException(status_code=404, detail="Опрос не найден")
    votes = json.loads(poll.votes_json)
    votes[req.place_name] = votes.get(req.place_name, 0) + 1
    poll.votes_json = json.dumps(votes, ensure_ascii=False)
    db.commit()
    return {"poll_id": poll_id, "votes": votes}
