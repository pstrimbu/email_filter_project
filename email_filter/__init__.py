import os
from flask import Flask
from flask_wtf import CSRFProtect
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from .extensions import db, bcrypt, login_manager
from .models import User, AIPrompt
from .config import Config


# IMPORTANT MANUAL DB STEP:
# ALTER TABLE email MODIFY COLUMN raw_data LONGBLOB;

migrate = Migrate()  # Initialize Migrate outside the function

def create_app():
    app = Flask(__name__)
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

    # Check database connectivity
    with app.app_context():
        try:
            # Attempt to connect to the database
            db.session.execute(text('SELECT 1'))
            print("Database connection successful.")
        except OperationalError as e:
            print("Database connection failed:", e)
            raise

    return app
