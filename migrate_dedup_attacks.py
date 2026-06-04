import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ["DATABASE_URL"]
engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    # 1. Delete duplicates — keep the row with the smallest id (earliest inserted)
    result = conn.execute(text("""
        DELETE FROM attacks
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM attacks
            GROUP BY player_id, defender, stars, destruction, is_attack
        )
    """))
    print(f"Deleted {result.rowcount} duplicate attack rows.")

    # 2. Add unique constraint
    conn.execute(text("""
        ALTER TABLE attacks
        ADD CONSTRAINT uq_attack
        UNIQUE (player_id, defender, stars, destruction, is_attack)
    """))
    print("Added unique constraint uq_attack.")

    conn.commit()
    print("Done.")
