from datetime import datetime
from .extensions import db
from flask_login import UserMixin
from sqlalchemy import Enum
from sqlalchemy.dialects.mysql import LONGTEXT, LONGBLOB

def load_user(user_id):
    return User.query.get(int(user_id))

class User(db.Model, UserMixin):
    # Table Identifiers
    id = db.Column(db.Integer, primary_key=True)

    # Table Columns
    email_address = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(60), nullable=False)

    # Relationships
    # email_accounts = db.relationship('EmailAccount', backref='user', lazy=True, cascade="all, delete-orphan")
    # email_folders = db.relationship('EmailFolder', backref='user', lazy=True, cascade="all, delete-orphan")
    # emails = db.relationship('Email', backref='user', lazy=True, cascade="all, delete-orphan")
    # email_addresses = db.relationship('EmailAddress', backref='user', lazy=True, cascade="all, delete-orphan")
    # filters = db.relationship('Filter', backref='user', lazy=True, cascade="all, delete-orphan")
    # prompts = db.relationship('AIPrompt', backref='user', lazy=True, cascade="all, delete-orphan")
    # results = db.relationship('Result', backref='user', lazy=True, cascade="all, delete-orphan", overlaps="user_results")

    # Constraints
    __table_args__ = (
        db.UniqueConstraint('email_address', name='uq_user_email_address'),
    )

class EmailAccount(db.Model):
    # Table Identifiers
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)

    # Table Columns
    email_address = db.Column(db.String(255), nullable=False)
    password = db.Column(db.String(255), nullable=False)
    provider = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='Active')
    imap_server = db.Column(db.String(120), nullable=True)
    imap_port = db.Column(db.String(10), nullable=True)
    imap_use_ssl = db.Column(db.Boolean, default=True, nullable=True)
    start_date = db.Column(db.DateTime, nullable=True)
    end_date = db.Column(db.DateTime, nullable=True)

    # Relationships
    # email_folders = db.relationship('EmailFolder', backref='account', lazy=True, cascade="all, delete-orphan")
    # emails = db.relationship('Email', backref='account', lazy=True, cascade="all, delete-orphan")
    # email_addresses = db.relationship('EmailAddress', backref='account', lazy=True, cascade="all, delete-orphan")
    # results = db.relationship('Result', backref='account', lazy=True, cascade="all, delete-orphan", overlaps="account_results")

    # Constraints
    __table_args__ = (
        db.UniqueConstraint('user_id', 'email_address', name='uq_email_account_email_address'),
    )

class EmailFolder(db.Model):
    # Table Identifiers
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    email_account_id = db.Column(db.Integer, db.ForeignKey('email_account.id', ondelete='CASCADE'), nullable=False)

    # Table Columns
    folder_name = db.Column(db.String(255), nullable=False)
    email_count = db.Column(db.Integer, nullable=False)

    # Constraints
    __table_args__ = (
        db.UniqueConstraint('user_id', 'email_account_id', 'folder_name', name='uq_email_folder_folder_name'),
    )

class Email(db.Model):
    # Table Identifiers
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    email_account_id = db.Column(db.Integer, db.ForeignKey('email_account.id', ondelete='CASCADE'), nullable=False)
    email_folder_id = db.Column(db.Integer, db.ForeignKey('email_folder.id', ondelete='CASCADE'), nullable=False)

    # Table Columns
    email_imap_id = db.Column(db.String(255), nullable=True)
    email_date = db.Column(db.DateTime, nullable=False)
    sender = db.Column(db.String(255), nullable=False, index=True)
    receivers = db.Column(db.Text, nullable=False)
    action = db.Column(Enum('include', 'ignore', 'exclude', name='email_action'), nullable=False, default='ignore')
    raw_data = db.Column(LONGBLOB, nullable=False)
    email_subject = db.Column(db.String(255), nullable=False)
    text_content = db.Column(LONGTEXT, nullable=True)

    # Constraints
    __table_args__ = (
        db.Index('ix_email_text_content', 'text_content', mysql_prefix='FULLTEXT'),
    )

    # String Representation
    def __repr__(self):
        return f"<Email {self.id}>"

class EmailAddress(db.Model):
    # Table Identifiers
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE', name='fk_email_address_user'), nullable=False)
    email_account_id = db.Column(db.Integer, db.ForeignKey('email_account.id', ondelete='CASCADE', name='fk_email_address_email_account'), nullable=False)

    # Table Columns
    email_address = db.Column(db.String(255), nullable=False, index=True)
    action = db.Column(Enum('include', 'ignore', 'exclude', name='email_action'), nullable=False, default='ignore')
    count = db.Column(db.Integer, default=0)

    # Constraints
    __table_args__ = (
        db.UniqueConstraint('user_id', 'email_account_id', 'email_address', name='uq_email_address_email_address'),
    )

class Filter(db.Model):
    # Table Identifiers
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    email_account_id = db.Column(db.Integer, db.ForeignKey('email_account.id', ondelete='CASCADE'), nullable=False)

    # Table Columns
    filter = db.Column(db.String(255), nullable=False)
    action = db.Column(Enum('include', 'ignore', 'exclude', name='email_action'), nullable=False, default='ignore')
    order = db.Column(db.Integer, nullable=False, default=0)

class AIPrompt(db.Model):
    # Table Identifiers
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    email_account_id = db.Column(db.Integer, db.ForeignKey('email_account.id', ondelete='CASCADE'), nullable=False)

    # Table Columns
    prompt_text = db.Column(db.Text, nullable=False)
    action = db.Column(Enum('include', 'ignore', 'exclude', name='email_action'), nullable=False, default='ignore')
    order = db.Column(db.Integer, nullable=False, default=0)

class Result(db.Model):
    # Table Identifiers
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    email_account_id = db.Column(db.Integer, db.ForeignKey('email_account.id', ondelete='CASCADE'), nullable=False)

    # Table Columns
    name = db.Column(db.String(255), nullable=True)
    created_date = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    log_entry = db.Column(LONGTEXT, nullable=True)
    status = db.Column(db.String(255), nullable=True)
    file_url = db.Column(LONGTEXT, nullable=True)
    zip_password = db.Column(db.String(255), nullable=True)
