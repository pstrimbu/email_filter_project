from datetime import datetime
from .extensions import db
from flask_login import UserMixin
from sqlalchemy import Enum
from sqlalchemy.dialects.mysql import LONGTEXT, LONGBLOB

def load_user(user_id):
    return User.query.get(int(user_id))

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(60), nullable=False)
    filters = db.relationship('Filter', backref='user', lazy=True)
    email_accounts = db.relationship('EmailAccount', backref='user', lazy=True)
    email_addresses = db.relationship('EmailAddress', back_populates='user', lazy=True)  # Corresponding relationship

class EmailAccount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    password = db.Column(db.String(255), nullable=False)  # Consider encrypting this
    provider = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='Active')
    imap_server = db.Column(db.String(120), nullable=True)
    imap_port = db.Column(db.String(10), nullable=True)
    imap_use_ssl = db.Column(db.Boolean, default=True, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    start_date = db.Column(db.DateTime, nullable=True)
    end_date = db.Column(db.DateTime, nullable=True)
    email_addresses = db.relationship('EmailAddress', back_populates='account', lazy=True)

class Email(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('email_account.id'), nullable=False)
    email_id = db.Column(db.String(255), nullable=False)
    email_date = db.Column(db.DateTime, nullable=False)
    sender = db.Column(db.String(120), nullable=False)
    receivers = db.Column(db.Text, nullable=False)
    action = db.Column(db.String(20), nullable=False, default='ignore')
    folder = db.Column(db.String(100), nullable=False)
    raw_data = db.Column(LONGBLOB, nullable=False)
    text_content = db.Column(LONGTEXT, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'account_id', 'email_id', name='uq_user_account_email'),
        db.Index('ix_email_text_content', 'text_content', mysql_prefix='FULLTEXT')
    )

    def __repr__(self):
        return f"<Email {self.email_id}>"

class EmailAddress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    state = db.Column(Enum('include', 'ignore', 'exclude', name='email_state'), nullable=False, default='ignore')
    email_account_id = db.Column(db.Integer, db.ForeignKey('email_account.id', name='fk_email_address_email_account'), nullable=False)  # Foreign key to EmailAccount
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', name='fk_email_address_user'), nullable=False)  # Foreign key to User

    # Relationships
    account = db.relationship('EmailAccount', back_populates='email_addresses')
    user = db.relationship('User', back_populates='email_addresses')

    __table_args__ = (
        db.UniqueConstraint('user_id', 'email_account_id', 'email', name='uq_user_account_email'),
    )
class EmailFolder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('email_account.id'), nullable=False)
    folder = db.Column(db.String(255), nullable=False)
    email_count = db.Column(db.Integer, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'account_id', 'folder', name='_user_account_folder_uc'),
    )

class Filter(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('email_account.id'), nullable=False)
    filter = db.Column(db.String(255), nullable=False)
    action = db.Column(db.String(50), nullable=False)
    order = db.Column(db.Integer, nullable=False, default=0)

class AIPrompt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('email_account.id'), nullable=False)
    prompt_text = db.Column(db.Text, nullable=False)
    action = db.Column(db.String(50), nullable=False)
    order = db.Column(db.Integer, nullable=False, default=0)

class Result(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('email_account.id'), nullable=False)
    name = db.Column(db.String(255), nullable=True)
    created_date = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    log_entry = db.Column(LONGTEXT, nullable=True)
    status = db.Column(db.String(255), nullable=True)
    file_url = db.Column(LONGTEXT, nullable=True)

    user = db.relationship('User', backref='results', lazy=True)
    account = db.relationship('EmailAccount', backref='results', lazy=True)