from datetime import datetime
from sqlalchemy import BigInteger, String, Float, DateTime, select, delete
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

DATABASE_URL = "sqlite+aiosqlite:///./kingdom.db"

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)

class Base(AsyncAttrs, DeclarativeBase):
    pass

class TrackedItem(Base):
    __tablename__ = "tracked_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    article: Mapped[int] = mapped_column(BigInteger, index=True)
    platform: Mapped[str] = mapped_column(String(20), default="wb")  # "wb" или "ozon"
    title: Mapped[str] = mapped_column(String(255))
    current_price: Mapped[float] = mapped_column(Float)
    last_checked: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def add_or_update_item(user_id: int, article: int, title: str, price: float, platform: str = "wb"):
    async with async_session() as session:
        stmt = select(TrackedItem).where(
            TrackedItem.user_id == user_id, 
            TrackedItem.article == article,
            TrackedItem.platform == platform
        )
        result = await session.execute(stmt)
        item = result.scalar_one_or_none()

        if item:
            item.current_price = price
            item.title = title
            item.last_checked = datetime.utcnow()
        else:
            item = TrackedItem(
                user_id=user_id, 
                article=article, 
                title=title, 
                current_price=price, 
                platform=platform
            )
            session.add(item)

        await session.commit()

async def get_user_items(user_id: int):
    async with async_session() as session:
        stmt = select(TrackedItem).where(TrackedItem.user_id == user_id)
        result = await session.execute(stmt)
        return result.scalars().all()

async def get_all_items():
    async with async_session() as session:
        stmt = select(TrackedItem)
        result = await session.execute(stmt)
        return result.scalars().all()

async def remove_item(user_id: int, article: int, platform: str = "wb"):
    async with async_session() as session:
        stmt = delete(TrackedItem).where(
            TrackedItem.user_id == user_id, 
            TrackedItem.article == article,
            TrackedItem.platform == platform
        )
        await session.execute(stmt)
        await session.commit()