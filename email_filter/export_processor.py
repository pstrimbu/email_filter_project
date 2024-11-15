from flask import current_app
from sqlalchemy import text
from sqlalchemy import or_
from .models import Email, EmailAddress, Filter, AIPrompt, Result, EmailAccount
from .extensions import db
from .aws import SpotInstanceManager, InstanceManager, delete_file_from_s3, upload_file_to_s3, generate_presigned_url
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
from email_filter.globals import processing_status
import threading

# Load environment variables from .env file
load_dotenv()

# Ollama API details
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL")
HEADERS = {"x-api-key": OLLAMA_API_KEY, "Content-Type": "application/json"}

# Initialize SpotInstanceManager instance
spot_instance_manager = SpotInstanceManager()
instance_manager = InstanceManager()

# Global variable to keep track of the monitoring thread
monitoring_thread = None

def stop(user_id, account_id):
    global monitoring_thread

    processing_status[(user_id, account_id)] = 'stopping'
    if monitoring_thread is None or not monitoring_thread.is_alive():
        app_context = current_app._get_current_object()
        monitoring_thread = threading.Thread(target=monitor_threads, args=(user_id, account_id, app_context))
        monitoring_thread.start()

def start_monitoring_thread(user_id, account_id):
    global monitoring_thread
    if monitoring_thread is None or not monitoring_thread.is_alive():
        # Capture the current app context
        app_context = current_app._get_current_object()
        monitoring_thread = threading.Thread(target=monitor_threads, args=(user_id, account_id, app_context))
        monitoring_thread.start()

def monitor_threads(user_id, account_id, app_context):
    global processing_status
    with app_context.app_context():  # Use the passed app context
        last_log_time = time.time()
        log_interval = 30  # Log every 30 seconds

        while True:
            # Check if there are any active threads
            active_threads = [t for t in threading.enumerate() if t.name.startswith('ThreadPoolExecutor')]
            num_active_threads = len(active_threads)

            # Log the number of active threads every 30 seconds
            current_time = time.time()
            if current_time - last_log_time >= log_interval:
                log_entry = f"Active AI request threads: {num_active_threads}"
                update_log_entry(user_id, account_id, log_entry, status='processing')
                last_log_time = current_time

            if not active_threads:
                update_log_entry(user_id, account_id, "All threads finished", status='finished')
                processing_status[(user_id, account_id)] = 'finished'
                break

            time.sleep(5)  # Check every 5 seconds

async def process_emails(user_id, account_id):
    global processing_status
    try:
        if user_id is None:
            raise ValueError("User ID cannot be None")
        if account_id is None:
            raise ValueError("Account ID cannot be None")

        processing_status[(user_id, account_id)] = 'running'

        # Start the monitoring thread
        start_monitoring_thread(user_id, account_id)

        # Delete previous results
        preprocess_cleanup(user_id, account_id)

        update_log_entry(user_id, account_id, 'Processing started', status='started')

        # LOG TOTAL EMAILS
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

        process_email_addresses(user_id, account_id)

        if processing_status.get((user_id, account_id)) == 'stopping':
            return {'success': False, 'error': 'stopped by user request'}

        process_filters(user_id, account_id)

        if processing_status.get((user_id, account_id)) == 'stopping':
            return {'success': False, 'error': 'stopped by user request'}

        await process_prompts(user_id, account_id)

        if processing_status.get((user_id, account_id)) == 'stopping':
            return {'success': False, 'error': 'stopped by user request'}
        
        try:
            # Generate files and get the mbox filename and presigned URL
            mbox_filename, presigned_url = generate_files(user_id, account_id)
            result = Result.query.filter_by(user_id=user_id, account_id=account_id).first()
            if result:
                result.user_id = user_id
                result.account_id = account_id
                result.name = mbox_filename
                result.file_url = presigned_url
                result.status = 'finished'
                result.log_entry = result.log_entry + '\nProcessing finished'
                db.session.add(result)
            db.session.commit()
        except Exception as e:
            log_entry = f"Error generating files: {e}"
            update_log_entry(user_id, account_id, log_entry, status='error')
            return
    finally:
        processing_status[(user_id, account_id)] = 'finished'


