from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agentgate.core.config import settings

engine = create_async_engine(settings.database_url, echo=settings.debug)
async_session = async_sessionmaker(engine, expire_on_commit=False)
