import os
if os.getenv('SKIP_EVENTLET') != '1':
    try:
        import eventlet
        eventlet.monkey_patch()
    except Exception as e:
        print(f"Note: eventlet monkey_patch skipped: {e}")

import os
import uuid
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, url_for, redirect
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
import time
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import cloudinary
import cloudinary.uploader
from authlib.integrations.flask_client import OAuth
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from cryptography.fernet import Fernet
import base64

load_dotenv()

app = Flask(__name__)
# Proxy Fix for Render/Production
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)

app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'secret!')
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024
# Database Configuration
db_url = os.getenv('DATABASE_URL')
if db_url:
    db_url = db_url.strip()
    # Safety: Only use it if it looks like a real database URL
    if db_url.startswith('postgres://') or db_url.startswith('postgresql://'):
        if db_url.startswith('postgres://'):
            db_url = db_url.replace('postgres://', 'postgresql://', 1)
    elif db_url.startswith('sqlite://'):
        pass 
    else:
        # If it's just instruction text or garbage, fallback to local
        print(f"⚠️ Warning: Invalid DATABASE_URL detected ('{db_url[:20]}...'). Falling back to SQLite.")
        db_url = 'sqlite:///rooted.db'
else:
    db_url = 'sqlite:///rooted.db'

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Session/Cookie Security (Fixes MismatchingStateError)
# On Render, HTTPS is used, but locally HTTP might be used.
IS_PROD = os.getenv('RENDER') is not None or os.getenv('IS_PROD') is not None
app.config['SESSION_COOKIE_SECURE'] = IS_PROD
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'None' if IS_PROD else 'Lax'
app.config['PREFERRED_URL_SCHEME'] = 'https' if IS_PROD else 'http'

# Chat Encryption Setup
CHAT_KEY = os.getenv('CHAT_ENCRYPTION_KEY')
if not CHAT_KEY:
    # Generate a key if not present (Not recommended for prod, but ensures it works)
    CHAT_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode()
    print("⚠️ Warning: CHAT_ENCRYPTION_KEY not found. Using transient key.")
cipher_suite = Fernet(CHAT_KEY.encode())

def encrypt_text(text):
    if not text: return text
    return cipher_suite.encrypt(text.encode()).decode()

def decrypt_text(token):
    if not token: return token
    try:
        return cipher_suite.decrypt(token.encode()).decode()
    except Exception:
        return "[Encrypted Message]"

def find_user_by_handle(handle):
    """Look up a User by handle, tolerating presence or absence of the '@' prefix.
    The DB stores handles WITH '@' (e.g. '@aastha'), but callers may pass either format.
    """
    if not handle:
        return None
    user = User.query.filter_by(handle=handle).first()
    if user:
        return user
    # Try the other format
    if handle.startswith('@'):
        user = User.query.filter_by(handle=handle[1:]).first()
    else:
        user = User.query.filter_by(handle='@' + handle).first()
    return user

@app.before_request
def redirect_www():
    """Permanently redirect www.rooted-feed.online → rooted-feed.online"""
    host = request.host
    if host and host.startswith('www.'):
        non_www = host[4:]  # Strip leading 'www.'
        url = request.url.replace(f'https://{host}', f'https://{non_www}', 1)
        url = url.replace(f'http://{host}', f'https://{non_www}', 1)
        return redirect(url, code=301)

@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    if IS_PROD:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        # Basic CSP - restrict sources
        response.headers['Content-Security-Policy'] = "default-src 'self' https:; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdnjs.cloudflare.com https://www.googletagmanager.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; img-src 'self' data: https:; media-src 'self' https:; connect-src 'self' https: wss:;"
    return response

# Cloudinary Setup
cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key=os.getenv('CLOUDINARY_API_KEY'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET')
)

# Authlib OAuth Setup
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile'
    }
)

# Flask Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'index'

db = SQLAlchemy(app)
migrate = Migrate(app, db)

followers = db.Table('followers',
    db.Column('follower_id', db.Integer, db.ForeignKey('user.id')),
    db.Column('followed_id', db.Integer, db.ForeignKey('user.id'))
)

follow_requests = db.Table('follow_requests',
    db.Column('requester_id', db.Integer, db.ForeignKey('user.id')),
    db.Column('requested_id', db.Integer, db.ForeignKey('user.id'))
)

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    email = db.Column(db.String(120), unique=True, nullable=False)
    handle = db.Column(db.String(50), unique=True, nullable=False)
    display_name = db.Column(db.String(100), nullable=False)
    bio = db.Column(db.String(250), default='')
    profile_photo_url = db.Column(db.String(200), default='')
    cover_photo_url = db.Column(db.String(200), default='')
    account_tier = db.Column(db.String(20), default='Free')
    is_private = db.Column(db.Boolean, default=False)
    
    followed = db.relationship(
        'User', secondary=followers,
        primaryjoin=(followers.c.follower_id == id),
        secondaryjoin=(followers.c.followed_id == id),
        backref=db.backref('followers', lazy='dynamic'), lazy='dynamic')
        
    requests_sent = db.relationship(
        'User', secondary=follow_requests,
        primaryjoin=(follow_requests.c.requester_id == id),
        secondaryjoin=(follow_requests.c.requested_id == id),
        backref=db.backref('requests_received', lazy='dynamic'), lazy='dynamic')
        
    def is_following(self, user):
        return self.followed.filter_by(id=user.id).first() is not None

    def follow(self, user):
        if not self.is_following(user):
            self.followed.append(user)
            db.session.add(self) # Ensure self is tracked for association change
            return True
        return False

    def unfollow(self, user):
        if self.is_following(user):
            self.followed.remove(user)
            db.session.add(self)
            return True
        return False

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@login_manager.request_loader
def load_user_from_request(request):
    # First, try to login using the Authorization header
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        token = auth_header.replace('Bearer ', '', 1)
        # In this app, we use user.uuid as the auth token
        user = User.query.filter_by(uuid=token).first()
        if user:
            return user

    # Next, check for token in query params (useful for some socket setups)
    token = request.args.get('token')
    if token:
        user = User.query.filter_by(uuid=token).first()
        if user:
            return user

    # Finally, returning None lets Flask-Login fallback to session/cookie
    return None

