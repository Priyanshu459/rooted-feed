
from app import app, db, User
import uuid

with app.app_context():
    db.create_all()
    user_uuid = str(uuid.uuid4())
    if not User.query.filter_by(handle='testuser').first():
        user = User(
            uuid=user_uuid,
            email='test@example.com',
            handle='testuser',
            display_name='Test User'
        )
        db.session.add(user)
        db.session.commit()
        print(f"Created test user: testuser with UUID: {user_uuid}")
    else:
        user = User.query.filter_by(handle='testuser').first()
        print(f"Test user already exists: {user.handle} with UUID: {user.uuid}")
