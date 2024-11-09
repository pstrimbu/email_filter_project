from flask import current_app
from .models import Email, EmailAddress, Filter, AIPrompt, Result, EmailAccount
from .extensions import db
import mailbox
import tempfile
from datetime import datetime
import os
import boto3
from io import BytesIO
from dateutil.relativedelta import relativedelta
from sqlalchemy import text
from sqlalchemy import or_, func
from .aws import request_spot_instance, update_last_interaction, check_status, terminate_instance
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from requests.exceptions import RequestException
from email_filter.logger import update_log_entry
from email_filter.aws import delete_file

# Ollama API details
OLLAMA_API_KEY = "_p0fhuNaCq9H8tS8b5OxtLE5VGieqFY4IrMNp9UUFPc"
HEADERS = {"x-api-key": OLLAMA_API_KEY, "Content-Type": "application/json"}


def process_email_results(user_id, account_id):
    global OLLAMA_API_URL
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
        delete_file(user_id, account_id)

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
        # Process emails through different stages
        process_email_addresses(user_id, account_id)
    except Exception as e:
        log_entry = f"Error processing email addresses: {e}"
        update_log_entry(user_id, account_id, log_entry, status='error')
        return

    ignored = db.session.query(Email).filter_by(user_id=user_id, account_id=account_id, action='ignore').count()
    log_entry = f"Remaining: {ignored}"
    update_log_entry(user_id, account_id, log_entry)

    try:
        process_filters(user_id, account_id)
    except Exception as e:
        log_entry = f"Error processing filters: {e}"
        update_log_entry(user_id, account_id, log_entry, status='error')
        return

    ignored = db.session.query(Email).filter_by(user_id=user_id, account_id=account_id, action='ignore').count()
    log_entry = f"Remaining: {ignored}"
    update_log_entry(user_id, account_id, log_entry)

    try:
        process_prompts(user_id, account_id)
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
        terminate_instance()
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

def process_prompts(user_id, account_id):
    # Register with AWS for a spot instance and get the IP address
    try:
        public_ip = request_spot_instance(user_id, account_id)
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
    
    # Check if ollama_api_url is not null
    if not ollama_api_url:
        log_entry = "OLLAMA_API_URL is null. Cannot process prompts."
        update_log_entry(user_id, account_id, log_entry, status='error')
        return

    # Fetch emails with action 'ignore'
    emails = db.session.query(Email).filter_by(user_id=user_id, account_id=account_id, action='ignore').all()

    included = 0
    excluded = 0
    ignored = 0

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
                    email.action = 'include' if response else 'exclude'
                except Exception as e:
                    print(f"Error processing email {email.id}: {e}")
                    email.action = 'ignore'

                if email.action == 'include':
                    included += 1
                elif email.action == 'exclude':
                    excluded += 1
                else:
                    ignored += 1

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
    log_entry = f"Prompts results - Included: {included}, Excluded: {excluded}"
    update_log_entry(user_id, account_id, log_entry)

def call_ollama_api(prompt_text, email, ollama_api_url, user_id, account_id):
    # Check if the API URL is not null
    if not ollama_api_url:
        print(f"[ERROR] OLLAMA_API_URL is null for email ID {email.id}")
        return False
    
    # Start timing the API call
    start_time = time.time()

    update_last_interaction()

    # Prepare the system prompt
    MAX_LENGTH = 5000  # Set a limit for the input length
    email_text = email.text_content  # Use the correct attribute name

    # Truncate the email content if necessary
    email_text = email_text[:MAX_LENGTH] if email_text else ""

    system_prompt = (
        f"Evaluate the given email content and determine if it explicitly discusses details relevant to the topic provided. "
        f"If the email content explicitly discusses the topic, respond with exactly '1'. If it does not, respond with '0'. "
        f"**Respond with only the single digit '0' or '1' ONLY. Provide no other preamble, text, explanation, or analysis.** "
        f"The topic to consider is: {prompt_text}. "
        f"The email content is: {email_text}. "
    )

    # Attempt to send the request with retries
    max_retries = 10
    backoff_factor = 0.5
    for attempt in range(max_retries):
        # Ensure a spot instance is available
        instance_is_active = check_status()
        if not instance_is_active:
            log_entry = f"No active instance. Cannot process prompts."
            update_log_entry(user_id, account_id, log_entry, status='error')
            return False
        try:
            response = requests.post(ollama_api_url, headers=HEADERS, 
                json={"query": system_prompt, "model": "llama3.2:latest"})
            if response.status_code == 200:
                response_json = response.json()
                response_value = response_json.get('response', '0')

                try:
                    content = int(response_value)
                    # print(f"[DEBUG] response : {response_value} : email {email.text_content[:300]}")
                except ValueError:
                    print(f"[ERROR] Received non-numeric response: {response_value[:200]} for email ID {email.id}")
                    content = 0
                break  # Exit the loop if the request is successful
            else:
                # print(f"[DEBUG] response code : {response.status_code} : email ID {email.id}")
                content = 0  # Treat errors as no match
        except RequestException as e:
            print(f"Error processing email {email.id}: {e}. waiting {backoff_factor * (2 ** attempt)} seconds before retrying")
            # Wait before retrying
            time.sleep(backoff_factor * (2 ** attempt))
    else:
        # If all retries fail, set content to 0
        content = 0

    # End timing the API call
    end_time = time.time()
    api_call_time = end_time - start_time
    # print(f"Ollama API call time for email {email.id}: {api_call_time:.2f}s")

    return content == 1

def generate_files(user_id, account_id):
    # Fetch the email account to get the email address and date range
    email_account = EmailAccount.query.get(account_id)
    if not email_account:
        raise ValueError(f"No EmailAccount found for account_id: {account_id}")

    email_address = email_account.email
    date_range = f"{email_account.start_date.strftime('%Y%m%d')}-{email_account.end_date.strftime('%Y%m%d')}"
    timestamp = datetime.now().strftime('%Y%m%d%H%M')
    mbox_filename = f"{email_address}-{date_range}-{timestamp}.mbox"

    # Initialize S3 client
    s3_client = boto3.client('s3')
    bucket_name = 'mailmatch'

    # Create a temporary file to store the mbox data
    with tempfile.NamedTemporaryFile(delete=False) as temp_mbox_file:
        mbox_path = temp_mbox_file.name

    # Open the mbox file for writing
    mbox = mailbox.mbox(mbox_path, create=True)
    try:
        # Fetch emails in batches of 100
        query = db.session.query(Email).filter_by(user_id=user_id, account_id=account_id, action='include')
        for email in query.yield_per(100):
            mbox.add(mailbox.mboxMessage(email.raw_data))
    finally:
        mbox.close()

    # Upload the mbox file to S3
    s3_client.upload_file(mbox_path, bucket_name, mbox_filename)

    # Generate a pre-signed URL valid for 1 month (30 days)
    presigned_url = s3_client.generate_presigned_url('get_object',
                                                     Params={'Bucket': bucket_name, 'Key': mbox_filename},
                                                     ExpiresIn=2592000)  # 30 days in seconds

    # Update the result with the S3 link
    log_entry = f"Generated mbox file and uploaded to S3. Download link: {presigned_url}"
    update_log_entry(user_id, account_id, log_entry, status='finished', file_url=presigned_url, name=mbox_filename)

    # Clean up the temporary mbox file
    os.remove(mbox_path)

    return mbox_filename, presigned_url
