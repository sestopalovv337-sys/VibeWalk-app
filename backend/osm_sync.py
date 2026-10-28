import json
import random
import httpx
from sqlalchemy.orm import Session
from database import Place

# Координаты границ Ростова-на-Дону (Юг, Запад, Север, Восток)
ROSTOV_BBOX = "47.16,39.58,47.33,39.86"

OVERPASS_QUERY = f"""
[out:json][timeout:45];
(
  node["amenity"~"cafe|restaurant|bar|pub|fast_food|cinema|theatre|arts_centre"]({ROSTOV_BBOX});
  way["amenity"~"cafe|restaurant|bar|pub|cinema|theatre|arts_centre"]({ROSTOV_BBOX});
  node["leisure"~"park|escape_game|bowling_alley"]({ROSTOV_BBOX});
);
out center tags;
"""


def classify_osm_element(tags: dict):
    amenity = tags.get("amenity", "")
    leisure = tags.get("leisure", "")
    cuisine = tags.get("cuisine", "").lower()

    place_tags = []
    if amenity in ["cafe", "fast_food"] or "coffee" in cuisine:
        category = "Кафе/Кофейня"
        budget = random.choice([450, 600, 750, 900, 1100])
        place_tags.extend(["кофе", "завтрак", "десерты", "уютно", "работа"])
    elif amenity in ["bar", "pub"]:
        category = "Бар"
        budget = random.choice([1200, 1500, 1900, 2400, 2800])
        place_tags.extend(["коктейли", "вечер", "крафт", "музыка", "компания"])
    elif amenity == "restaurant":
        category = "Ресторан"
        budget = random.choice([1400, 1800, 2500, 3200, 4200])
        place_tags.extend(["ужин", "свидание", "вкусно", "вино"])
    elif amenity in ["cinema", "theatre"] or leisure in ["escape_game", "bowling_alley", "park"]:
        category = "Развлечения"
        budget = random.choice([600, 900, 1300, 1800])
        place_tags.extend(["активный отдых", "прогулка", "эмоции", "друзья"])
    else:
        category = "Необычные места"
        budget = random.choice([700, 1100, 1500])
        place_tags.extend(["арт-пространство", "необычно", "свидание", "отдых"])

    # Дополнительные теги из OpenStreetMap
    if "pizza" in cuisine or "italian" in cuisine:
        place_tags.append("пицца и паста")
    if "georgian" in cuisine:
        place_tags.append("грузинская кухня")
    if "asian" in cuisine or "sushi" in cuisine or "japanese" in cuisine:
        place_tags.append("азия")
    if "burger" in cuisine or "steak" in cuisine:
        place_tags.append("мясо")
    if tags.get("outdoor_seating") == "yes":
        place_tags.append("летняя веранда")
    if tags.get("internet_access") in ["wlan", "yes"]:
        place_tags.append("тихо")

    unique_tags = list(dict.fromkeys(place_tags))[:4]
    return category, budget, unique_tags


async def sync_rostov_places(db: Session) -> int:
    """Скачивает актуальные заведения Ростова-на-Дону из OpenStreetMap и обновляет БД."""
    url = "https://overpass-api.de/api/interpreter"
    added_or_updated = 0

    try:
        async with httpx.AsyncClient(timeout=50.0) as client:
            response = await client.post(url, data={"data": OVERPASS_QUERY})
            response.raise_for_status()
            data = response.json()

        elements = data.get("elements", [])
        for el in elements:
            tags = el.get("tags", {})
            name = tags.get("name") or tags.get("name:ru")
            if not name:
                continue

            osm_id = f"{el.get('type')}_{el.get('id')}"
            lat = el.get("lat") or (el.get("center", {}).get("lat"))
            lon = el.get("lon") or (el.get("center", {}).get("lon"))

            street = tags.get("addr:street", "")
            housenumber = tags.get("addr:housenumber", "")
            if street and housenumber:
                address = f"г. Ростов-на-Дону, {street}, {housenumber}"
            elif street:
                address = f"г. Ростов-на-Дону, {street}"
            else:
                address = "г. Ростов-на-Дону (Центр)"

            category, budget, place_tags = classify_osm_element(tags)
            opening_hours = tags.get("opening_hours", "Ежедневно 10:00–23:00")

            existing = db.query(Place).filter(Place.osm_id == osm_id).first()
            if existing:
                existing.name = name
                existing.address = address
                existing.lat = lat
                existing.lon = lon
                existing.opening_hours = opening_hours
            else:
                # 15% заведений получают статус Партнера для демонстрации нативной монетизации
                is_partner = random.random() < 0.15
                rating = round(random.uniform(4.2, 5.0), 1)
                new_place = Place(
                    osm_id=osm_id,
                    name=name,
                    category=category,
                    budget_per_person=budget,
                    rating=rating,
                    tags_json=json.dumps(place_tags, ensure_ascii=False),
                    address=address,
                    lat=lat,
                    lon=lon,
                    opening_hours=opening_hours,
                    is_partner=is_partner
                )
                db.add(new_place)
                added_or_updated += 1

        db.commit()
    except Exception as e:
        print(f"Ошибка при синхронизации с Overpass API: {e}")

    # Если БД пустая (например, нет интернета при первом запуске), добавим стартовое ядро Ростова
    if db.query(Place).count() == 0:
        seed_fallback_rostov(db)

    return db.query(Place).count()


