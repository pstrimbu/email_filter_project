from datetime import datetime
from .models import Result
from .extensions import db
import inspect

def update_log_entry(user_id, email_account_id, log_entry, status='processing'):
    if user_id is None:
        caller = inspect.stack()[1].function
        print(f"ERROR: update_log_entry: User ID cannot be None. Called from {caller}")
        return
    if email_account_id is None:
        caller = inspect.stack()[1].function
        print(f"ERROR: update_log_entry: Account ID cannot be None. Called from {caller}")
        return
    # Add a timestamp to the log entry
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f"[{timestamp}] {log_entry}\n"

    # Log to console or file
    print(f"Log Entry: {status} | {log_entry.strip()}")

    # Update or create the Result entry
    result = Result.query.filter_by(user_id=user_id, email_account_id=email_account_id).first()
    if result:
        result.log_entry += log_entry
        result.status = status
    else:
        result = Result(user_id=user_id, email_account_id=email_account_id, log_entry=log_entry, status=status)
        db.session.add(result)
    db.session.commit()
