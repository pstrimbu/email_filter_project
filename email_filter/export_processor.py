from flask import current_app
from sqlalchemy import text
from sqlalchemy import or_
from .models import Email, EmailAddress, Filter, AIPrompt, Result, EmailAccount
from .extensions import db
from .aws import SpotInstanceManager, delete_file_from_s3, upload_file_to_s3, generate_presigned_url
from email_filter.logger import update_log_entry
import mailbox
import tempfile
from datetime import datetime
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from requests.exceptions import RequestException
import random
import string
import pyminizip
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Ollama API details
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL")
HEADERS = {"x-api-key": OLLAMA_API_KEY, "Content-Type": "application/json"}

# Initialize SpotInstanceManager instance
spot_instance_manager = SpotInstanceManager()

async def process_emails(user_id, account_id):
    if account_id is None:
        raise ValueError("Account ID cannot be None")

    try:
        # Set all emails to 'ignore' before processing
        db.session.query(Email).filter_by(user_id=user_id, account_id=account_id).update({'action': 'ignore'}, synchronize_session=False)
        db.session.commit()
    except Exception as e:
        log_entry = f"Error setting emails to ignore: {e}"
        update_log_entry(user_id, account_id, log_entry, status='error')
        return

    try:
        # Delete existing file from S3
        delete_file_from_s3(user_id, account_id)

        # Delete existing Result entry for the user and account
        existing_result = Result.query.filter_by(user_id=user_id, account_id=account_id).first()
        if existing_result:
            db.session.delete(existing_result)
            db.session.commit()        
    except Exception as e:
        log_entry = f"Error deleting existing results: {e}"
        update_log_entry(user_id, account_id, log_entry, status='error')
        return

    update_log_entry(user_id, account_id, 'Processing started', status='started')

    try:
        # Get the count of all emails for the user and account
        total_emails = db.session.query(Email).filter_by(user_id=user_id, account_id=account_id).count()
        
        # Initialize log entry
        log_entry = f"Total emails found: {total_emails}"
        update_log_entry(user_id, account_id, log_entry, status='processing')
    except Exception as e:
        log_entry = f"Error fetching email count: {e}"
        update_log_entry(user_id, account_id, log_entry, status='error')
        return

    try:
        # Check if there are any email addresses set to include/exclude
        has_email_addresses = db.session.query(EmailAddress).filter(
            EmailAddress.user_id == user_id,
            EmailAddress.state.in_(['include', 'exclude'])
        ).count() > 0

        if has_email_addresses:
            process_email_addresses(user_id, account_id)
    except Exception as e:
        log_entry = f"Error processing email addresses: {e}"
        update_log_entry(user_id, account_id, log_entry, status='error')
        return

    ignored = db.session.query(Email).filter_by(user_id=user_id, account_id=account_id, action='ignore').count()
    log_entry = f"Remaining: {ignored}"
    update_log_entry(user_id, account_id, log_entry)

    try:
        # Check if there are any filters defined
        has_filters = db.session.query(Filter).filter_by(user_id=user_id, account_id=account_id).count() > 0

        if has_filters:
            process_filters(user_id, account_id)
    except Exception as e:
        log_entry = f"Error processing filters: {e}"
        update_log_entry(user_id, account_id, log_entry, status='error')
        return

    ignored = db.session.query(Email).filter_by(user_id=user_id, account_id=account_id, action='ignore').count()
    log_entry = f"Remaining: {ignored}"
    update_log_entry(user_id, account_id, log_entry)

    try:
        # Check if there are any prompts defined
        has_prompts = db.session.query(AIPrompt).filter_by(user_id=user_id, account_id=account_id).count() > 0

        if has_prompts:
            await process_prompts(user_id, account_id)
    except Exception as e:
        log_entry = f"Error processing prompts: {e}"
        update_log_entry(user_id, account_id, log_entry, status='error')
        return

    try:
        # Generate files and get the mbox filename and presigned URL
        mbox_filename, presigned_url = generate_files(user_id, account_id)
    except Exception as e:
        log_entry = f"Error generating files: {e}"
        update_log_entry(user_id, account_id, log_entry, status='error')
        return

    update_log_entry(user_id, account_id, 'Processing finished', status='finished', file_url=presigned_url, name=mbox_filename)

    # Notify AWS that the instance is no longer in use
    try:
        spot_instance_manager.terminate_instance()
    except Exception as e:
        log_entry = f"Error terminating instance: {e}"
        update_log_entry(user_id, account_id, log_entry, status='error')


