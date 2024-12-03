import os
from dotenv import load_dotenv

# Load environment variables from the appropriate .env file
env_file = 'dev.env' if os.environ.get('FLASK_ENV') == 'development' else 'prod.env'
load_dotenv(os.path.join(os.path.dirname(__file__), env_file))

basedir = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY')
    SQLALCHEMY_DATABASE_URI = (
        f"mysql://{os.environ.get('DB_USER')}:{os.environ.get('DB_PASSWORD')}"
        f"@{os.environ.get('DB_HOST')}:{os.environ.get('DB_PORT')}/{os.environ.get('DB_NAME')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GOOGLE_CLIENT_ID = 'YOUR_GOOGLE_CLIENT_ID'
    GOOGLE_CLIENT_SECRET = 'YOUR_GOOGLE_CLIENT_SECRET'
    GOOGLE_DISCOVERY_URL = (
        "https://accounts.google.com/.well-known/openid-configuration"
    )
    
    # Increase pool size and overflow
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 50,  # Further increase the pool size
        'max_overflow': 100,  # Further increase the overflow limit
        'pool_timeout': 60,  # Increase the timeout for getting a connection from the pool
    }