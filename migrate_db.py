from sqlalchemy import text
from db import engine

def migrate():
    print("Connecting to database...")
    with engine.connect() as conn:
        print("Adding last_analysis column to farms table...")
        try:
            # Using JSONB for better performance in Postgres if supported, 
            # but JSON is safer as it matches the model's type.
            conn.execute(text("ALTER TABLE farms ADD COLUMN last_analysis JSON;"))
            conn.commit()
            print("Migration successful: Added last_analysis column.")
        except Exception as e:
            if "already exists" in str(e).lower():
                print("Column already exists. Skipping.")
            else:
                print(f"Migration failed: {e}")

if __name__ == "__main__":
    migrate()
