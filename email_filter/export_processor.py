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
import requests
from requests.exceptions import RequestException
import random
import string
import pyminizip
from dotenv import load_dotenv
from email_filter.globals import processing_status
import asyncio
import json

# Load environment variables from .env file
load_dotenv()

# Ollama API details
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL")
HEADERS = {"x-api-key": OLLAMA_API_KEY, "Content-Type": "application/json"}

# Initialize the appropriate instance manager based on PROCESSOR_TYPE
PROCESSOR_TYPE = os.getenv("PROCESSOR_TYPE", "spot")
if PROCESSOR_TYPE == "spot":
    manager = SpotInstanceManager()
else:
    manager = InstanceManager()

tasks = []
included = 0
excluded = 0
refused = 0
errored = 0
unexpected = 0
processed = 0
start_time = time.time()
last_log_time = start_time
total_emails = 0
log_interval = int(os.getenv("LOG_INTERVAL", 30))


def stop(user_id, account_id):
    for task in tasks:
        task.close()
        tasks.remove(task)

    if len(tasks) == 0:
        update_log_entry(user_id, account_id, "Stopped by user request.", status='finished')
    else:
        update_log_entry(user_id, account_id, "Stop request received.", status='stopping')
        processing_status[(user_id, account_id)] = 'stopping'


async def process_emails(user_id, account_id):
    global processing_status
    try:
        if user_id is None:
            raise ValueError("User ID cannot be None")
        if account_id is None:
            raise ValueError("Account ID cannot be None")

        processing_status[(user_id, account_id)] = 'running'

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
            update_log_entry(user_id, account_id, "Stopped by user request.", status='finished')
            return {'success': False, 'error': 'stopped by user request'}

        process_filters(user_id, account_id)

        if processing_status.get((user_id, account_id)) == 'stopping':
            update_log_entry(user_id, account_id, "Stopped by user request.", status='finished')
            return {'success': False, 'error': 'stopped by user request'}

        await process_prompts(user_id, account_id)

        if processing_status.get((user_id, account_id)) == 'stopping':
            update_log_entry(user_id, account_id, "Stopped by user request.", status='finished')
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
        update_log_entry(user_id, account_id, "Finished processing.", status='finished')


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
    global processing_status, included, excluded, refused, errored, unexpected, total_emails, tasks
    # Check if there are any prompts defined
    has_prompts = db.session.query(AIPrompt).filter_by(user_id=user_id, account_id=account_id).count() > 0
    if not has_prompts:
        return

    try:
        # Fetch emails with action 'ignore' in batches
        batch_size = int(os.getenv("EMAIL_BATCH_SIZE", 100))

        offset = 0

        ai_prompts = AIPrompt.query.filter_by(user_id=user_id, account_id=account_id).order_by(AIPrompt.order).all()

        total_emails = db.session.query(Email).filter_by(user_id=user_id, account_id=account_id, action='ignore').count()

        while True:
            print(f"Processing batch {offset}")
            emails = db.session.query(Email).filter_by(user_id=user_id, account_id=account_id, action='ignore').limit(batch_size).all()
            if not emails:
                break
                   
            # Use asyncio.gather to process emails concurrently
            for prompt in ai_prompts:
                for email in emails:
                    # Check stop condition before submitting new tasks
                    if processing_status.get((user_id, account_id)) == 'stopping':
                        return {'success': False, 'error': 'stopped by user request'}
                    
                    # task = call_ollama_api(prompt.prompt_text, email, user_id, account_id)
                    task = asyncio.create_task(call_ollama_api(prompt.prompt_text, email, user_id, account_id))
                    
                    tasks.append(task)

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Clear tasks list for each batch
            start_wait_time = time.time()  # Capture the current time
            while len(tasks) > 0:
                for task in tasks:
                    if task.done():
                        tasks.remove(task)
                    elif time.time() - start_wait_time > 60:  # Check if wait time exceeds 60 seconds
                        task.close()
                        tasks.remove(task)
                        print(f"Task exceeded 60 seconds and was closed.")
                await asyncio.sleep(1)
            
            for response, email in zip(results, emails):
                # Check stop condition within the loop
                if processing_status.get((user_id, account_id)) == 'stopping':
                    return {'success': False, 'error': 'stopped by user request'}

                try:
                    match response:
                        case 1: 
                            email.action = 'include'
                            included += 1
                            print(f"Included: {included}")
                        case 0: 
                            email.action = 'exclude'
                            excluded += 1
                            print(f"Excluded: {excluded}")
                        case 2: # received "can't process" response
                            email.action = 'exclude'
                            refused += 1
                            print(f"Refused: {refused}")
                        case -1: # received error response
                            email.action = 'exclude'
                            errored += 1
                            print(f"Errored: {errored}")
                        case _: # unexpected response
                            email.action = 'exclude'
                            unexpected += 1
                            print(f"Unexpected: {unexpected}")
                except Exception as e:
                    print(f"Error processing email {email.id}: {e}")
                    email.action = 'ignore'

            db.session.commit()
            offset += batch_size

        log_entry = f"Prompts results - Included: {included}, Excluded: {excluded}, Refused: {refused}, Errored: {errored}, Unexpected: {unexpected}"
        update_log_entry(user_id, account_id, log_entry)
    finally:
        # Notify AWS that the instance is no longer in use
        try:
            manager.terminate_instance(user_id)
        except Exception as e:
            log_entry = f"Error terminating instance: {e}"
            update_log_entry(user_id, account_id, log_entry, status='error')


