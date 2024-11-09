from datetime import datetime
from .models import Result, EmailAccount
from .extensions import db

def update_log_entry(user_id, account_id, log_entry, status='processing', file_url=None, name=None):
    if account_id is None:
        raise ValueError("Account ID cannot be None")

    # Fetch user email and account date range
    email_account = EmailAccount.query.get(account_id)
    if not email_account:
        raise ValueError(f"No EmailAccount found for account_id: {account_id}")

    # Add a timestamp to the log entry
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f"[{timestamp}] {log_entry}\n"

    # Log to console or file
    print(f"Log Entry: {status} | {log_entry.strip()}")

    # Update or create the Result entry
    result = Result.query.filter_by(user_id=user_id, account_id=account_id).first()
    if result:
        result.log_entry += log_entry
        result.status = status
        result.file_url = file_url
        result.name = name
    else:
        result = Result(user_id=user_id, account_id=account_id, log_entry=log_entry, name=name, status=status, file_url=file_url)
        db.session.add(result)
    db.session.commit()