def seed_fallback_rostov(db: Session):
    """Резервное ядро культовых мест Ростова-на-Дону, чтобы база никогда не была пустой."""
    core_places = [
        ("Leo Wine & Kitchen", "Ресторан", 1800, 4.9, ["свидание", "вино", "ужин", "вкусно"], "ул. Максима Горького, 195", 47.2281, 39.7285, True),
        ("ОнегинДача", "Ресторан", 2500, 4.8, ["ужин", "свидание", "уютно", "вино"], "пр. Чехова, 45Б", 47.2265, 39.7210, False),
        ("Ош Пош", "Ресторан", 2200, 4.7, ["мясо", "компания", "вкусно", "ужин"], "ул. Красноармейская, 168/99", 47.2321, 39.7180, True),
        ("Голодранец", "Бар", 1600, 4.8, ["крафт", "коктейли", "вечер", "компания"], "пер. Семашко, 51", 47.2235, 39.7120, True),
        ("Shanghai 12", "Бар", 1500, 4.7, ["коктейли", "азия", "музыка", "тайный бар"], "пер. Соборный, 17", 47.2195, 39.7085, False),
        ("Свое Место", "Бар", 1700, 4.6, ["крафт", "шумно", "вечер", "друзья"], "ул. Пушкинская, 29", 47.2225, 39.7050, False),
        ("Setters", "Кафе/Кофейня", 650, 4.8, ["кофе", "завтрак", "работа", "десерты"], "ул. Пушкинская, 132", 47.2270, 39.7220, True),
        ("Питькофе", "Кафе/Кофейня", 700, 4.5, ["кофе", "завтрак", "быстро", "уютно"], "пр. Ворошиловский, 91", 47.2310, 39.7140, False),
        ("Гаврош", "Кафе/Кофейня", 850, 4.7, ["десерты", "круассаны", "уютно", "тихо"], "ул. Пушкинская, 36", 47.2230, 39.7060, False),
        ("Циферблат", "Необычные места", 700, 4.8, ["арт-пространство", "работа", "тихо", "друзья"], "пр. Соколова, 46", 47.2260, 39.7160, True),
        ("Парк Революции", "Развлечения", 500, 4.9, ["прогулка", "аттракционы", "свидание", "отдых"], "пл. Театральная, 3", 47.2285, 39.7430, False),
        ("Горизонт Cinema & Emotion", "Развлечения", 1100, 4.8, ["кино", "свидание", "отдых"], "пр. Михаила Нагибина, 32/2", 47.2590, 39.7190, False)
    ]
    for idx, (name, cat, budget, rating, tags, addr, lat, lon, partner) in enumerate(core_places):
        db.add(Place(
            osm_id=f"core_{idx}",
            name=name,
            category=cat,
            budget_per_person=budget,
            rating=rating,
            tags_json=json.dumps(tags, ensure_ascii=False),
            address=f"г. Ростов-на-Дону, {addr}",
            lat=lat,
            lon=lon,
            is_partner=partner
        ))
    db.commit()