def preprocess_cleanup(user_id, account_id):
    try:
        # Set all emails to 'ignore' before processing
        db.session.query(Email).filter_by(user_id=user_id, account_id=account_id).update({'action': 'ignore'}, synchronize_session=False)
        db.session.commit()
    except Exception as e:
        log_entry = f"Error setting emails to ignore: {e}"
        update_log_entry(user_id, account_id, log_entry, status='error')
        return

    try:

        # Delete existing Result entry for the user and account
        existing_results = Result.query.filter_by(user_id=user_id, account_id=account_id)
        if existing_results:
            for result in existing_results:
                if result.name:
                    bucket_name = os.getenv("S3_BUCKET_NAME")
                    # Delete existing file from S3
                    delete_file_from_s3(bucket_name, result.name)

                # delete the result entry
                db.session.delete(result)
            db.session.commit()        
    except Exception as e:
        log_entry = f"Error deleting existing results: {e}"
        update_log_entry(user_id, account_id, log_entry, status='error')
        return


def process_email_addresses(user_id, account_id):
    # Check if there are any email addresses set to include/exclude
    has_email_addresses = db.session.query(EmailAddress).filter(
        EmailAddress.user_id == user_id,
        EmailAddress.state.in_(['include', 'exclude'])
    ).count() > 0

    if not has_email_addresses:
        return
    
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

    ignored = db.session.query(Email).filter_by(user_id=user_id, account_id=account_id, action='ignore').count()
    log_entry = f"Remaining: {ignored}"
    update_log_entry(user_id, account_id, log_entry)


def process_filters(user_id, account_id):

    # Check if there are any filters defined
    has_filters = db.session.query(Filter).filter_by(user_id=user_id, account_id=account_id).count() > 0
    if not has_filters:
        return

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

    ignored = db.session.query(Email).filter_by(user_id=user_id, account_id=account_id, action='ignore').count()
    log_entry = f"Remaining: {ignored}"
    update_log_entry(user_id, account_id, log_entry)


async def process_prompts(user_id, account_id):
    global processing_status
    # Check if there are any prompts defined
    has_prompts = db.session.query(AIPrompt).filter_by(user_id=user_id, account_id=account_id).count() > 0
    if not has_prompts:
        return

    try:
        # Register with AWS for a spot instance and get the IP address
        try:
            processor_type = os.getenv("PROCESSOR_TYPE", "spot")
            if processor_type == "spot":
                public_ip = await spot_instance_manager.request_instance(user_id, account_id)
            else:
                public_ip = await instance_manager.request_instance(user_id, account_id)
            if not public_ip:
                log_entry = "No public IP address found. Cannot process prompts."
                update_log_entry(user_id, account_id, log_entry, status='error')
                return
            ollama_api_url = f"http://{public_ip}:5000/api"  # Use the IP address to form the URL
            log_entry = f"AI Server active. Processing prompts..."
            update_log_entry(user_id, account_id, log_entry, status='processing')
        except Exception as e:
            log_entry = f"Error requesting instance: {e}"
            update_log_entry(user_id, account_id, log_entry, status='error')
            return

        # Fetch emails with action 'ignore'
        emails = db.session.query(Email).filter_by(user_id=user_id, account_id=account_id, action='ignore').all()

        included = 0
        excluded = 0
        ignored = 0
        cant_process = 0
        ai_prompts = AIPrompt.query.filter_by(user_id=user_id, account_id=account_id).order_by(AIPrompt.order).all()

        # Capture the current app context
        app_context = current_app._get_current_object()

        # Use ThreadPoolExecutor to process emails concurrently
        with ThreadPoolExecutor(max_workers=5) as executor:        
            with app_context.app_context():
                future_to_email = {}
                for prompt in ai_prompts:
                    for email in emails:
                        # Check stop condition before submitting new tasks
                        if processing_status.get((user_id, account_id)) == 'stopping':
                            return {'success': False, 'error': 'stopped by user request'}
                        
                        future = executor.submit(call_ollama_api, prompt.prompt_text, email, ollama_api_url, user_id, account_id, app_context)
                        future_to_email[future] = email

                start_time = time.time()
                total_emails = len(future_to_email)
                last_log_time = start_time
                log_interval = 10  # Log every 10 seconds

                for future in as_completed(future_to_email):
                    # Check stop condition within the loop
                    if processing_status.get((user_id, account_id)) == 'stopping':
                        return {'success': False, 'error': 'stopped by user request'}

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
                            case '2': # received "can't" response
                                cant_process += 1
                            case _: 
                                ignored += 1
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
                                    f"Elapsed: {elapsed_time:.2f}s, "
                                    f"Projected: {projected_remaining_time:.2f}s")
                        update_log_entry(user_id, account_id, log_entry)
                        last_log_time = current_time

        db.session.commit()
        log_entry = f"Prompts results - Included: {included}, Excluded: {excluded}, Cant process: {cant_process}"
        update_log_entry(user_id, account_id, log_entry)
    finally:
        # Notify AWS that the instance is no longer in use
        try:
            processor_type = os.getenv("PROCESSOR_TYPE", "spot")
            if processor_type == "spot":
                spot_instance_manager.terminate_instance(user_id)
            else:
                instance_manager.terminate_instance(user_id)
        except Exception as e:
            log_entry = f"Error terminating instance: {e}"
            update_log_entry(user_id, account_id, log_entry, status='error terminating instance')


