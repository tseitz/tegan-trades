from distill.schema import FLAT_THESIS_SCHEMA


def test_flat_schema_advertises_asset_heard_but_not_required():
    item = FLAT_THESIS_SCHEMA["properties"]["theses"]["items"]
    # The model must be told the field exists...
    assert item["properties"]["asset_heard"]["type"] == "string"
    # ...but it must NOT be required — the strict --json-schema subset would then
    # force a heard-form on every call, defeating "populate only when unsure".
    assert "asset_heard" not in item["required"]
