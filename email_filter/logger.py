from datetime import datetime
from .models import Result
from .extensions import db

def update_log_entry(user_id, account_id, log_entry, status='processing'):
    if account_id is None:
        raise ValueError("Account ID cannot be None")

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
    else:
        result = Result(user_id=user_id, account_id=account_id, log_entry=log_entry, status=status)
        db.session.add(result)
    db.session.commit()
