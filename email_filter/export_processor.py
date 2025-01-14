# Standard Library Imports
import os
import time
import json
import logging
import secrets
from datetime import datetime
from dotenv import load_dotenv
import tempfile
import mailbox

# Third-Party Imports
import pyminizip
import requests
from sqlalchemy.exc import SQLAlchemyError

# Flask Imports
from flask import current_app

# Local Application Imports
from .models import Email, EmailAddress, Filter, AIPrompt, Result, EmailAccount
from .extensions import db
from .aws import (
    SpotInstanceManager,
    InstanceManager,
    delete_file_from_s3,
    upload_file_to_s3,
    generate_presigned_url,
)
from email_filter.logger import update_log_entry
from email_filter.globals import processing_status


# Load environment variables from .env file
load_dotenv()

# Debug flag
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"

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

LOG_INTERVAL = int(os.getenv("LOG_INTERVAL", 30))

processed = 0
included = 0
excluded = 0
ignored = 0
refused = 0
errored = 0
unexpected = 0
total_emails = 0
tasks = []
start_time = time.time()
last_log_time = start_time

# Use the global logger
logger = logging.getLogger(__name__)


def log_debug(user_id, email_account_id, message):
    if DEBUG_MODE:
        update_log_entry(user_id, email_account_id, f"DEBUG: {message}")

def stop(user_id, email_account_id):
    log_debug(user_id, email_account_id, "Entering stop function")

    processing_status[(user_id, email_account_id)] = 'stopping'
    
    try:
        # Retry loop with backoff
        max_retries = 10
        backoff_factor = 0.5
        for attempt in range(max_retries):
            for task in tasks:
                task.cancel()
                tasks.remove(task)
            if len(tasks) == 0:
                update_log_entry(user_id, email_account_id, "Stopped by user request.", status='finished')
                break
            else:
                update_log_entry(user_id, email_account_id, f"Stop request received. Waiting for tasks to complete. Attempt {attempt + 1}/{max_retries}", status='stopping')
                time.sleep(backoff_factor * (2 ** attempt))  # Exponential backoff

        if len(tasks) > 0:
            update_log_entry(user_id, email_account_id, "Stop request received. Waiting for tasks to complete.", status='stopping')
            
    except Exception as e:
        logger.error(f"Exception in stop: {e}")

async def process_emails(user_id, email_account_id):
    log_debug(user_id, email_account_id, f"Entering process_emails: {user_id}, {email_account_id}")
    global processing_status
    try:
        if user_id is None:
            raise ValueError("User ID cannot be None")
        if email_account_id is None:
            raise ValueError("Account ID cannot be None")

        processing_status[(user_id, email_account_id)] = 'running'

        # Delete previous results
        preprocess_cleanup(user_id, email_account_id)

        update_log_entry(user_id, email_account_id, 'Processing started', status='started')

        # LOG TOTAL EMAILS
        try:
            # Get the count of all emails for the user and account
            total_emails = db.session.query(Email).filter_by(user_id=user_id, email_account_id=email_account_id).count()
            
            # Initialize log entry
            log_entry = f"Total emails found: {total_emails}"
            update_log_entry(user_id, email_account_id, log_entry, status='processing')
        except Exception as e:
            log_entry = f"Error fetching email count: {e}"
            update_log_entry(user_id, email_account_id, log_entry, status='error')
            return

        process_email_addresses(user_id, email_account_id)

        if processing_status.get((user_id, email_account_id)) == 'stopping' or processing_status.get((user_id, email_account_id)) == 'finished':
            update_log_entry(user_id, email_account_id, "Stopped by user request.", status='finished')
            return {'success': False, 'error': 'stopped by user request'}

        process_filters(user_id, email_account_id)

        if processing_status.get((user_id, email_account_id)) == 'stopping' or processing_status.get((user_id, email_account_id)) == 'finished':
            update_log_entry(user_id, email_account_id, "Stopped by user request.", status='finished')
            return {'success': False, 'error': 'stopped by user request'}

        await process_prompts(user_id, email_account_id)

        if processing_status.get((user_id, email_account_id)) == 'stopping' or processing_status.get((user_id, email_account_id)) == 'finished':
            update_log_entry(user_id, email_account_id, "Stopped by user request.", status='finished')
            return {'success': False, 'error': 'stopped by user request'}
        
        try:
            # Generate files and get the mbox filename and presigned URL
            mbox_filename, presigned_url = generate_files(user_id, email_account_id)
            result = Result.query.filter_by(user_id=user_id, email_account_id=email_account_id).first()
            if result:
                result.user_id = user_id
                result.email_account_id = email_account_id
                result.name = mbox_filename
                result.file_url = presigned_url
                result.status = 'finished'
                result.log_entry = result.log_entry + '\nProcessing finished'
                db.session.add(result)
            db.session.commit()
        except Exception as e:
            log_entry = f"Error generating files: {e}"
            update_log_entry(user_id, email_account_id, log_entry, status='error')
            return
    except Exception as e:
        log_entry = f"Error processing emails: {e}"
        update_log_entry(user_id, email_account_id, log_entry, status='error')
        return
    finally:
        processing_status[(user_id, email_account_id)] = 'finished'
        update_log_entry(user_id, email_account_id, "Finished processing.", status='finished')
        log_debug(user_id, email_account_id, "Exiting process_emails function")


