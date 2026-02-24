from __future__ import annotations

from typing import AsyncGenerator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


class DB:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.url = f"sqlite+aiosqlite:///{self.db_path}"
        self._engine: AsyncEngine | None = None
        self._maker = None

    def engine(self) -> AsyncEngine:
        if self._engine is None:
            self._engine = create_async_engine(self.url, future=True)
        return self._engine

    def sessionmaker(self):
        if self._maker is None:
            self._maker = async_sessionmaker(self.engine(), expire_on_commit=False)
        return self._maker

    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        maker = self.sessionmaker()
        async with maker() as sess:
            yield sess


def make_db(db_path: Path | str) -> DB:
    return DB(db_path)
