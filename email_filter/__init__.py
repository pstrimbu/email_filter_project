import os
import logging
from flask import Flask
from flask_wtf import CSRFProtect
from flask_migrate import Migrate
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from .extensions import db, bcrypt, login_manager
from .models import User
from .config import Config
from dotenv import load_dotenv

# Load environment variables from the appropriate .env file
env_file = 'dev.env' if os.environ.get('FLASK_ENV') == 'development' else 'prod.env'
load_dotenv(os.path.join(os.path.dirname(__file__), env_file))

# Configure logging using the LOG_LEVEL from the environment
log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(level=getattr(logging, log_level, logging.INFO))

# IMPORTANT MANUAL DB STEP:
# ALTER TABLE email MODIFY COLUMN raw_data LONGBLOB;

migrate = Migrate()  # Initialize Migrate outside the function

def create_app():
    app = Flask(__name__, static_folder='static')
    app.config.from_object(Config)  # Load configuration from Config class

    # Ensure the instance folder exists
    instance_path = os.path.join(app.root_path, 'instance')
    os.makedirs(instance_path, exist_ok=True)

    # Initialize extensions
    csrf = CSRFProtect()
    csrf.init_app(app)
    db.init_app(app)  # Ensure db is initialized with the app
    migrate.init_app(app, db)  # Initialize Migrate with app and db
    bcrypt.init_app(app)
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # Import and initialize routes
    from .routes import init_routes
    init_routes(app)

    # Check database connectivity and set lock wait timeout
    with app.app_context():
        try:
            # Attempt to connect to the database
            db.session.execute(text('SELECT 1'))
            logging.info("Database connection successful.")  # Use logging

            # Set the innodb_lock_wait_timeout
            db.session.execute(text('SET GLOBAL innodb_lock_wait_timeout = 120'))
            logging.info("Set innodb_lock_wait_timeout to 120 seconds.")

        except OperationalError as e:
            logging.error("Database connection failed: %s", e)  # Use logging
            raise

    return app