def preprocess_cleanup(user_id, email_account_id):
    log_debug(user_id, email_account_id, "Entering preprocess_cleanup function")
    try:
        # Set all emails to 'ignore' before processing
        db.session.query(Email).filter_by(user_id=user_id, email_account_id=email_account_id).update({'action': 'ignore'}, synchronize_session=False)
        db.session.commit()
    except Exception as e:
        log_entry = f"Error setting emails to ignore: {e}"
        update_log_entry(user_id, email_account_id, log_entry, status='error')
        return

    try:
        # Delete existing Result entry for the user and account
        existing_results = Result.query.filter_by(user_id=user_id, email_account_id=email_account_id)
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
        update_log_entry(user_id, email_account_id, log_entry, status='error')
        return
    finally:
        log_debug(user_id, email_account_id, "Exiting preprocess_cleanup function")


def process_email_addresses(user_id, email_account_id):
    log_debug(user_id, email_account_id, "Entering process_email_addresses function")
    
    # Check if there are any email addresses set to include/exclude
    has_email_addresses = db.session.query(EmailAddress).filter(
        EmailAddress.user_id == user_id,
        EmailAddress.action.in_(['include', 'exclude'])
    ).count() > 0

    if not has_email_addresses:
        log_debug(user_id, email_account_id, "No email addresses with include/exclude action found")
        return
    
    total_included = 0
    total_excluded = 0

    # Subquery for emails to include
    include_subquery = db.session.query(Email.id).join(
        EmailAddress, Email.sender_id == EmailAddress.id
    ).filter(
        EmailAddress.user_id == user_id,
        EmailAddress.email_account_id == email_account_id,
        EmailAddress.action == 'include',
        Email.action == 'ignore'
    ).union(
        db.session.query(Email.id).join(
            Email.receivers
        ).filter(
            EmailAddress.user_id == user_id,
            EmailAddress.email_account_id == email_account_id,
            EmailAddress.action == 'include',
            Email.action == 'ignore'
        )
    ).subquery()

    # Update emails to include
    include_emails = db.session.query(Email).filter(Email.id.in_(include_subquery)).all()
    for email in include_emails:
        email.action = 'include'
        total_included += 1

    # Subquery for emails to exclude
    exclude_subquery = db.session.query(Email.id).join(
        EmailAddress, Email.sender_id == EmailAddress.id
    ).filter(
        EmailAddress.user_id == user_id,
        EmailAddress.email_account_id == email_account_id,
        EmailAddress.action == 'exclude',
        Email.action == 'ignore'
    ).union(
        db.session.query(Email.id).join(
            Email.receivers
        ).filter(
            EmailAddress.user_id == user_id,
            EmailAddress.email_account_id == email_account_id,
            EmailAddress.action == 'exclude',
            Email.action == 'ignore'
        )
    ).subquery()

    # Update emails to exclude
    exclude_emails = db.session.query(Email).filter(Email.id.in_(exclude_subquery)).all()
    for email in exclude_emails:
        email.action = 'exclude'
        total_excluded += 1

    # Commit the changes
    db.session.commit()

    ignored = db.session.query(Email).filter_by(
        user_id=user_id, 
        email_account_id=email_account_id, 
        action='ignore'
    ).count()

    # Log the results
    log_entry = f"Email address filter results - Included: {total_included}, Excluded: {total_excluded}, Remaining: {ignored}"
    update_log_entry(user_id, email_account_id, log_entry)

    log_debug(user_id, email_account_id, "Exiting process_email_addresses function")


