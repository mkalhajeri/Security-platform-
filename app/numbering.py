"""Atomic generation of incident document numbers.

Format: "{SITE_CODE}-{YEAR}-{SEQ:04d}", e.g. "JAW-2026-0001". The sequence
resets every calendar year and is scoped per site, so two different sites
can both have a "0001" in the same year without colliding.

An incident filed under "Other" (a site not in the managed Sites
registry — see app/site_models.py) shares a single OTH pseudo-code rather
than inventing one from whatever free text was typed in: a code seeds
every future number in its scope, so it shouldn't depend on someone's
one-off spelling of a location.

Numbers are generated via an atomic `find_one_and_update($inc,
upsert=True)` against a dedicated `counters` collection. A naive
"find the max existing number and add one" would race under concurrent
inserts (two reports filed for the same site in the same instant could
compute the same next number); this can't, because MongoDB executes the
increment atomically server-side.
"""

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

OTHER_SITE_CODE = "OTH"


async def next_incident_number(db: AsyncIOMotorDatabase, site_code: str, year: int) -> str:
    key = f"{site_code}-{year}"
    result = await db["counters"].find_one_and_update(
        {"_id": key},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    seq = result["seq"]
    return f"{site_code}-{year}-{seq:04d}"
