from tokenleak.db.base import Database
from tokenleak.db.sqlite import SQLiteDB
from tokenleak.db.postgres import PostgresDB
from tokenleak.config import Config


def create_db(config: Config) -> Database:
    if config.db_type == "postgres":
        return PostgresDB(config)
    return SQLiteDB(config)