def process_filters(user_id, email_account_id):
    log_debug(user_id, email_account_id, "Entering process_filters function")
    # Check if there are any filters defined
    has_filters = db.session.query(Filter).filter_by(user_id=user_id, email_account_id=email_account_id).count() > 0
    if not has_filters:
        return

    # Fetch all filters for the user and account
    filters = Filter.query.filter_by(user_id=user_id, email_account_id=email_account_id).order_by(Filter.order).all()

    total_included = 0
    total_excluded = 0

    for filter_obj in filters:
        if filter_obj.action in ['include', 'exclude']:
            # Update emails to 'include' or 'exclude' where the filter matches and the action is 'ignore'
            result = db.session.query(Email).filter(
                Email.user_id == user_id,
                Email.email_account_id == email_account_id,
                Email.action == 'ignore',
                Email.text_content.contains(filter_obj.filter)
            ).update({'action': filter_obj.action}, synchronize_session=False)

            # Increment the appropriate counter based on the filter action
            if filter_obj.action == 'include':
                total_included += result
            elif filter_obj.action == 'exclude':
                total_excluded += result

    # Commit the changes
    db.session.commit()

    ignored = db.session.query(Email).filter_by(user_id=user_id, email_account_id=email_account_id, action='ignore').count()

    log_entry = f"Filter results - Included: {total_included}, Excluded: {total_excluded}, Remaining: {ignored}"
    update_log_entry(user_id, email_account_id, log_entry)

    log_debug(user_id, email_account_id, "Exiting process_filters function")


