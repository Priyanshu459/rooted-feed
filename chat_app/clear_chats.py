import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# Standalone script that doesn't import app.py to avoid eventlet issues on Python 3.14
load_dotenv()

def get_db_url():
    db_url = os.getenv('DATABASE_URL')
    if db_url:
        db_url = db_url.strip()
        if db_url.startswith('postgres://'):
            db_url = db_url.replace('postgres://', 'postgresql://', 1)
    else:
        db_url = 'sqlite:///rooted.db'
    return db_url

def clear_chat_data():
    url = get_db_url()
    engine = create_engine(url)
    
    with engine.connect() as conn:
        print("🗑️ Deleting all direct messages...")
        conn.execute(text("DELETE FROM message"))
        
        print("🗑️ Deleting all group messages...")
        conn.execute(text("DELETE FROM group_message"))
        
        print("🗑️ Deleting all conversations...")
        conn.execute(text("DELETE FROM conversation"))
        
        conn.commit()
        print("✅ All chat data cleared successfully!")

if __name__ == "__main__":
    confirm = input("⚠️ Standalone Cleanup: Are you sure you want to delete ALL chat data? (y/n): ")
    if confirm.lower() == 'y':
        try:
            clear_chat_data()
        except Exception as e:
            print(f"❌ Error: {e}")
    else:
        print("❌ Operation cancelled.")