class Conversation(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user1_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    user2_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    updated_at = db.Column(db.BigInteger)
    messages = db.relationship('Message', backref='conversation', lazy='dynamic')

class Message(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = db.Column(db.String(36), db.ForeignKey('conversation.id'))
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    text = db.Column(db.String(1000))
    timestamp = db.Column(db.BigInteger)
    read = db.Column(db.Boolean, default=False)

chat_group_members = db.Table('chat_group_members',
    db.Column('group_id', db.String(36), db.ForeignKey('chat_group.id')),
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'))
)

class ChatGroup(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(100), nullable=False)
    admin_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    updated_at = db.Column(db.BigInteger)
    
    admin = db.relationship('User', foreign_keys=[admin_id])
    members = db.relationship('User', secondary=chat_group_members, backref=db.backref('chat_groups', lazy='dynamic'))
    messages = db.relationship('GroupMessage', backref='group', lazy='dynamic', cascade='all, delete-orphan')

class GroupMessage(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    group_id = db.Column(db.String(36), db.ForeignKey('chat_group.id'))
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    text = db.Column(db.String(1000))
    timestamp = db.Column(db.BigInteger)
    
    sender = db.relationship('User', foreign_keys=[sender_id])

class Notification(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False) # Owner
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id')) # Triggers it
    type = db.Column(db.String(50), nullable=False) # follow, follow_request, like, retweet, message
    content = db.Column(db.String(250))
    is_read = db.Column(db.Boolean, default=False)
    timestamp = db.Column(db.BigInteger)
    
    user = db.relationship('User', foreign_keys=[user_id], backref=db.backref('notifications', lazy='dynamic', cascade='all, delete-orphan'))
    sender = db.relationship('User', foreign_keys=[sender_id])
    
    def to_dict(self):
        sender_handle = self.sender.handle if self.sender else None
        sender_name = self.sender.display_name if self.sender else None
        sender_photo = self.sender.profile_photo_url if self.sender else None
        return {
            'id': self.id,
            'type': self.type,
            'content': self.content,
            'is_read': self.is_read,
            'timestamp': self.timestamp,
            'sender_handle': sender_handle,
            'sender_name': sender_name,
            'sender_photo': sender_photo
        }

class Story(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    media_url = db.Column(db.String(200))
    media_type = db.Column(db.String(20))
    text = db.Column(db.String(500))
    timestamp = db.Column(db.BigInteger)
    
    user = db.relationship('User', foreign_keys=[user_id], backref=db.backref('stories', lazy='dynamic', cascade='all, delete-orphan'))

    def to_dict(self):
        return {
            'id': self.id,
            'user_handle': self.user.handle if self.user else None,
            'media_url': self.media_url,
            'media_type': self.media_type,
            'text': self.text,
            'timestamp': self.timestamp
        }

class Post(db.Model):
    id = db.Column(db.String(50), primary_key=True)
    sender = db.Column(db.String(100))
    handle = db.Column(db.String(50))
    text = db.Column(db.String(500))
    media_url = db.Column(db.String(200))
    media_type = db.Column(db.String(20))
    timestamp = db.Column(db.BigInteger)
    likes = db.Column(db.Integer, default=0)
    bookmarks = db.Column(db.Integer, default=0)
    reply_count = db.Column(db.Integer, default=0)
    node = db.Column(db.String(50), default='For You')
    parent_id = db.Column(db.String(50), db.ForeignKey('post.id'), nullable=True)
    is_retweet = db.Column(db.Boolean, default=False)
    original_post_id = db.Column(db.String(50), db.ForeignKey('post.id'), nullable=True)

class PostLike(db.Model):
    """Tracks which user liked which post — enforces one like per user per post."""
    __tablename__ = 'post_like'
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), primary_key=True)
    post_id = db.Column(db.String(50), db.ForeignKey('post.id'), primary_key=True)

# Database migrations are handled by Flask-Migrate via alembic


# Enable SocketIO
async_mode = 'eventlet' if os.getenv('SKIP_EVENTLET') != '1' else 'threading'
socketio = SocketIO(app, cors_allowed_origins="*", max_http_buffer_size=500*1024*1024, async_mode=async_mode)

@app.route('/')
def index():
    token = request.args.get('token')
    if token:
        user = User.query.filter_by(uuid=token).first()
        if user:
            login_user(user, remember=True)
            return redirect(url_for('index'))

    user_data = None
    if current_user.is_authenticated:
        user_data = {
            'id': current_user.id,
            'name': current_user.display_name,
            'handle': current_user.handle,
            'uuid': current_user.uuid,
            'photo': current_user.profile_photo_url,
            'cover': current_user.cover_photo_url,
            'bio': current_user.bio,
            'is_private': current_user.is_private
        }
    return render_template('index.html', current_user=user_data)

@app.route('/robots.txt')
def robots():
    content = "User-agent: *\nAllow: /\nSitemap: https://www.rooted-feed.online/sitemap.xml"
    return content, 200, {'Content-Type': 'text/plain'}

@app.route('/sitemap.xml')
def sitemap():
    # Base URL for the site
    base_url = "https://www.rooted-feed.online"
    
    # Query all public posts (not comments, maybe limit to top 500 for performance)
    posts = Post.query.filter(Post.parent_id == None).order_by(Post.timestamp.desc()).limit(500).all()
    
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    
    # Homepage
    xml += f'  <url>\n    <loc>{base_url}/</loc>\n    <changefreq>daily</changefreq>\n    <priority>1.0</priority>\n  </url>\n'
    
    # Post pages
    for post in posts:
        xml += f'  <url>\n    <loc>{base_url}/post/{post.id}</loc>\n    <changefreq>weekly</changefreq>\n    <priority>0.7</priority>\n  </url>\n'
        
    xml += '</urlset>'
    return xml, 200, {'Content-Type': 'application/xml'}

@app.route('/post/<post_id>')
def view_post(post_id):
    """Individual shareable post page with rich Open Graph meta tags for SEO."""
    post = Post.query.get(post_id)
    if not post:
        return redirect(url_for('index'))
    author = User.query.filter_by(handle=post.handle).first()
    og_title = f"{post.sender} on Rooted"
    og_description = post.text[:200] if post.text else "Check out this post on Rooted Feed."
    og_image = post.media_url if post.media_url else (author.profile_photo_url if author else '')
    og_url = f"https://rooted-feed.online/post/{post_id}"
    return render_template('post.html',
        post=post,
        author=author,
        og_title=og_title,
        og_description=og_description,
        og_image=og_image,
        og_url=og_url
    )

@app.route('/login/google')
def login_google():
    client_id = os.getenv('GOOGLE_CLIENT_ID')
    if not client_id:
        return "Error: GOOGLE_CLIENT_ID not set in environment variables.", 500
        
    # Generate redirect URI
    redirect_uri = url_for('auth_google', _external=True)
    
    # Force HTTPS in production or if current request is secure
    if (IS_PROD or request.is_secure) and redirect_uri.startswith('http://'):
        redirect_uri = redirect_uri.replace('http://', 'https://', 1)
    
    # Ensure no trailing slashes or spaces causing mismatch
    redirect_uri = redirect_uri.strip()
    
    return google.authorize_redirect(redirect_uri)

@app.route('/login/google/authorized')
def auth_google():
    token = google.authorize_access_token()
    # With OpenID Connect, userinfo is parsed directly into the token
    user_info = token.get('userinfo')
    if not user_info:
        user_info = google.get('https://openidconnect.googleapis.com/v1/userinfo').json()
    
    email = user_info['email']
    display_name = user_info.get('name', email.split('@')[0])
    
    user = User.query.filter_by(email=email).first()
    if not user:
        base_handle = "@" + display_name.lower().replace(" ", "")
        handle = base_handle
        counter = 1
        while User.query.filter_by(handle=handle).first():
            handle = f"{base_handle}{counter}"
            counter += 1
            
        user = User(
            email=email,
            display_name=display_name,
            handle=handle,
            profile_photo_url=user_info.get('picture', '')
        )
        db.session.add(user)
        db.session.commit()
        
    login_user(user)
    return redirect(url_for('index'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    upload_type = request.form.get('type', 'post')
    url = request.form.get('media_url')
    media_type = request.form.get('media_type')
    
    if 'media' in request.files and request.files['media'].filename != '':
        file = request.files['media']
        try:
            if file.mimetype.startswith('video/'):
                upload_result = cloudinary.uploader.upload(
                    file, 
                    resource_type="video",
                    transformation=[{"quality": "auto"}]
                )
                media_type = 'video'
            else:
                upload_result = cloudinary.uploader.upload(
                    file,
                    transformation=[{"quality": "auto", "fetch_format": "auto"}]
                )
                media_type = 'image'
            url = upload_result.get('secure_url')
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    elif not url and upload_type not in ('profile', 'cover', 'story'):
        return jsonify({'error': 'No media part'}), 400
        
    try:
        if upload_type == 'profile':
            new_name = request.form.get('name')
            new_bio = request.form.get('bio')
            is_private = request.form.get('is_private') == 'true'
            
            user = User.query.get(current_user.id)
            if url:
                user.profile_photo_url = url
            if new_name:
                user.display_name = new_name
            if new_bio is not None:
                user.bio = new_bio
            user.is_private = is_private
                
            db.session.commit()
            return jsonify({'success': True, 'url': url})
            
        elif upload_type == 'cover':
            user = User.query.get(current_user.id)
            if url:
                user.cover_photo_url = url
                db.session.commit()
            return jsonify({'success': True, 'url': url})
            
        elif upload_type == 'story':
            text = request.form.get('text', '')
            if not text and not url:
                return jsonify({'error': 'Story must have text or media'}), 400
                
            story = Story(
                user_id=current_user.id,
                media_url=url,
                media_type=media_type,
                text=text,
                timestamp=int(time.time() * 1000)
            )
            db.session.add(story)
            db.session.commit()
            return jsonify({'success': True, 'url': url, 'story_id': story.id})
            
        else:
            return jsonify({'url': url, 'type': media_type})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/user/<handle>')
def get_user_profile(handle):
    user = User.query.filter_by(handle=handle).first()
    if not user:
        return jsonify({'error': 'User not found'}), 404
        
    is_following = False
    is_mutual = False
    is_requested = False
    if current_user.is_authenticated:
        is_following = current_user.is_following(user)
        is_mutual = is_following and user.is_following(current_user)
        is_requested = user in current_user.requests_sent
        
    return jsonify({
        'handle': user.handle,
        'name': user.display_name,
        'bio': user.bio,
        'photo': user.profile_photo_url,
        'cover': user.cover_photo_url,
        'followers_count': user.followers.count(),
        'following_count': user.followed.count(),
        'is_following': is_following,
        'is_mutual': is_mutual,
        'is_requested': is_requested,
        'is_self': current_user.is_authenticated and current_user.id == user.id
    })

@app.route('/api/search/users')
def search_users():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])
        
    users = User.query.filter(
        (User.display_name.ilike(f'%{q}%')) | (User.handle.ilike(f'%{q}%'))
    ).limit(20).all()
    
    results = []
    for u in users:
        results.append({
            'handle': u.handle,
            'name': u.display_name,
            'photo': u.profile_photo_url,
            'bio': u.bio
        })
    return jsonify(results)


def post_to_dict(p, viewer_id=None, preloaded_users=None, preloaded_posts=None, preloaded_likes=None):
    if preloaded_users is not None:
        user = preloaded_users.get(p.handle)
    else:
        user = User.query.filter_by(handle=p.handle).first()
        
    sender_name = user.display_name if user else p.sender
    sender_photo = user.profile_photo_url if user else None
    
    reply_to_handle = None
    if p.parent_id:
        if preloaded_posts is not None:
            parent_post = preloaded_posts.get(p.parent_id)
        else:
            parent_post = Post.query.get(p.parent_id)
        if parent_post:
            reply_to_handle = parent_post.handle
            
    retweeted_from = None
    if p.is_retweet and p.original_post_id:
        if preloaded_posts is not None:
            orig = preloaded_posts.get(p.original_post_id)
        else:
            orig = Post.query.get(p.original_post_id)
        if orig:
            retweeted_from = orig.handle

    # Check if the current viewer has already liked this post
    user_liked = False
    if viewer_id:
        if preloaded_likes is not None:
            user_liked = p.id in preloaded_likes
        else:
            user_liked = PostLike.query.filter_by(user_id=viewer_id, post_id=p.id).first() is not None
            
    return {
        'id': p.id,
        'sender': sender_name,
        'senderPhoto': sender_photo,
        'handle': p.handle,
        'text': p.text,
        'mediaUrl': p.media_url,
        'mediaType': p.media_type,
        'timestamp': p.timestamp,
        'likes': p.likes,
        'bookmarks': p.bookmarks,
        'replyCount': getattr(p, 'reply_count', 0),
        'node': p.node,
        'parentId': p.parent_id,
        'replyToHandle': reply_to_handle,
        'isRetweet': p.is_retweet,
        'originalPostId': p.original_post_id,
        'retweetedFrom': retweeted_from,
        'userLiked': user_liked
    }

@app.route('/api/post/<int:post_id>', methods=['PATCH', 'DELETE'])
@login_required
def modify_post(post_id):
    post = Post.query.get(post_id)
    if not post:
        return jsonify({'error': 'Post not found'}), 404
        
    if post.handle != current_user.handle:
        return jsonify({'error': 'Unauthorized'}), 403
        
    if request.method == 'DELETE':
        db.session.delete(post)
        db.session.commit()
        socketio.emit('delete_post', {'id': post_id})
        return jsonify({'success': True})
        
    if request.method == 'PATCH':
        data = request.json
        if not data or 'text' not in data:
            return jsonify({'error': 'No text provided'}), 400
            
        post.text = data['text']
        db.session.commit()
        socketio.emit('edit_post', {'id': post_id, 'text': post.text})
        return jsonify({'success': True})

@app.route('/api/posts/following')
@login_required
def get_following_posts():
    followed_users = current_user.followed.all()
    followed_handles = [u.handle for u in followed_users]
    # Include own posts
    followed_handles.append(current_user.handle)

    # Cursor-based pagination: `before` is a timestamp (ms)
    before = request.args.get('before', type=int)
    limit = request.args.get('limit', default=30, type=int)

    query = Post.query.filter(Post.handle.in_(followed_handles))
    if before:
        query = query.filter(Post.timestamp < before)
    posts = query.order_by(Post.timestamp.desc()).limit(limit).all()

    viewer_id = current_user.id

    # Preload users
    handles = list(set([p.handle for p in posts]))
    preloaded_users = {u.handle: u for u in User.query.filter(User.handle.in_(handles)).all()} if handles else {}

    # Preload related posts
    related_ids = list(set(
        [p.parent_id for p in posts if p.parent_id] +
        [p.original_post_id for p in posts if p.is_retweet and p.original_post_id]
    ))
    preloaded_posts = {p.id: p for p in Post.query.filter(Post.id.in_(related_ids)).all()} if related_ids else {}

    # Preload likes
    preloaded_likes = set()
    if posts:
        likes = PostLike.query.filter_by(user_id=viewer_id).filter(
            PostLike.post_id.in_([p.id for p in posts])
        ).all()
        preloaded_likes = {lk.post_id for lk in likes}

    res = [post_to_dict(p, viewer_id,
                        preloaded_users=preloaded_users,
                        preloaded_posts=preloaded_posts,
                        preloaded_likes=preloaded_likes) for p in posts]
    # Include a `has_more` flag so frontend knows whether to show Load More
    return jsonify({'posts': res, 'has_more': len(posts) == limit})

@app.route('/api/follow/<handle>', methods=['POST'])
@login_required
def follow_user(handle):
    user = User.query.filter_by(handle=handle).first()
    if not user or user == current_user:
        return jsonify({'error': 'Invalid action'}), 400
        
    ts = int(time.time() * 1000)
    
    if user.is_private:
        # Check if request already sent
        existing = Notification.query.filter_by(user_id=user.id, sender_id=current_user.id, type='follow_request').first()
        if not existing:
            current_user.requests_sent.append(user)
            n = Notification(user_id=user.id, sender_id=current_user.id, type='follow_request', content=f"{current_user.display_name} requested to follow you.", timestamp=ts)
            db.session.add(n)
            socketio.emit('receive_notification', n.to_dict(), room=f"user_{user.id}")
            db.session.commit()
        return jsonify({'success': True, 'status': 'requested'})
    else:
        current_user.follow(user)
        n = Notification(user_id=user.id, sender_id=current_user.id, type='follow', content=f"{current_user.display_name} started following you.", timestamp=ts)
        db.session.add(n)
        socketio.emit('receive_notification', n.to_dict(), room=f"user_{user.id}")
        db.session.commit()
        return jsonify({'success': True, 'status': 'following'})

@app.route('/api/following')
@login_required
def get_following():
    following = [{'handle': u.handle, 'name': u.display_name} for u in current_user.followed.all()]
    return jsonify(following)

@app.route('/api/posts/user/<handle>')
def get_user_posts(handle):
    """Return all posts for a given handle, newest first."""
    clean = handle.lstrip('@')
    handles = [clean, '@' + clean]

    posts = Post.query.filter(
        Post.handle.in_(handles)
    ).order_by(Post.timestamp.desc()).limit(200).all()

    viewer_id = current_user.id if current_user.is_authenticated else None

    user_obj = User.query.filter(User.handle.in_(handles)).first()
    preloaded_users = {h: user_obj for h in handles} if user_obj else {}

    related_ids = list(set(
        [p.parent_id for p in posts if p.parent_id] +
        [p.original_post_id for p in posts if p.is_retweet and p.original_post_id]
    ))
    preloaded_posts = {p.id: p for p in Post.query.filter(Post.id.in_(related_ids)).all()} if related_ids else {}

    preloaded_likes = set()
    if viewer_id and posts:
        likes = PostLike.query.filter_by(user_id=viewer_id).filter(
            PostLike.post_id.in_([p.id for p in posts])
        ).all()
        preloaded_likes = {lk.post_id for lk in likes}

    result = [
        post_to_dict(p, viewer_id,
                     preloaded_users=preloaded_users,
                     preloaded_posts=preloaded_posts,
                     preloaded_likes=preloaded_likes)
        for p in posts
    ]
    return jsonify(result)

@app.route('/api/unfollow/<handle>', methods=['POST'])
@login_required
def unfollow_user(handle):
    user = User.query.filter_by(handle=handle).first()
    if not user:
        return jsonify({'error': 'User not found'}), 404
        
    # Remove from requests if pending
    if user in current_user.requests_sent:
        current_user.requests_sent.remove(user)
        n = Notification.query.filter_by(user_id=user.id, sender_id=current_user.id, type='follow_request').first()
        if n: db.session.delete(n)
        
    current_user.unfollow(user)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/follow/accept/<handle>', methods=['POST'])
@login_required
def accept_follow(handle):
    sender = User.query.filter_by(handle=handle).first()
    if not sender or current_user not in sender.requests_sent:
        return jsonify({'error': 'Invalid request'}), 400
        
    sender.requests_sent.remove(current_user)
    sender.follow(current_user)
    
    # Mark notification as read/handled
    n = Notification.query.filter_by(user_id=current_user.id, sender_id=sender.id, type='follow_request').first()
    if n:
        n.type = 'follow'
        n.content = f"{sender.display_name} started following you."
        n.is_read = True
    
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/follow/decline/<handle>', methods=['POST'])
@login_required
def decline_follow(handle):
    sender = User.query.filter_by(handle=handle).first()
    if sender and current_user in sender.requests_sent:
        sender.requests_sent.remove(current_user)
        n = Notification.query.filter_by(user_id=current_user.id, sender_id=sender.id, type='follow_request').first()
        if n: db.session.delete(n)
        db.session.commit()
    return jsonify({'success': True})

@app.route('/api/notifications')
@login_required
def get_notifications():
    notifs = current_user.notifications.order_by(Notification.timestamp.desc()).limit(50).all()
    return jsonify([n.to_dict() for n in notifs])

@app.route('/api/notifications/read', methods=['POST'])
@login_required
def mark_notifications_read():
    unread = current_user.notifications.filter_by(is_read=False).all()
    for n in unread:
        n.is_read = True
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/conversations')
@login_required
def get_conversations():
    convs = Conversation.query.filter(
        (Conversation.user1_id == current_user.id) | (Conversation.user2_id == current_user.id)
    ).order_by(Conversation.updated_at.desc()).all()
    res = []
    for c in convs:
        other_user = User.query.get(c.user2_id if c.user1_id == current_user.id else c.user1_id)
        last_msg = c.messages.order_by(Message.timestamp.desc()).first()
        unread_count = c.messages.filter_by(read=False).filter(Message.sender_id != current_user.id).count()
        if last_msg:
            res.append({
                'id': c.id,
                'is_group': False,
                'other_user': {'handle': other_user.handle, 'name': other_user.display_name, 'photo': other_user.profile_photo_url},
                'last_message': decrypt_text(last_msg.text),
                'timestamp': last_msg.timestamp,
                'unread_count': unread_count
            })
            
    # Also fetch groups
    groups = current_user.chat_groups.all()
    for g in groups:
        last_msg = g.messages.order_by(GroupMessage.timestamp.desc()).first()
        res.append({
            'id': g.id,
            'is_group': True,
            'other_user': {'handle': 'group_' + g.id, 'name': g.name, 'photo': None},
            'group_id': g.id,
            'last_message': decrypt_text(last_msg.text) if last_msg else '',
            'timestamp': last_msg.timestamp if last_msg else g.updated_at,
            'unread_count': 0
        })
        
    res.sort(key=lambda x: x['timestamp'] or 0, reverse=True)
    return jsonify(res)

@app.route('/api/messages/<handle>')
@login_required
def get_messages(handle):
    other_user = find_user_by_handle(handle)
    if not other_user: return jsonify({'error': 'Not found'}), 404
    conv = Conversation.query.filter(
        ((Conversation.user1_id == current_user.id) & (Conversation.user2_id == other_user.id)) |
        ((Conversation.user1_id == other_user.id) & (Conversation.user2_id == current_user.id))
    ).first()
    if not conv: return jsonify([])
    
    # Cursor-based pagination
    before = request.args.get('before', type=int)
    limit = request.args.get('limit', default=50, type=int)
    
    query = conv.messages
    if before:
        query = query.filter(Message.timestamp < before)
    
    messages = query.order_by(Message.timestamp.desc()).limit(limit).all()
    # Reverse to return in chronological order
    messages.reverse()
    
    return jsonify([{
        'id': m.id, 'sender_id': m.sender_id, 'text': decrypt_text(m.text), 'timestamp': m.timestamp, 'is_me': m.sender_id == current_user.id, 'read': m.read
    } for m in messages])

@app.route('/api/group_messages/<group_id>')
@login_required
def get_group_messages(group_id):
    group = ChatGroup.query.get(group_id)
    if not group or current_user not in group.members:
        return jsonify([])
        
    # Cursor-based pagination
    before = request.args.get('before', type=int)
    limit = request.args.get('limit', default=50, type=int)
    
    query = group.messages
    if before:
        query = query.filter(GroupMessage.timestamp < before)
        
    messages = query.order_by(GroupMessage.timestamp.desc()).limit(limit).all()
    messages.reverse()
    
    return jsonify([{
        'id': m.id, 'sender_id': m.sender_id, 'sender_name': m.sender.display_name if m.sender else 'Unknown', 'text': decrypt_text(m.text), 'timestamp': m.timestamp, 'is_me': m.sender_id == current_user.id
    } for m in messages])

@app.route('/api/groups', methods=['POST'])
@login_required
def create_group():
    data = request.json
    name = data.get('name')
    member_handles = data.get('member_handles', [])
    
    if not name:
        return jsonify({'error': 'Name is required'}), 400
        
    group = ChatGroup(name=name, admin_id=current_user.id, updated_at=int(time.time() * 1000))
    group.members.append(current_user)
    
    for handle in member_handles:
        user = User.query.filter_by(handle=handle).first()
        if user and user != current_user:
            if current_user.is_following(user):
                group.members.append(user)
                
    db.session.add(group)
    db.session.commit()
    
    for member in group.members:
        socketio.emit('group_added', {}, room=f"user_{member.id}")
        
    return jsonify({'success': True, 'group_id': group.id})

@app.route('/api/groups/<group_id>', methods=['PATCH'])
@login_required
def edit_group(group_id):
    group = ChatGroup.query.get(group_id)
    if not group or group.admin_id != current_user.id:
        return jsonify({'error': 'Unauthorized or not found'}), 403
        
    data = request.json
    if 'name' in data:
        group.name = data['name']
        
    if 'member_handles' in data:
        new_members = [current_user]
        for handle in data['member_handles']:
            user = User.query.filter_by(handle=handle).first()
            if user and user != current_user and current_user.is_following(user):
                new_members.append(user)
        group.members = new_members
        
    db.session.commit()
    for member in group.members:
        socketio.emit('group_added', {}, room=f"user_{member.id}")
        
    return jsonify({'success': True})
    
@app.route('/api/groups/<group_id>', methods=['DELETE'])
@login_required
def delete_group_api(group_id):
    group = ChatGroup.query.get(group_id)
    if not group or group.admin_id != current_user.id:
        return jsonify({'error': 'Unauthorized or not found'}), 403
        
    db.session.delete(group)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/groups/<group_id>/members')
@login_required
def get_group_members(group_id):
    group = ChatGroup.query.get(group_id)
    if not group or current_user not in group.members:
        return jsonify({'error': 'Unauthorized'}), 403
    members = [{'handle': m.handle, 'name': m.display_name, 'photo': m.profile_photo_url} for m in group.members if m != current_user]
    # Also fetch people you follow so you can add them
    following = [{'handle': u.handle, 'name': u.display_name} for u in current_user.followed.all()]
    return jsonify({
        'admin_id': group.admin_id,
        'members': members,
        'following': following
    })

from flask_socketio import join_room

@socketio.on('connect')
def handle_connect(auth):
    # Handle token-based auth for Native Android Socket.IO
    token = None
    if auth and isinstance(auth, dict):
        token = auth.get('token')
    
    if token:
        user = User.query.filter_by(uuid=token).first()
        if user:
            login_user(user)
            print(f"✅ Native User Connected: {user.handle}")
            join_room(f"user_{user.id}")
            return True
            
    if current_user.is_authenticated:
        print(f"✅ Web User Connected: {current_user.handle}")
        join_room(f"user_{current_user.id}")
        return True
    
    # Allow anonymous for now, or return False to reject
    return True

@socketio.on('join_chat')
def join_chat(data):
    if current_user.is_authenticated:
        join_room(f"user_{current_user.id}")

@socketio.on('send_message')
def send_dm(data):
    if not current_user.is_authenticated: return
    target_handle, text = data.get('target_handle'), data.get('text')
    if not target_handle or not text: return
    target_user = find_user_by_handle(target_handle)
    if not target_user: return
    
    # Only mutual followers can DM
    if not current_user.is_following(target_user) or not target_user.is_following(current_user):
        emit('message_error', {'error': 'You can only message mutual followers'})
        return
        
    conv = Conversation.query.filter(
        ((Conversation.user1_id == current_user.id) & (Conversation.user2_id == target_user.id)) |
        ((Conversation.user1_id == target_user.id) & (Conversation.user2_id == current_user.id))
    ).first()
    ts = int(time.time() * 1000)
    if not conv:
        conv = Conversation(user1_id=current_user.id, user2_id=target_user.id, updated_at=ts)
        db.session.add(conv)
        db.session.commit()
    msg = Message(conversation_id=conv.id, sender_id=current_user.id, text=encrypt_text(text), timestamp=ts)
    conv.updated_at = ts
    db.session.add(msg)
    
    # Notification for Message
    existing_notif = Notification.query.filter_by(user_id=target_user.id, sender_id=current_user.id, type='message', is_read=False).first()
    if not existing_notif:
        n = Notification(user_id=target_user.id, sender_id=current_user.id, type='message', content=f"{current_user.display_name} sent you a message.", timestamp=ts)
        db.session.add(n)
        socketio.emit('receive_notification', n.to_dict(), room=f"user_{target_user.id}")
        
    db.session.commit()
    
    payload = {
        'id': msg.id, 'text': text, 'timestamp': ts, 'conv_id': conv.id,
        'sender_id': current_user.id,
        'sender_handle': current_user.handle, 'sender_name': current_user.display_name, 'sender_photo': current_user.profile_photo_url,
        'target_handle': target_handle
    }
    emit('receive_message', payload, room=f"user_{current_user.id}")
    emit('receive_message', payload, room=f"user_{target_user.id}")

@socketio.on('send_group_message')
def send_group_message(data):
    if not current_user.is_authenticated: return
    group_id = data.get('group_id')
    text = data.get('text')
    if not group_id or not text: return
    
    group = ChatGroup.query.get(group_id)
    if not group or current_user not in group.members: return
    
    ts = int(time.time() * 1000)
    msg = GroupMessage(group_id=group.id, sender_id=current_user.id, text=encrypt_text(text), timestamp=ts)
    group.updated_at = ts
    db.session.add(msg)
    db.session.commit()
    
    payload = {
        'id': msg.id, 'text': text, 'timestamp': ts, 'group_id': group.id,
        'sender_id': current_user.id,
        'sender_handle': current_user.handle, 'sender_name': current_user.display_name, 'sender_photo': current_user.profile_photo_url
    }
    
    for member in group.members:
        emit('receive_group_message', payload, room=f"user_{member.id}")

@socketio.on('typing')
def handle_typing(data):
    if not current_user.is_authenticated:
        return
    target_handle = data.get('target_handle')
    target_user = User.query.filter_by(handle=target_handle).first()
    if target_user:
        socketio.emit('user_typing', {
            'sender_handle': current_user.handle,
            'sender_name': current_user.display_name,
            'is_typing': data.get('is_typing', True),
            'is_group': False
        }, room=f"user_{target_user.id}")
    else:
        # Check if it's a group
        group = ChatGroup.query.get(target_handle)
        if group and current_user in group.members:
            for member in group.members:
                if member.id != current_user.id:
                    socketio.emit('user_typing', {
                        'sender_handle': current_user.handle,
                        'sender_name': current_user.display_name,
                        'is_typing': data.get('is_typing', True),
                        'is_group': True,
                        'group_id': group.id
                    }, room=f"user_{member.id}")

@socketio.on('mark_read')
def handle_mark_read(data):
    if not current_user.is_authenticated: return
    target_handle = data.get('target_handle')
    if not target_handle: return
    
    target_user = find_user_by_handle(target_handle)
    if not target_user: return
    
    conv = Conversation.query.filter(
        ((Conversation.user1_id == current_user.id) & (Conversation.user2_id == target_user.id)) |
        ((Conversation.user1_id == target_user.id) & (Conversation.user2_id == current_user.id))
    ).first()
    
    if conv:
        # Mark all messages from the other user as read
        unread = conv.messages.filter_by(read=False, sender_id=target_user.id).all()
        if unread:
            for m in unread:
                m.read = True
            db.session.commit()
            # Notify the sender that their messages were read
            socketio.emit('messages_read', {
                'reader_handle': current_user.handle,
                'conv_id': conv.id
            }, room=f"user_{target_user.id}")


@socketio.on('join')
def handle_join(user_data):
    if current_user.is_authenticated:
        join_room(f"user_{current_user.id}")
    
    # Fetch top 200 recent posts
    recent_posts = Post.query.order_by(Post.timestamp.desc()).limit(200).all()
    
    # Pre-load all users to prevent N+1 queries
    handles = list(set([p.handle for p in recent_posts]))
    preloaded_users = {u.handle: u for u in User.query.filter(User.handle.in_(handles)).all()}
    
    # Filter private posts
    visible_posts = []
    followed_handles = []
    viewer_id = None
    if current_user.is_authenticated:
        followed_handles = [u.handle for u in current_user.followed]
        viewer_id = current_user.id
        
    for p in recent_posts:
        user = preloaded_users.get(p.handle)
        if user and user.is_private:
            if not current_user.is_authenticated:
                continue
            if p.handle != current_user.handle and p.handle not in followed_handles:
                continue
        visible_posts.append(p)
    
    # Sort strictly by timestamp (Newest First)
    visible_posts.sort(key=lambda p: p.timestamp, reverse=True)
    
    batch_posts = visible_posts[:100]
    
    # Pre-load related posts (parents and retweets) to prevent N+1 queries
    parent_ids = [p.parent_id for p in batch_posts if p.parent_id]
    retweet_ids = [p.original_post_id for p in batch_posts if p.is_retweet and p.original_post_id]
    all_related_ids = list(set(parent_ids + retweet_ids))
    preloaded_posts = {p.id: p for p in Post.query.filter(Post.id.in_(all_related_ids)).all()} if all_related_ids else {}
    
    # Pre-load user likes to prevent N+1 queries
    preloaded_likes = set()
    if viewer_id and batch_posts:
        likes = PostLike.query.filter_by(user_id=viewer_id).filter(PostLike.post_id.in_([p.id for p in batch_posts])).all()
        preloaded_likes = {l.post_id for l in likes}
    
    # Send top 100 as a batch
    batch = [post_to_dict(p, viewer_id, preloaded_users=preloaded_users, preloaded_posts=preloaded_posts, preloaded_likes=preloaded_likes) for p in batch_posts]
    emit('initial_posts', batch)

@socketio.on('create_post')
def handle_create_post(data):
    post_id = str(int(time.time() * 1000))
    handle = data.get('handle', '@user')
    sender = data.get('sender', 'Anonymous')
    
    parent_id = data.get('parentId')
    if parent_id:
        parent_post = Post.query.get(parent_id)
        if parent_post:
            parent_post.reply_count += 1
            
            # Notification for Reply
            target_user = User.query.filter_by(handle=parent_post.handle).first()
            if target_user and target_user.handle != handle:
                n = Notification(user_id=target_user.id, type='reply', content=f"{sender} replied to your post.", timestamp=int(time.time() * 1000))
                
                # Check if sender is logged in to attach sender_id
                if current_user.is_authenticated:
                    n.sender_id = current_user.id
                    
                db.session.add(n)
                socketio.emit('receive_notification', n.to_dict(), room=f"user_{target_user.id}")
                
            db.session.commit()
            
    post = Post(
        id=post_id,
        sender=sender,
        handle=handle,
        text=data.get('text', ''),
        media_url=data.get('mediaUrl'),
        media_type=data.get('mediaType'),
        timestamp=int(time.time() * 1000),
        likes=0,
        bookmarks=0,
        reply_count=0,
        node=data.get('node', 'For You'),
        parent_id=parent_id,
        is_retweet=data.get('isRetweet', False),
        original_post_id=data.get('originalPostId')
    )
    db.session.add(post)
    db.session.commit()
    
    # Notification for Retweet
    if post.is_retweet and post.original_post_id:
        orig = Post.query.get(post.original_post_id)
        if orig:
            target_user = User.query.filter_by(handle=orig.handle).first()
            if target_user and target_user.handle != handle:
                n = Notification(user_id=target_user.id, type='retweet', content=f"{sender} retweeted your post.", timestamp=int(time.time() * 1000))
                if current_user.is_authenticated:
                    n.sender_id = current_user.id
                db.session.add(n)
                socketio.emit('receive_notification', n.to_dict(), room=f"user_{target_user.id}")
                db.session.commit()
            
    # Broadcast logic
    user = User.query.filter_by(handle=handle).first()
    if user and user.is_private:
        # Only emit to the sender and their followers
        payload = post_to_dict(post)
        emit('receive_post', payload, room=f"user_{user.id}")
        for follower in user.followers:
            socketio.emit('receive_post', payload, room=f"user_{follower.id}")
    else:
        # Public post
        emit('receive_post', post_to_dict(post), broadcast=True)

@socketio.on('bookmark_post')
def handle_bookmark_post(post_id):
    post = Post.query.get(post_id)
    if post:
        post.bookmarks += 1
        db.session.commit()

@socketio.on('like_post')
def handle_like_post(post_id):
    if not current_user.is_authenticated:
        return
    post = Post.query.get(post_id)
    if not post:
        return

    # Check if user already liked this post
    existing_like = PostLike.query.filter_by(user_id=current_user.id, post_id=post_id).first()

    if existing_like:
        # Toggle off — unlike
        db.session.delete(existing_like)
        post.likes = max(0, post.likes - 1)
        db.session.commit()
        emit('update_likes', {'id': post_id, 'likes': post.likes, 'userLiked': False}, broadcast=True)
    else:
        # New like
        new_like = PostLike(user_id=current_user.id, post_id=post_id)
        db.session.add(new_like)
        post.likes += 1

        target_user = User.query.filter_by(handle=post.handle).first()
        if target_user and target_user.id != current_user.id:
            n = Notification(user_id=target_user.id, sender_id=current_user.id, type='like', content=f"{current_user.display_name} liked your post.", timestamp=int(time.time() * 1000))
            db.session.add(n)
            socketio.emit('receive_notification', n.to_dict(), room=f"user_{target_user.id}")

        db.session.commit()
        emit('update_likes', {'id': post_id, 'likes': post.likes, 'userLiked': True}, broadcast=True)

@app.route('/api/post/<post_id>', methods=['DELETE'])
@login_required
def delete_post(post_id):
    post = Post.query.get(post_id)
    if not post:
        return jsonify({'error': 'Post not found'}), 404
    if post.handle != current_user.handle:
        return jsonify({'error': 'Unauthorized'}), 403

    # Decrement parent reply count
    if post.parent_id:
        parent = Post.query.get(post.parent_id)
        if parent:
            parent.reply_count = max(0, parent.reply_count - 1)

    db.session.delete(post)
    db.session.commit()

    # Broadcast deletion to all clients
    socketio.emit('delete_post', {'id': post_id}, broadcast=True)
    return jsonify({'success': True})


@app.route('/api/post/<post_id>', methods=['PATCH'])
@login_required
def edit_post(post_id):
    post = Post.query.get(post_id)
    if not post:
        return jsonify({'error': 'Post not found'}), 404
    if post.handle != current_user.handle:
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json()
    new_text = data.get('text', '').strip()
    if not new_text:
        return jsonify({'error': 'Text cannot be empty'}), 400

    post.text = new_text
    db.session.commit()

    # Broadcast update to all clients
    socketio.emit('edit_post', {'id': post_id, 'text': new_text}, broadcast=True)
    return jsonify({'success': True})


# --- STORIES API ---
@app.route('/api/stories', methods=['POST'])
@login_required
def create_story():
    data = request.get_json(silent=True) or {}

    text       = (data.get('text') or '').strip()
    media_url  = data.get('media_url')
    media_type = data.get('media_type')

    if not text and not media_url:
        return jsonify({'error': 'text or media required'}), 400

    story = Story(
        user_id    = current_user.id,
        text       = text or None,
        media_url  = media_url,
        media_type = media_type,
        timestamp  = int(time.time() * 1000)
    )
    db.session.add(story)
    db.session.commit()

    return jsonify({'success': True, 'id': story.id})

@app.route('/api/stories/feed', methods=['GET'])
@login_required
def get_stories_feed():
    # Returns a list of users (followed + self) who have active stories in the last 24h
    twenty_four_hours_ago = int((time.time() - 24 * 3600) * 1000)
    
    following_ids = [u.id for u in current_user.followed]
    following_ids.append(current_user.id)
    
    # Query distinct users who have active stories
    active_story_users = User.query.join(Story).filter(
        User.id.in_(following_ids),
        Story.timestamp >= twenty_four_hours_ago
    ).all()
    
    res = []
    for u in active_story_users:
        res.append({
            'handle': u.handle,
            'name': u.display_name,
            'photo': u.profile_photo_url
        })
        
    return jsonify(res)

@app.route('/api/stories/<handle>', methods=['GET'])
@login_required
def get_user_stories(handle):
    clean_handle = handle.lstrip('@')
    user = User.query.filter(
        db.or_(User.handle == handle, User.handle == '@' + clean_handle)
    ).first()
    
    if not user:
        return jsonify({'error': 'User not found'}), 404
        
    # Get stories from the last 24 hours
    twenty_four_hours_ago = int((time.time() - 24 * 3600) * 1000)
    stories = Story.query.filter_by(user_id=user.id).filter(Story.timestamp >= twenty_four_hours_ago).order_by(Story.timestamp.asc()).all()
    
    return jsonify([s.to_dict() for s in stories])

@app.route('/api/trending')
@login_required
def get_trending():
    """Mine real hashtags and topics from the last 500 posts."""
    import re
    from collections import Counter

    recent_posts = Post.query.order_by(Post.timestamp.desc()).limit(500).all()
    hashtag_counter = Counter()
    word_counter = Counter()

    for post in recent_posts:
        if not post.text:
            continue
        # Extract hashtags
        tags = re.findall(r'#(\w+)', post.text)
        for tag in tags:
            if len(tag) > 2:
                hashtag_counter['#' + tag] += 1
        # Also extract meaningful words (>5 chars) from news-bot posts
        words = re.findall(r'\b([A-Z][a-z]{4,}(?:\s[A-Z][a-z]{3,})?)\b', post.text)
        for w in words:
            if len(w) > 5:
                word_counter[w] += 1

    trends = []
    # Top hashtags first
    for tag, count in hashtag_counter.most_common(3):
        trends.append({'name': tag, 'count': count, 'category': 'Trending'})
    # Then top capitalized topics (usually proper nouns/news headlines)
    for word, count in word_counter.most_common(5):
        if count > 1 and len(trends) < 6:
            trends.append({'name': word, 'count': count, 'category': 'In the news'})

    return jsonify(trends[:5])

@app.route('/api/who_to_follow')
@login_required
def who_to_follow():
    """Return up to 3 real users the current user doesn't already follow."""
    already_following_ids = [u.id for u in current_user.followed.all()]
    already_following_ids.append(current_user.id)

    # Exclude bot accounts and people already followed, pick most followed users
    suggestions = User.query.filter(
        User.id.notin_(already_following_ids),
        User.account_tier != 'Bot'
    ).all()

    # Sort by number of followers descending
    suggestions.sort(key=lambda u: u.followers.count(), reverse=True)

    result = []
    for u in suggestions[:3]:
        result.append({
            'handle': u.handle,
            'name': u.display_name,
            'photo': u.profile_photo_url,
            'followers': u.followers.count()
        })
    return jsonify(result)

@app.route('/api/ai/chat', methods=['POST'])
@login_required
def ai_chat():
    try:
        data = request.get_json()
        history = data.get('history', [])[-6:]
        message = data.get('message', '')
        system_prompt = "You are Rooted AI 🌿 — a friendly, concise assistant on a nature-inspired Indian social platform. Keep replies under 150 words."

        import requests

        # --- Option A: Groq (Primary - FREE & FAST) ---
        groq_key = os.getenv('GROQ_API_KEY')
        if groq_key:
            messages = [{"role": "system", "content": system_prompt}]
            for h in history:
                role = "assistant" if h['role'] == "model" else h['role']
                messages.append({"role": role, "content": h['content']})
            messages.append({"role": "user", "content": message})

            headers = {
                "Authorization": f"Bearer {groq_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "llama-3.3-70b-versatile",
                "messages": messages,
                "temperature": 0.7
            }
            resp = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
            if not resp.ok:
                raise Exception(f"{resp.status_code} {resp.text}")
            return jsonify({'reply': resp.json()['choices'][0]['message']['content']})

        # --- Option B: Grok (Secondary - xAI) ---
        xai_key = os.getenv('XAI_API_KEY')
        if xai_key:
            messages = [{"role": "system", "content": system_prompt}]
            for h in history:
                role = "assistant" if h['role'] == "model" else h['role']
                messages.append({"role": role, "content": h['content']})
            messages.append({"role": "user", "content": message})

            headers = {
                "Authorization": f"Bearer {xai_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "grok-beta",
                "messages": messages,
                "temperature": 0.7
            }
            resp = requests.post("https://api.x.ai/v1/chat/completions", headers=headers, json=payload)
            if not resp.ok:
                raise Exception(f"{resp.status_code} {resp.text}")
            return jsonify({'reply': resp.json()['choices'][0]['message']['content']})

        return jsonify({'reply': '⚡ Rooted AI is not configured. Add GROQ_API_KEY to your environment.'})

    except Exception as e:
        err = str(e)
        if 'quota' in err.lower() or '429' in err or 'rate' in err.lower():
            return jsonify({'reply': '⏳ Rooted AI is resting a moment. Please try again in 30 seconds 🌿'})
        # Show actual error for debugging
        return jsonify({'reply': f'AI Error: {err[:200]}'})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3001))
    socketio.run(app, host='0.0.0.0', port=port, debug=True)