async def process_prompts(user_id, email_account_id):
    log_debug(user_id, email_account_id, "Entering process_prompts function")
    global processed, processing_status, included, excluded, ignored, refused, errored, unexpected, total_emails, tasks, start_time, last_log_time

    processed = 0
    included = 0
    excluded = 0
    ignored = 0
    refused = 0
    errored = 0
    unexpected = 0
    total_emails = 0
    tasks = []
    start_time = time.time()
    last_log_time = start_time

    # Check if there are any prompts defined
    has_prompts = db.session.query(AIPrompt).filter_by(user_id=user_id, email_account_id=email_account_id).count() > 0
    if not has_prompts:
        return

    try:
        # Fetch emails with action 'ignore' in batches
        batch_size = int(os.getenv("EMAIL_BATCH_SIZE", 100))
        offset = 0

        ai_prompts = AIPrompt.query.filter_by(user_id=user_id, email_account_id=email_account_id)\
                                   .order_by(AIPrompt.order).all()

        total_emails = db.session.query(Email).filter_by(user_id=user_id, email_account_id=email_account_id, action='ignore').count()

        # Limit how many tasks run concurrently
        max_concurrency = 10

        while True:
            # Check stop/finish condition
            if processing_status.get((user_id, email_account_id)) in ['stopping', 'finished']:
                return False

            log_debug(user_id, email_account_id, f"Processing batch {offset}, processing_status: {processing_status.get((user_id, email_account_id))}")

            # Use offset + limit so we don't re-fetch the same batch
            emails = db.session.query(Email)\
                               .filter_by(user_id=user_id, email_account_id=email_account_id, action='ignore')\
                               .offset(offset)\
                               .limit(batch_size)\
                               .all()

            if not emails:
                break

            # Schedule tasks for each email/prompt combination
            for prompt in ai_prompts:
                for email in emails:
                    # Check stop condition before creating new tasks
                    if processing_status.get((user_id, email_account_id)) == 'stopping':
                        return {'success': False, 'error': 'stopped by user request'}

                    # If we're at capacity, wait for at least one task to finish
                    while len(tasks) >= max_concurrency:
                        remaining = total_emails - processed
                        current_time = time.time()

                        # Time-based logging
                        if current_time - last_log_time >= LOG_INTERVAL:
                            elapsed_time = current_time - start_time
                            if processed > 0:
                                average_time = elapsed_time / processed
                                projected_remaining_time = average_time * remaining
                                log_entry = (
                                    f"Tasks {len(tasks)} - Processed {processed}/{total_emails} emails - "
                                    f"Included: {included}, Excluded: {excluded}, Ignored: {ignored}, Refused: {refused}, "
                                    f"Errored: {errored}, Unexpected: {unexpected}, Remaining: {remaining}, "
                                    f"Elapsed: {elapsed_time:.2f}s, "
                                    f"Projected: {projected_remaining_time:.2f}s"
                                )
                            else:
                                log_entry = (
                                    f"Tasks {len(tasks)} - Processed {processed}/{total_emails} emails - "
                                    f"Included: {included}, Excluded: {excluded}, Ignored: {ignored}, Refused: {refused}, "
                                    f"Errored: {errored}, Unexpected: {unexpected}, Remaining: {remaining} "
                                    f"Elapsed: {elapsed_time:.2f}s "
                                )
                            update_log_entry(user_id, email_account_id, log_entry)
                            last_log_time = current_time

                        # Wait for at least one of the current tasks to complete
                        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                        for d in done:
                            tasks.remove(d)

                    # Create and add the new task
                    task = asyncio.create_task(
                        call_ollama_api(prompt.prompt_text, email, user_id, email_account_id, prompt.action)
                    )
                    tasks.append(task)

            # After scheduling tasks for this batch, wait until they all finish (or time out)
            start_wait_time = time.time()
            while len(tasks) > 0:
                remaining = total_emails - processed
                current_time = time.time()

                # Time-based logging
                if current_time - last_log_time >= LOG_INTERVAL:
                    elapsed_time = current_time - start_time
                    if processed > 0:
                        average_time = elapsed_time / processed
                        projected_remaining_time = average_time * remaining
                        log_entry = (
                            f"Tasks {len(tasks)} - Processed {processed}/{total_emails} emails - "
                            f"Included: {included}, Excluded: {excluded}, Ignored: {ignored}, Refused: {refused}, "
                            f"Errored: {errored}, Unexpected: {unexpected}, Remaining: {remaining}, "
                            f"Elapsed: {elapsed_time:.2f}s, "
                            f"Projected: {projected_remaining_time:.2f}s"
                        )
                    else:
                        log_entry = (
                            f"Tasks {len(tasks)} - Processed {processed}/{total_emails} emails - "
                            f"Included: {included}, Excluded: {excluded}, Ignored: {ignored}, Refused: {refused}, "
                            f"Errored: {errored}, Unexpected: {unexpected}, Remaining: {remaining} "
                            f"Elapsed: {elapsed_time:.2f}s "
                        )
                    update_log_entry(user_id, email_account_id, log_entry)
                    last_log_time = current_time

                for task in list(tasks):
                    if task.done():
                        tasks.remove(task)
                    elif time.time() - start_wait_time > 60:  # check if wait time exceeds 60 seconds
                        task.close()
                        tasks.remove(task)
                        logger.warning("Task exceeded 60 seconds and was closed.")

                await asyncio.sleep(1)

            # Commit any changes after finishing the batch
            db.session.commit()
            offset += batch_size

        log_entry = (
            f"Prompts results - Included: {included}, Excluded: {excluded}, Refused: {refused}, "
            f"Errored: {errored}, Unexpected: {unexpected}"
        )
        update_log_entry(user_id, email_account_id, log_entry)
    finally:
        # Notify AWS that the instance is no longer in use
        try:
            manager.terminate_instance(user_id)
        except Exception as e:
            log_entry = f"Error terminating instance: {e}"
            update_log_entry(user_id, email_account_id, log_entry, status='error')
        log_debug(user_id, email_account_id, "Exiting process_prompts function")