def process_email_addresses(user_id, account_id):
    # Update emails where the sender is included
    result_included = db.session.query(Email).filter(
        Email.user_id == user_id,
        Email.account_id == account_id,
        Email.sender.in_(
            db.session.query(EmailAddress.email).filter(
                EmailAddress.user_id == user_id,
                EmailAddress.state == 'include'
            )
        )
    ).update({'action': 'include'}, synchronize_session=False)
    total_included = result_included  # Store the count of included emails

    # Update emails where the sender is excluded
    result_excluded = db.session.query(Email).filter(
        Email.user_id == user_id,
        Email.account_id == account_id,
        Email.sender.in_(
            db.session.query(EmailAddress.email).filter(
                EmailAddress.user_id == user_id,
                EmailAddress.state == 'exclude'
            )
        )
    ).update({'action': 'exclude'}, synchronize_session=False)
    total_excluded = result_excluded  # Store the count of excluded emails

    # Use raw SQL to update emails based on receivers
    included_emails = db.session.query(EmailAddress.email).filter(
        EmailAddress.user_id == user_id,
        EmailAddress.state == 'include'
    ).all()

    excluded_emails = db.session.query(EmailAddress.email).filter(
        EmailAddress.user_id == user_id,
        EmailAddress.state == 'exclude'
    ).all()

    included_emails_list = [email[0] for email in included_emails]
    excluded_emails_list = [email[0] for email in excluded_emails]

    # Update emails to 'include' if any receiver is in the included list
    for email in included_emails_list:
        result = db.session.execute(
            text("UPDATE email SET action = 'include' WHERE user_id = :user_id AND account_id = :account_id AND action = 'ignore' AND FIND_IN_SET(:email, receivers)"),
            {'user_id': user_id, 'account_id': account_id, 'email': email}
        )
        total_included += result.rowcount  # Add the number of affected rows to the counter

    for email in excluded_emails_list:
        result = db.session.execute(
            text("UPDATE email SET action = 'exclude' WHERE user_id = :user_id AND account_id = :account_id AND action = 'ignore' AND FIND_IN_SET(:email, receivers)"),
            {'user_id': user_id, 'account_id': account_id, 'email': email}
        )
        total_excluded += result.rowcount  # Add the number of affected rows to the counter

    # Commit the changes
    db.session.commit()

    # Log the results
    included = total_included
    excluded = total_excluded

    log_entry = f"Email address filter results - Included: {included}, Excluded: {excluded}"
    update_log_entry(user_id, account_id, log_entry)


def process_filters(user_id, account_id):
    # Fetch all filters for the user and account
    filters = Filter.query.filter_by(user_id=user_id, account_id=account_id).order_by(Filter.order).all()

    total_included = 0
    total_excluded = 0

    for filter_obj in filters:
        if filter_obj.action in ['include', 'exclude']:
            # Update emails to 'include' or 'exclude' where the filter matches and the action is 'ignore'
            result = db.session.query(Email).filter(
                Email.user_id == user_id,
                Email.account_id == account_id,
                # Email.action == 'ignore',
                Email.text_content.contains(filter_obj.filter)
            ).update({'action': filter_obj.action}, synchronize_session=False)

            # Increment the appropriate counter based on the filter action
            if filter_obj.action == 'include':
                total_included += result
            elif filter_obj.action == 'exclude':
                total_excluded += result

    # Commit the changes
    db.session.commit()

    log_entry = f"Filter results - Included: {total_included}, Excluded: {total_excluded}"
    update_log_entry(user_id, account_id, log_entry)


async def process_prompts(user_id, account_id):
    # Register with AWS for a spot instance and get the IP address
    try:
        public_ip = await spot_instance_manager.request_instance(user_id, account_id)
        if not public_ip:
            log_entry = "No public IP address found. Cannot process prompts."
            update_log_entry(user_id, account_id, log_entry, status='error')
            return
        ollama_api_url = f"http://{public_ip}:5000/api"  # Use the IP address to form the URL
        log_entry = f"AI Server active. Processing prompts..."
        update_log_entry(user_id, account_id, log_entry, status='processing')
    except Exception as e:
        log_entry = f"Error requesting spot instance: {e}"
        update_log_entry(user_id, account_id, log_entry, status='error')
        return

    # Fetch emails with action 'ignore'
    emails = db.session.query(Email).filter_by(user_id=user_id, account_id=account_id, action='ignore').all()

    included = 0
    excluded = 0
    ignored = 0
    cant_process = 0
    ai_prompts = AIPrompt.query.filter_by(user_id=user_id, account_id=account_id).order_by(AIPrompt.order).all()

    # Use ThreadPoolExecutor to process emails concurrently
    with ThreadPoolExecutor(max_workers=5) as executor:
        with current_app.app_context(): 
            future_to_email = {executor.submit(call_ollama_api, prompt.prompt_text, email, ollama_api_url, user_id, account_id): email for prompt in ai_prompts for email in emails}

            start_time = time.time()
            total_emails = len(future_to_email)
            last_log_time = start_time
            log_interval = 10  # Log every 10 seconds

            for future in as_completed(future_to_email):
                email = future_to_email[future]
                try:
                    response = future.result()
                    match response:
                        case '1': 
                            email.action = 'include'
                            included += 1
                        case '0': 
                            email.action = 'exclude'
                            excluded += 1
                        case _: 
                            ignored += 1
                            if "can't" in response:
                                cant_process += 1
                            print(f"Unexpected response received for email {email.id}.\n{response[:300]}\n--------------------------------\n")
                            email.action = 'ignore'
                except Exception as e:
                    print(f"Error processing email {email.id}: {e}")
                    email.action = 'ignore'

                # Time-based logging
                current_time = time.time()
                if current_time - last_log_time >= log_interval:
                    elapsed_time = current_time - start_time
                    processed = included + excluded + ignored
                    average_time = elapsed_time / processed
                    remaining = total_emails - processed
                    projected_remaining_time = average_time * remaining

                    log_entry = (f"Processed {processed}/{total_emails} emails - "
                                f"Included: {included}, Excluded: {excluded}, Remaining: {remaining}, "
                                f"Projected remaining time: {projected_remaining_time:.2f}s")
                    update_log_entry(user_id, account_id, log_entry)
                    last_log_time = current_time

    db.session.commit()
    log_entry = f"Prompts results - Included: {included}, Excluded: {excluded}, Cant process: {cant_process}"
    update_log_entry(user_id, account_id, log_entry)

