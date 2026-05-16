import os
import time
import uuid
import feedparser
import re

os.environ['SKIP_EVENTLET'] = '1'
from app import app, db, User, Post

# Configure your RSS feeds here
RSS_FEEDS = [
    {
        "url": "https://timesofindia.indiatimes.com/rssfeeds/-2128936835.cms",
        "node": "India News",
        "bot_handle": "@IndiaNews",
        "bot_name": "Rooted India 🇮🇳",
        "bot_photo": "https://images.unsplash.com/photo-1530122037265-a5f1f91d3b99?w=200&h=200&fit=crop"
    },
    {
        "url": "https://timesofindia.indiatimes.com/rssfeeds/1081479906.cms",
        "node": "Entertainment",
        "bot_handle": "@RootedEnt",
        "bot_name": "Rooted Entertainment 🎬",
        "bot_photo": "https://images.unsplash.com/photo-1598899134739-24c46f58b8c0?w=200&h=200&fit=crop"
    },
    {
        "url": "https://timesofindia.indiatimes.com/rssfeeds/2647163.cms", 
        "node": "Environment",
        "bot_handle": "@RootedNature",
        "bot_name": "Rooted Environment 🌿",
        "bot_photo": "https://images.unsplash.com/photo-1441974231531-c6227db76b6e?w=200&h=200&fit=crop"
    },
    {
        "url": "https://timesofindia.indiatimes.com/rssfeeds/66949542.cms",
        "node": "Technology",
        "bot_handle": "@RootedTech",
        "bot_name": "Rooted Tech 💻",
        "bot_photo": "https://images.unsplash.com/photo-1518770660439-4636190af475?w=200&h=200&fit=crop"
    }
]

def clean_html(raw_html):
    """Removes HTML tags from RSS descriptions"""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    # Truncate if too long so it looks good on the feed
    return cleantext[:200] + '...' if len(cleantext) > 200 else cleantext

def get_or_create_bot(handle, name, photo_url):
    user = User.query.filter_by(handle=handle).first()
    if not user:
        # Create a bot user dynamically
        email = f"{handle.replace('@', '')}@bot.rooted-feed.online"
        user = User(
            email=email,
            display_name=name,
            handle=handle,
            bio="Automated news bot bringing you the latest updates from the real world. 🌿",
            profile_photo_url=photo_url,
            is_private=False,
            account_tier='Bot'
        )
        db.session.add(user)
        db.session.commit()
        print(f"Created new bot user: {handle}")
    return user

def run_bot():
    print("🌿 Starting Rooted News Bot...")
    with app.app_context():
        for feed_info in RSS_FEEDS:
            print(f"Fetching {feed_info['node']} from {feed_info['url']}...")
            bot = get_or_create_bot(feed_info['bot_handle'], feed_info['bot_name'], feed_info['bot_photo'])
            
            # Parse the RSS feed
            feed = feedparser.parse(feed_info['url'])
            
            # Get the top 3 most recent entries
            entries = feed.entries[:3]
            
            new_posts_count = 0
            for entry in entries:
                title = entry.get('title', '')
                link = entry.get('link', '')
                description = clean_html(entry.get('description', ''))
                
                # Format the text to look nice on Rooted
                text = f"**{title}**\n\n{description}\n\nRead more: {link}"
                
                # Try to extract a thumbnail image URL from the RSS feed
                media_url = None
                if 'media_content' in entry and len(entry.media_content) > 0:
                    media_url = entry.media_content[0].get('url')
                elif 'enclosures' in entry and len(entry.enclosures) > 0:
                    media_url = entry.enclosures[0].get('href')
                # Try standard image dict if available
                elif 'image' in entry and hasattr(entry.image, 'href'):
                     media_url = entry.image.href
                    
                # Check if this link was already posted to avoid duplicate spam!
                existing_post = Post.query.filter(Post.handle == bot.handle, Post.text.like(f"%{link}%")).first()
                if existing_post:
                    continue
                    
                # Create the Post
                post_id = str(int(time.time() * 1000)) + str(uuid.uuid4())[:8]
                post = Post(
                    id=post_id,
                    sender=bot.display_name,
                    handle=bot.handle,
                    text=text,
                    media_url=media_url,
                    media_type='image' if media_url else None,
                    timestamp=int(time.time() * 1000),
                    node=feed_info['node'],
                )
                db.session.add(post)
                db.session.commit()
                new_posts_count += 1
                time.sleep(1) # Slight delay for timestamp ordering
                
            print(f"Added {new_posts_count} new posts for {feed_info['node']}.")
    print("✅ News Bot finished successfully.")

if __name__ == "__main__":
    run_bot()