async def call_ollama_api(prompt_text, email, user_id, email_account_id, action):
    global included, excluded, ignored, refused, errored, unexpected, last_log_time, total_emails, processed    

    MAX_LENGTH = int(os.getenv("EMAIL_MAX_LENGTH", 5000))
    max_retries = 20
    backoff_factor = 0.5

    for attempt in range(max_retries):
        try:
            # Initialize response to None
            response = None

            # tell the manager we are still using the instance
            manager.update_last_interaction()

            if processing_status.get((user_id, email_account_id)) == 'stopping':
                return -1

            public_ip = None
            # Register with AWS for a spot instance and get the IP address
            try:
                public_ip = manager.get_public_ip()
                log_debug(user_id, email_account_id, f"Got public IP: {public_ip}")
                if not public_ip:
                    update_log_entry(user_id, email_account_id, "No AI Server found, requesting instance")
                    public_ip = await manager.request_instance(user_id, email_account_id)
                    if public_ip:
                        update_log_entry(user_id, email_account_id, f"AI Server found. IP: {public_ip}")
                    else:
                        log_debug(user_id, email_account_id, f"No public IP found, retrying: {attempt}")
                        time.sleep(backoff_factor * (2 ** attempt))
                        continue  # Retry the loop
            except Exception as e:
                log_entry = f"Error requesting instance: {e}"
                update_log_entry(user_id, email_account_id, log_entry, status='error')
                time.sleep(backoff_factor * (2 ** attempt))
                continue  # Retry the loop

            email_text = email.text_content
            email_text = email_text[:MAX_LENGTH] if email_text else ""

            system_prompt_template = os.getenv("SYSTEM_PROMPT", "Default prompt text")
            system_prompt = system_prompt_template.format(prompt_text=prompt_text, email_text=email_text)

            try:
                # Add logging before and after network calls
                log_debug(user_id, email_account_id, "Preparing to make network request")
                try:
                    response = requests.post(f"http://{public_ip}:5000/api", headers=HEADERS, json={"query": system_prompt, "model": OLLAMA_MODEL}, timeout=30)
                    log_debug(user_id, email_account_id, f"Received response: {response.status_code}")
                except requests.exceptions.Timeout:
                    errored += 1
                    log_debug(user_id, email_account_id, "Request timed out")
                except requests.exceptions.ConnectionError:
                    errored += 1
                    log_debug(user_id, email_account_id, "Connection error occurred")
                except Exception as e:
                    errored += 1
                    log_debug(user_id, email_account_id, f"Exception occurred: {e}")

                response_str = None
                if response.status_code == 200:
                    log_debug(user_id, email_account_id, f"Received response: {response.status_code}")
                    try:
                        response_json = response.json()
                        response_str = response_json.get('response', response_json)
                    except Exception as e:
                        logger.error(f"call_ollama_api error {e}. response: {response.text}")

                    if response_str is not None and (response_str == '0' or response_str == '1'):
                        if response_str == '1' and action == 'include':
                            included += 1
                            email.action = 'include' 
                            db.session.commit()
                        elif response_str == '1' and action == 'exclude':
                            excluded += 1
                            email.action = 'exclude'
                            db.session.commit()
                        else:
                            ignored += 1
                        return int(response_str)

                    if isinstance(response_str, str):
                        # see if the response_str contains json, ex: '{ "response": "0" }'
                        try:
                            response_str_json = json.loads(response_str)
                            response_str = response_str_json.get('response', response_str)
                            response_str = response_str.split('\n')[0]

                            if response_str is not None and (response_str == '0' or response_str == '1'):
                                if response_str == '1' and action == 'include':
                                    included += 1
                                    email.action = 'include'
                                    db.session.commit()
                                elif response_str == '1' and action == 'exclude':
                                    excluded += 1
                                    email.action = 'exclude'
                                    db.session.commit()
                                else:
                                    ignored += 1
                                return int(response_str)
                        except json.JSONDecodeError:
                            pass  # If parsing fails, use response_str as is

                    if isinstance(response_str, str) and "can't" in response_str:
                        refused += 1
                        # logger.info(f"Received 'can't' response for email {email.id}: {response_str[:300]}")
                        return 2

                    logger.debug(f"Unexpected response: {response_str}.")
                    unexpected += 1
                    return -2

                elif response.status_code == 500:
                    log_debug(user_id, email_account_id, f"Received 500 response. Retrying in {backoff_factor * (2 ** attempt)} seconds")
                else:
                    log_debug(user_id, email_account_id, f"Received unexpected response {response.status_code}. Retrying in {backoff_factor * (2 ** attempt)} seconds")
            except Exception as e:
                # tell the instance manager that the instance is not ready
                # manager.set_public_ip(None)
                log_debug(user_id, email_account_id, f"Error processing email {email.id}: {e}. Retrying in {backoff_factor * (2 ** attempt)} seconds")
                time.sleep(backoff_factor * (2 ** attempt))
        finally:
            processed += 1        

    log_debug(user_id, email_account_id, f"call_ollama_api: returning -2")
    errored += 1
    return -2