def call_ollama_api(prompt_text, email, ollama_api_url, user_id, account_id):
    """Call Ollama API with retries."""
    if not ollama_api_url:
        print(f"[ERROR] {ollama_api_url} is null for email ID {email.id}")
        return False
    
    start_time = time.time()
    spot_instance_manager.update_last_interaction()

    email_text = email.text_content
    MAX_LENGTH = int(os.getenv("EMAIL_MAX_LENGTH", 5000))  # Default to 5000 if not set
    email_text = email_text[:MAX_LENGTH] if email_text else ""

    # Load system prompt from environment and format it
    system_prompt_template = os.getenv("SYSTEM_PROMPT", "Default prompt text")
    system_prompt = system_prompt_template.format(prompt_text=prompt_text, email_text=email_text)

    max_retries = 10
    backoff_factor = 0.5
    for attempt in range(max_retries):
        if not spot_instance_manager.check_status():
            log_entry = f"No active instance. Cannot process prompts."
            update_log_entry(user_id, account_id, log_entry, status='error')
            return False
        try:
            response = requests.post(ollama_api_url, headers=HEADERS, json={"query": system_prompt, "model": OLLAMA_MODEL})
            if response.status_code == 200:
                return response.json().get('response', 'default response')
            if response.status_code == 500:
                log_entry = f"AI Server error 500. Retrying in {backoff_factor * (2 ** attempt)} seconds"
                update_log_entry(user_id, account_id, log_entry)
        except RequestException as e:
            print(f"Error processing email {email.id}: {e}. Retrying in {backoff_factor * (2 ** attempt)} seconds")
            time.sleep(backoff_factor * (2 ** attempt))
    return 'no response received'

def generate_files(user_id, account_id):
    """Generates and uploads email files."""
    email_account = EmailAccount.query.get(account_id)
    if not email_account:
        raise ValueError(f"No EmailAccount found for account_id: {account_id}")

    email_address = email_account.email
    date_range = f"{email_account.start_date.strftime('%Y%m%d')}-{email_account.end_date.strftime('%Y%m%d')}"
    timestamp = datetime.now().strftime('%Y%m%d%H%M')
    mbox_filename = f"{email_address}-{date_range}-{timestamp}.mbox"

    zip_password = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
    mbox_path = os.path.join(tempfile.gettempdir(), mbox_filename)
    mbox = mailbox.mbox(mbox_path, create=True)
    try:
        query = db.session.query(Email).filter_by(user_id=user_id, account_id=account_id, action='include')
        for email in query.yield_per(100):
            mbox.add(mailbox.mboxMessage(email.raw_data))
    finally:
        mbox.close()

    zip_filename = f"{email_address}-{date_range}-{timestamp}.zip"
    with tempfile.NamedTemporaryFile(delete=False) as temp_zip_file:
        zip_path = temp_zip_file.name
        pyminizip.compress(mbox_path, None, zip_path, zip_password, 5)

    upload_file_to_s3(zip_path, 'mailmatch', zip_filename)

    presigned_url = generate_presigned_url('mailmatch', zip_filename)

    log_entry = f"Generated zip file and uploaded to S3. Download link: {presigned_url}"
    update_log_entry(user_id, account_id, log_entry, status='finished', file_url=presigned_url, name=zip_filename)

    result = Result.query.filter_by(user_id=user_id, account_id=account_id).first()
    if result:
        result.zip_password = zip_password
        db.session.commit()

    os.remove(mbox_path)
    os.remove(zip_path)

    return zip_filename, presigned_url
