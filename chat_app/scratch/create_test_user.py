
import sqlite3
import uuid

conn = sqlite3.connect('rooted.db')
curr = conn.cursor()

# Create table if not exists (though db.create_all should do it)
# We can just wait for the app to start once, or create it manually.

user_uuid = str(uuid.uuid4())
curr.execute('''
INSERT INTO user (uuid, email, handle, display_name, bio, profile_photo_url, cover_photo_url, account_tier, is_private)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
''', (user_uuid, 'test@example.com', 'testuser', 'Test User', 'Bio', '', '', 'Free', 0))

conn.commit()
conn.close()

print(f"Created test user with UUID: {user_uuid}")
print(f"Login URL: http://localhost:3001/?token={user_uuid}")