async def call_ollama_api(prompt_text, email, user_id, account_id):
    global processing_status, included, excluded, refused, errored, unexpected, last_log_time, start_time, total_emails
    """Call Ollama API with retries."""

    max_retries = 20
    backoff_factor = 0.5

    for attempt in range(max_retries):
        try:
            if processing_status.get((user_id, account_id)) == 'stopping':
                return -1

            public_ip = None
            # Register with AWS for a spot instance and get the IP address
            try:
                public_ip = manager.get_public_ip()
                manager.update_last_interaction()
                if not public_ip:
                    public_ip = await manager.request_instance(user_id, account_id)
            except Exception as e:
                log_entry = f"Error requesting instance: {e}"
                update_log_entry(user_id, account_id, log_entry, status='error')
                time.sleep(backoff_factor * (2 ** attempt))
                continue  # Retry the loop

            if not public_ip:
                time.sleep(backoff_factor * (2 ** attempt))
                continue  # Retry the loop

            ollama_api_url = f"http://{public_ip}:5000/api"

            if not ollama_api_url:
                print(f"[ERROR] {ollama_api_url} is null for email ID {email.id}")
                return -1

            email_text = email.text_content
            MAX_LENGTH = int(os.getenv("EMAIL_MAX_LENGTH", 5000))
            email_text = email_text[:MAX_LENGTH] if email_text else ""

            system_prompt_template = os.getenv("SYSTEM_PROMPT", "Default prompt text")
            system_prompt = system_prompt_template.format(prompt_text=prompt_text, email_text=email_text)

            try:
                response = requests.post(ollama_api_url, headers=HEADERS, json={"query": system_prompt, "model": OLLAMA_MODEL})
                response_str = None
                if response.status_code == 200:
                    try:
                        response_json = response.json()
                        response_str = response_json.get('response', response_json)
                    except Exception as e:
                        print(f"call_ollama_api error {e}. response: {response.text}")

                    if response_str is not None and (response_str == '0' or response_str == '1'):
                        return int(response_str)

                    if isinstance(response_str, str):
                        # see if the response_str contains json, ex: '{ "response": "0" }'
                        try:
                            response_str_json = json.loads(response_str)
                            response_str = response_str_json.get('response', response_str)

                            if response_str is not None and (response_str == '0' or response_str == '1'):
                                return int(response_str)
                        except json.JSONDecodeError:
                            pass  # If parsing fails, use response_str as is

                    if isinstance(response_str, str) and "can't" in response_str:
                        print(f"call_ollama_api received 'can't' response for email {email.id}: {response_str[:300]}")
                        return 2

                    print(f"call_ollama_api unexpected response {response_str}.")
                    return -2

                elif response.status_code == 500:
                    print(f"call_ollama_api error 500. Retrying in {backoff_factor * (2 ** attempt)} seconds")
                else:
                    print(f"call_ollama_api unexpected response {response.status_code}. Retrying in {backoff_factor * (2 ** attempt)} seconds")
            except Exception as e:
                # tell the instance manager that the instance is not ready
                manager.set_public_ip(None)
                print(f"Error processing email {email.id}: {e}. Retrying in {backoff_factor * (2 ** attempt)} seconds")
                time.sleep(backoff_factor * (2 ** attempt))
        finally:
            processed = included + excluded          
            remaining = total_emails - processed

            try:
                # Time-based logging
                current_time = time.time()
                if current_time - last_log_time >= log_interval:
                    elapsed_time = current_time - start_time
                    if processed > 0:
                        average_time = elapsed_time / processed
                        projected_remaining_time = average_time * remaining

                        log_entry = (f"Processed {processed}/{total_emails} emails - "
                                    f"Included: {included}, Excluded: {excluded}, Refused: {refused}, "
                                    f"Errored: {errored}, Unexpected: {unexpected}, Remaining: {remaining}, "
                                    f"Elapsed: {elapsed_time:.2f}s, "
                                    f"Projected: {projected_remaining_time:.2f}s")
                    else:
                        log_entry = (f"Processed {processed}/{total_emails} emails - "
                                    f"Included: {included}, Excluded: {excluded}, Refused: {refused}, "
                                    f"Errored: {errored}, Unexpected: {unexpected}, Remaining: {remaining} "
                                    f"Elapsed: {elapsed_time:.2f}s ")
                    update_log_entry(user_id, account_id, log_entry)
                    last_log_time = current_time
            except Exception as e:
                print(f"Error logging: {e}")
    return -2

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

    update_log_entry(user_id, account_id, "Generated zip file and uploaded to S3.", status='finished')

    result = Result.query.filter_by(user_id=user_id, account_id=account_id).first()
    if result:
        result.zip_password = zip_password
        result.file_url = presigned_url
        result.name = zip_filename
        db.session.commit()

    os.remove(mbox_path)
    os.remove(zip_path)

    return zip_filename, presigned_url
