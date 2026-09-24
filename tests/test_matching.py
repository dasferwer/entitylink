from entitylink.matching import compare, normalize, phone


def test_normalization():
    assert normalize("  Ёлка, ООО!") == "елка ооо"
    assert phone("8 (999) 123-45-67") == phone("+7 999 1234567")


def test_identifier_conflict_dominates_name():
    assert (
        compare(
            {"name": "Пример", "tax_id": "1234567890"}, {"name": "Пример", "tax_id": "9876543210"}
        )["decision"]
        == "conflict"
    )


def test_name_alone_is_insufficient():
    assert compare({"name": "Пример"}, {"name": "Пример"})["decision"] == "distinct"


def test_typos_and_supporting_fields():
    result = compare(
        {"name": "Северный ветер", "address": "Москва ул Полевая 5", "phone": "89991234567"},
        {"name": "Северный ветар", "address": "Москва Полевая 5", "phone": "+79991234567"},
    )
    assert result["decision"] == "review"
    assert len(result["reasons"]) == 3


def test_empty_phone_does_not_match():
    assert (
        compare({"name": "Пример", "phone": ""}, {"name": "Пример", "phone": ""})["score"] == 0.55
    )