def generate_files(user_id, email_account_id):
    log_debug(user_id, email_account_id, "Entering generate_files function")
    """Generates and uploads email files."""
    try:
        # Fetch email account details
        email_account = EmailAccount.query.get(email_account_id)
        if not email_account:
            raise ValueError(f"No EmailAccount found for email_account_id: {email_account_id}")

        email_address = email_account.email_address
        timestamp = datetime.now().strftime('%Y%m%d%H%M')

        if email_account.start_date and email_account.end_date:
            date_range = f"{email_account.start_date.strftime('%Y%m%d')}-{email_account.end_date.strftime('%Y%m%d')}"
            mbox_filename = f"{email_address}-{date_range}-{timestamp}.mbox"
            zip_filename = f"{email_address}-{date_range}-{timestamp}.zip"
        else:
            mbox_filename = f"{email_address}-{timestamp}.mbox"
            zip_filename = f"{email_address}-{timestamp}.zip"

        zip_password = secrets.token_urlsafe(12)
        mbox_path = os.path.join(tempfile.gettempdir(), mbox_filename)
        mbox = mailbox.mbox(mbox_path, create=True)

        try:
            query = db.session.query(Email).filter_by(
                user_id=user_id, email_account_id=email_account_id, action='include'
            )
            for email in query.yield_per(100):
                mbox.add(mailbox.mboxMessage(email.raw_data))
        finally:
            mbox.close()

        # Create a zip file with password protection
        with tempfile.NamedTemporaryFile(delete=False) as temp_zip_file:
            zip_path = temp_zip_file.name
        pyminizip.compress(mbox_path, None, zip_path, zip_password, 5)

        # Upload to S3 and generate presigned URL
        bucket_name = os.getenv("S3_BUCKET_NAME")
        upload_success = upload_file_to_s3(zip_path, bucket_name, zip_filename)
        if not upload_success:
            raise RuntimeError("Failed to upload file to S3.")

        presigned_url = generate_presigned_url('mailmatch', zip_filename)

        # Update log and database
        update_log_entry(user_id, email_account_id, "Generated zip file and uploaded to S3.", status='finished')

        result = Result.query.filter_by(user_id=user_id, email_account_id=email_account_id).first()
        if result:
            result.zip_password = zip_password
            result.file_url = presigned_url
            result.name = zip_filename
            db.session.commit()

        return zip_filename, presigned_url

    except (ValueError, SQLAlchemyError, RuntimeError) as e:
        log_debug(user_id, email_account_id, f"Error in generate_files: {str(e)}")
        raise
    finally:
        # Clean up temporary files
        try:
            if os.path.exists(mbox_path):
                os.remove(mbox_path)
            if os.path.exists(zip_path):
                os.remove(zip_path)
        except Exception as cleanup_error:
            log_debug(user_id, email_account_id, f"Error cleaning up temporary files: {str(cleanup_error)}")
