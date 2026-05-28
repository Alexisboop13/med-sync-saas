import asyncio
from app.db.session import AsyncSessionLocal
from sqlalchemy import text

async def main():
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(text("SELECT COUNT(*) FROM clinics"))
            print(f"✅ Conexión OK. {result.scalar()} clínicas.")
    except Exception as e:
        print(f"❌ Error: {e}")

asyncio.run(main())
