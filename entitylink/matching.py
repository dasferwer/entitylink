import re
import unicodedata

from rapidfuzz.fuzz import ratio, token_sort_ratio


def normalize(value):
    value = unicodedata.normalize("NFKC", value or "").lower().replace("ё", "е")
    return " ".join(re.findall(r"[\w]+", value))


def phone(value):
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return digits


def compare(left, right):
    reasons = []
    a, b = normalize(left.get("tax_id")), normalize(right.get("tax_id"))
    if a and b and a != b:
        return {"score": 0.0, "decision": "conflict", "reasons": ["Разные идентификаторы"]}
    if a and b:
        return {"score": 1.0, "decision": "review", "reasons": ["Совпадает идентификатор"]}
    name = token_sort_ratio(normalize(left["name"]), normalize(right["name"])) / 100
    address_a, address_b = normalize(left.get("address")), normalize(right.get("address"))
    address = ratio(address_a, address_b) / 100 if address_a and address_b else 0
    phone_a, phone_b = phone(left.get("phone")), phone(right.get("phone"))
    same_phone = bool(phone_a and phone_b and phone_a == phone_b)
    score = 0.55 * name + 0.25 * address + 0.20 * same_phone
    reasons.append(f"Сходство названий: {name:.0%}")
    reasons.append(f"Сходство адресов: {address:.0%}")
    if same_phone:
        reasons.append("Совпадает телефон")
    # Одного похожего названия недостаточно для предложения объединить записи.
    decision = "review" if score >= 0.78 and name >= 0.65 else "distinct"
    return {"score": round(score, 4), "decision": decision, "reasons": reasons}
