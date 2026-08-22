# Developed by ARGON telegram: @REACTIVEARGON
from datetime import datetime, time

from motor.motor_asyncio import AsyncIOMotorClient

from bot.config import DB_NAME, DB_URI
from bot.logger import LOGGER

log = LOGGER(__name__)

# --- Internal Helpers ---


def clean_value(value):
    if isinstance(value, time):
        return {"__time__": value.strftime("%H:%M")}
    if isinstance(value, datetime):
        return {"__datetime__": value.isoformat()}
    if isinstance(value, list):
        return [clean_value(v) for v in value]
    if isinstance(value, dict):
        return {k: clean_value(v) for k, v in value.items()}
    return value


def restore_value(value):
    if isinstance(value, dict) and "__time__" in value:
        return datetime.strptime(value["__time__"], "%H:%M").time()
    if isinstance(value, dict) and "__datetime__" in value:
        try:
            return datetime.fromisoformat(value["__datetime__"])
        except ValueError:
            return value
    if isinstance(value, list):
        return [restore_value(v) for v in value]
    if isinstance(value, dict):
        return {k: restore_value(v) for k, v in value.items()}
    return value


# --- Client ---

dbclient = AsyncIOMotorClient(DB_URI)
database = dbclient[DB_NAME]
user_data = database["users"]
config_data = database["config"]


async def add_user(user_id: int):
    try:
        await user_data.update_one(
            {"_id": user_id}, {"$set": {"_id": user_id}}, upsert=True
        )
    except Exception as e:
        log.error(f"Error adding user {user_id}: {e}")


async def del_user(user_id: int):
    try:
        await user_data.delete_one({"_id": user_id})
    except Exception as e:
        log.error(f"Error deleting user {user_id}: {e}")


async def present_user(user_id: int):
    try:
        found = await user_data.find_one({"_id": user_id})
        return bool(found)
    except Exception as e:
        log.error(f"Error finding user {user_id}: {e}")
        return False


async def full_userbase():
    try:
        cursor = user_data.find({}, {"_id": 1})
        return [doc["_id"] async for doc in cursor]
    except Exception as e:
        log.error(f"Error retrieving user base: {e}")
        return []


async def get_user_settings(user_id: int):
    """Retrieve user settings from the database."""
    try:
        user = await user_data.find_one({"_id": user_id})
        if user and "settings" in user:
            return user["settings"]
        return {}
    except Exception as e:
        log.error(f"Error getting settings for {user_id}: {e}")
        return {}


async def update_user_settings(user_id: int, settings: dict) -> bool:
    """Update user settings in the database. Returns success."""
    try:
        await user_data.update_one(
            {"_id": user_id}, {"$set": {"settings": settings}}, upsert=True
        )
        return True
    except Exception as e:
        log.error(f"Error updating settings for {user_id}: {e}")
        return False


# --- Config variables ---


async def set_variable(key: str, value):
    """Set a configuration variable in the database."""
    try:
        await config_data.update_one(
            {"_id": key},
            {"$set": {"value": clean_value(value)}},
            upsert=True,
        )
    except Exception as e:
        log.error(f"Error setting variable '{key}': {e}")


async def get_variable(key: str, default=None):
    """Retrieve a configuration variable, falling back to default."""
    try:
        entry = await config_data.find_one({"_id": key})
    except Exception as e:
        log.error(f"Error getting variable '{key}': {e}")
        return default

    if not entry:
        try:
            await config_data.insert_one({"_id": key, "value": clean_value(default)})
        except Exception as e:
            log.debug(f"Could not persist default for '{key}': {e}")
        return default

    value = entry.get("value", default)
    return default if value is None else restore_value(value)


async def get_all_variables():
    """Retrieve all configuration variable keys and values."""
    variables = []
    try:
        cursor = config_data.find({})
        async for entry in cursor:
            variables.append((entry["_id"], restore_value(entry.get("value"))))
    except Exception as e:
        log.error(f"Error listing variables: {e}")
    return variables


# --- Aggregate stats ---


async def inc_stats(fields: dict, user_id: int = None):
    """Atomically increment global (and optional per-user) counters."""
    try:
        await config_data.update_one(
            {"_id": "stats"}, {"$inc": clean_value(fields)}, upsert=True
        )
        if user_id is not None:
            await user_data.update_one(
                {"_id": user_id},
                {"$inc": {f"stats.{k}": v for k, v in fields.items()}},
                upsert=True,
            )
    except Exception as e:
        log.error(f"Error incrementing stats: {e}")


async def get_stats() -> dict:
    try:
        doc = await config_data.find_one({"_id": "stats"})
        return doc or {}
    except Exception as e:
        log.error(f"Error reading stats: {e}")
        return {}