def call_ollama_api(prompt_text, email, ollama_api_url, user_id, account_id, app_context):
    global processing_status
    """Call Ollama API with retries."""
    with app_context.app_context():
        if not ollama_api_url:
            print(f"[ERROR] {ollama_api_url} is null for email ID {email.id}")
            return False
        
        processor_type = os.getenv("PROCESSOR_TYPE", "spot")
        if processor_type == "spot":
            spot_instance_manager.update_last_interaction()
        else:
            instance_manager.update_last_interaction()

        email_text = email.text_content
        MAX_LENGTH = int(os.getenv("EMAIL_MAX_LENGTH", 5000))  # Default to 5000 if not set
        email_text = email_text[:MAX_LENGTH] if email_text else ""

        system_prompt_template = os.getenv("SYSTEM_PROMPT", "Default prompt text")
        system_prompt = system_prompt_template.format(prompt_text=prompt_text, email_text=email_text)

        max_retries = 20
        backoff_factor = 0.5
        for attempt in range(max_retries):
            if processing_status.get((user_id, account_id)) == 'stopping':
                return False
            
            processor_type = os.getenv("PROCESSOR_TYPE", "spot")
            if processor_type == "spot":
                if not spot_instance_manager.check_status():
                    print(f"No active instance. waiting for instance to start.")
                    time.sleep(backoff_factor * (2 ** attempt))
                    continue
            else:
                if not instance_manager.check_status():
                    print(f"No active instance. waiting for instance to start.")
                    time.sleep(backoff_factor * (2 ** attempt))
                    continue
                
            try:
                response = requests.post(ollama_api_url, headers=HEADERS, json={"query": system_prompt, "model": OLLAMA_MODEL})
                response_str = None
                if response.status_code == 200:
                    try:
                        response_json = response.json()
                        response_str = response_json.get('response', response_json)
                    except Exception as e:
                        print(f"call_ollama_api error {e}. response: {response.text}")

                    if isinstance(response_str, str):
                        # see if the response_str contains json, ex: '{ "response": "0" }'
                        try:
                            response_str_json = response_str.json()
                            response_str = response_str_json.get('response', response_json)
                        except Exception as e:
                            pass # no problem, we'll just use response_str as is

                    if response_str is not None and (response_str == '0' or response_str == '1'):
                        return response_str

                    if isinstance(response_str, str) and "can't" in response_str:
                        print(f"call_ollama_api received 'can't' response for email {email.id}: {response_str[:300]}")
                        return '2'
                  
                    print(f"call_ollama_api unexpected response {response_str}.")
                    return '-1'

                elif response.status_code == 500:
                    print(f"call_ollama_api error 500. Retrying in {backoff_factor * (2 ** attempt)} seconds")
                else:
                    print(f"call_ollama_api unexpected response {response.status_code}. Retrying in {backoff_factor * (2 ** attempt)} seconds")
            except RequestException as e:
                print(f"Error processing email {email.id}: {e}. Retrying in {backoff_factor * (2 ** attempt)} seconds")
                time.sleep(backoff_factor * (2 ** attempt))
    return '-1'

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

    log_entry = f"Generated zip file and uploaded to S3."
    update_log_entry(user_id, account_id, log_entry, status='finished')

    result = Result.query.filter_by(user_id=user_id, account_id=account_id).first()
    if result:
        result.zip_password = zip_password
        result.file_url = presigned_url
        result.name = zip_filename
        db.session.commit()

    os.remove(mbox_path)
    os.remove(zip_path)

    return zip_filename, presigned_url
