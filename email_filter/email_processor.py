from .extensions import db
from .models import Email, EmailAddress, EmailFolder, User  # Add Job to the import
from bs4 import BeautifulSoup  # Import BeautifulSoup for HTML parsing
from concurrent.futures import ThreadPoolExecutor, as_completed  # Re-import ThreadPoolExecutor
from datetime import timedelta
from email import message_from_bytes
from email.parser import BytesParser
from email.policy import default
from imaplib import IMAP4_SSL
from poplib import POP3_SSL, POP3
import mailbox
import re
import smtplib
from email.utils import parsedate_to_datetime
from email_filter.globals import scan_status
from email.message import EmailMessage



def extract_human_readable_text(content):
    """
    Extracts and returns only human-readable text from the provided content.
    Handles both EmailMessage objects and HTML strings.
    """
    html_content = None
    plain_text_content = None

    try:
        if isinstance(content, str):
            html_content = content
        elif isinstance(content, EmailMessage):
            for part in content.iter_parts():
                content_type = part.get_content_type()
                if content_type == 'text/html' and html_content is None:
                    html_content = part.get_content()
                elif content_type == 'text/plain' and plain_text_content is None:
                    plain_text_content = part.get_content()
                elif content_type.startswith('multipart/'):
                    # Recursively handle multipart content
                    sub_content = extract_human_readable_text(part)
                    if sub_content:
                        if 'html' in content_type:
                            html_content = sub_content
                        elif 'multipart/alternative' in content_type or 'plain' in content_type:
                            plain_text_content = sub_content

            if html_content is None and hasattr(content, '_payload'):
                if isinstance(content._payload, str):
                    html_content = content._payload
                elif isinstance(content._payload, EmailMessage):
                    html_content = extract_human_readable_text(content._payload)
                elif isinstance(content._payload, list):
                    html_content = ''.join(
                        extract_human_readable_text(msg) for msg in content._payload
                        if isinstance(msg, EmailMessage)
                    )

        content_to_use = html_content if html_content else plain_text_content

        if content_to_use is None:
            # print("Warning: No HTML or plain text content found.")
            return ""

        # Use a more forgiving parser
        soup = BeautifulSoup(content_to_use, 'html.parser')

        # Remove script and style elements
        for script_or_style in soup(["script", "style"]):
            script_or_style.extract()

        # Get the human-readable text
        text = soup.get_text()

        # Clean up the text
        lines = (line.strip() for line in text.splitlines())
        readable_text = '\n'.join(line for line in lines if line)

        # Remove excessive whitespace and non-breaking spaces
        readable_text = re.sub(r'\s+', ' ', readable_text)
        readable_text = re.sub(r'\u200c', '', readable_text)

        # Remove surrogate characters
        readable_text = re.sub(r'[\ud800-\udfff]', '', readable_text)

        return readable_text

    except Exception as e:
        print(f"Error extracting text: {str(e)}")
        return ""



def connect_email_server(data):
    """Connect to the email server and return the client."""
    email_client = None
    if data['email_type'] == 'IMAP':
        try:
            email_client = IMAP4_SSL(data['imap_server'], int(data['imap_port'])) if data['imap_use_ssl'] else IMAP4_SSL(data['imap_server'])
            email_client.login(data['email_address'], data['password'])
            email_client.select("inbox")
        except Exception as e:
            raise Exception(f"IMAP connection failed: {str(e)}")
    elif data['email_type'] == 'POP3':
        try:
            email_client = POP3_SSL(data['imap_server'], int(data['imap_port'])) if data['imap_use_ssl'] else POP3(data['imap_server'], int(data['imap_port']))
            email_client.user(data['email_address'])
            email_client.pass_(data['password'])
        except Exception as e:
            raise Exception(f"POP3 connection failed: {str(e)}")
    elif data['email_type'] == 'SMTP':
        try:
            email_client = smtplib.SMTP_SSL(data['smtp_server'], int(data['smtp_port'])) if data['smtp_use_ssl'] else smtplib.SMTP(data['smtp_server'], int(data['smtp_port']))
            email_client.login(data['email_address'], data['password'])
        except Exception as e:
            raise Exception(f"SMTP connection failed: {str(e)}")

    return email_client


# Utility function: Normalize email addresses
def normalize_email(addresses):
    if not addresses:
        return []

    # Split addresses by comma and strip whitespace
    address_list = [addr.strip() for addr in addresses.split(',')]

    # Use a more comprehensive regex to match email addresses
    email_regex = r'[\w\.-]+@[\w\.-]+\.\w+'
    normalized_emails = []

    for addr in address_list:
        # Extract email using regex
        match = re.search(email_regex, addr)
        if match:
            normalized_emails.append(match.group(0).lower())

    return normalized_emails


def read_imap_emails(account, user_id):
    global scan_status
    if scan_status is None:
        scan_status = {}

    scan_status[(user_id, account.id)] = 'running'
    try:
        email_client = connect_email_server({
            'email_type': account.provider,
            'imap_server': account.imap_server,
            'imap_port': account.imap_port,
            'imap_use_ssl': account.imap_use_ssl,
            'email_address': account.email,
            'password': account.password
        })

        # Convert start and end dates to the required format
        start_date = account.start_date
        end_date = account.end_date + timedelta(days=1)

        # Remove all previous info for this email account
        EmailFolder.query.filter_by(user_id=user_id, account_id=account.id).delete()
        Email.query.filter_by(user_id=user_id, account_id=account.id).delete()
        EmailAddress.query.filter_by(user_id=user_id, email_account_id=account.id).delete()
        db.session.commit()

        existing_email_addresses = {}
        # Select all mailboxes
        status, mailboxes = email_client.list()
        if status != "OK":
            raise Exception("Failed to list mailboxes")

        # List to store mailboxes with emails
        mailboxes_with_emails = []

        # First pass: Get mailbox metadata and create EmailFolder entries
        for mailbox in mailboxes:
            try:
                # Extract the mailbox name
                mailbox_name = mailbox.decode().split(' "/" ')[1].strip('"')
            except IndexError:
                print(f"Failed to parse mailbox: {mailbox}")
                continue

            # Skip special folders like "[Gmail]" or "All Mail"
            if mailbox_name.startswith("[Gmail]") or "All Mail" in mailbox_name:
                # print(f"Skipping special folder: {mailbox_name}")
                continue

            # Use SELECT to get folder metadata
            status, data = email_client.select(f'"{mailbox_name}"', readonly=True)
            if status != "OK":
                print(f"Failed to select mailbox: {mailbox_name}")
                continue

            # Search emails for the entire date range to determine folder size
            status, email_ids = email_client.search(None, f'(SINCE "{start_date.strftime("%d-%b-%Y")}" BEFORE "{end_date.strftime("%d-%b-%Y")}")')
            if status != "OK":
                print(f"Failed to search emails: {mailbox_name}")
                continue

            if not email_ids or len(email_ids) == 0:
                continue

            if not email_ids[0] or len(email_ids[0]) == 0:
                continue

            email_ids = email_ids[0].split()
            email_count = len(email_ids)  # Count only emails within the date range

            # print(f"Mailbox: {mailbox_name} - Total Email count: {email_count}")
            if email_count > 0:
                # Store the folder with the count of emails in the entire date range
                new_folder = EmailFolder(
                    user_id=user_id,
                    account_id=account.id,
                    folder=mailbox_name,
                    email_count=email_count
                )
                db.session.add(new_folder)
                db.session.commit()  # Commit the folder size

                # Add mailbox to the list of mailboxes with emails
                mailboxes_with_emails.append(mailbox_name)

        # Second pass: Process emails only for mailboxes with emails
        processed_email_ids = set()  # Track processed email IDs

        for mailbox_name in mailboxes_with_emails:
            # Check if scan_status is "stopping"
            if scan_status.get((user_id, account.id)) == 'stopping':
                break

            # Use SELECT to get folder metadata
            status, data = email_client.select(f'"{mailbox_name}"', readonly=True)
            if status != "OK":
                print(f"Failed to select mailbox: {mailbox_name}")
                continue

            # Break the date range into 10-day batches
            current_start_date = start_date
            while current_start_date < end_date:
                current_end_date = min(current_start_date + timedelta(days=10), end_date)

                try:
                    # Search emails for the current 10-day batch
                    status, email_ids = email_client.search(
                        None,
                        f'(SINCE "{current_start_date.strftime("%d-%b-%Y")}" BEFORE "{current_end_date.strftime("%d-%b-%Y")}")'
                    )
                    if status != "OK":
                        print(f"Failed to search emails: {mailbox_name}")
                        continue

                    email_ids = email_ids[0].split()

                    for num in email_ids:
                        # Fetch the full email and its UID
                        status, data = email_client.fetch(num, '(UID RFC822)')
                        if status != "OK":
                            print(f"Failed to fetch email with ID: {num}")
                            continue

                        # Extract the UID
                        uid_match = re.search(r'UID (\d+)', data[0][0].decode())

                        if not uid_match:
                            print(f"Failed to extract UID for email ID: {num}")
                            continue
                        email_uid = uid_match.group(1)

                        raw_email_data = data[0][1]  # This is the raw email data
                        # Parse the email to extract necessary fields
                        msg = BytesParser(policy=default).parsebytes(raw_email_data)

                        # Extract the email date
                        email_date_header = msg['Date']
                        if email_date_header:
                            email_date = parsedate_to_datetime(email_date_header)
                        else:
                            print(f"Email with ID: {email_uid} has no date. Skipping.")
                            continue

                        # Check if email has been processed
                        if email_uid in processed_email_ids:
                            continue

                        sender = msg['From']
                        if not sender:
                            print(f"Email with UID: {email_uid} has no sender. Skipping.")
                            continue

                        receivers = ', '.join(filter(None, [msg.get('To'), msg.get('Cc'), msg.get('Bcc')]))

                        normalized_sender = normalize_email(sender)[0] if sender else None
                        normalized_receivers = normalize_email(receivers)

                        # Extract the email subject
                        email_subject = msg['Subject'] or "No Subject"

                        # Extract readable text content
                        text_content = extract_human_readable_text(msg)

                        # Ensure text_content is UTF-8 encoded, handling any encoding errors
                        try:
                            text_content = text_content.encode('utf-8', errors='ignore').decode('utf-8')
                        except Exception as e:
                            print(f"Encoding error for email UID: {email_uid}. Error: {e}")
                            text_content = ""  # Optionally set to an empty string or handle as needed

                        # Prepend the email subject to the text content with a newline
                        text_content = f"{email_subject}\n{text_content}"

                        # Store email in the database with folder information and raw data
                        email = Email(
                            user_id=user_id,
                            account_id=account.id,
                            email_id=email_uid,  # Use the UID as the unique identifier
                            email_date=email_date,
                            sender=normalized_sender,
                            receivers=', '.join(normalized_receivers),
                            folder=mailbox_name,
                            raw_data=raw_email_data,  # Store the raw email data
                            text_content=text_content  # Store the extracted text content
                        )
                        db.session.add(email)

                        # Add the unique_email_id to the processed_email_ids set to prevent future duplicates
                        processed_email_ids.add(email_uid)

                        # Update email address counts
                        for address in [normalized_sender] + normalized_receivers:
                            if address:
                                if address in existing_email_addresses:
                                    pass
                                else:
                                    new_email_address = EmailAddress(
                                        email=address,
                                        user_id=user_id,
                                        email_account_id=account.id,
                                        state='ignore'
                                    )
                                    db.session.add(new_email_address)
                                    existing_email_addresses[address] = new_email_address

                    # Commit after processing each 10-day batch
                    db.session.commit()

                except Exception as e:
                    print(f"Error processing emails in mailbox {mailbox_name}: {e}")
                    db.session.rollback()  # Rollback the session to clear the error state

                # Move to the next batch
                current_start_date = current_end_date

        return {'success': True}

    except Exception as e:
        return {'success': False, 'error': str(e)}

    finally:
        if account.provider == 'IMAP':
            email_client.logout()
        # Update status to 'stopped' when done
        scan_status[(user_id, account.id)] = 'stopped'


def export_to_mbox(user_id, account_id, mbox_file_path):
    # Create an mbox file
    mbox = mailbox.mbox(mbox_file_path)

    # Retrieve emails from the database
    emails = Email.query.filter_by(user_id=user_id, account_id=account_id).all()

    for email in emails:
        # Create a new mbox message
        mbox_message = mailbox.mboxMessage(email.raw_data)
        mbox.add(mbox_message)

    # Flush and close the mbox file
    mbox.flush()
    mbox.close()

    print(f"Exported {len(emails)} emails to {mbox_file_path}")


def summarize_email_content(content):
    """
    Summarizes the content types found in the provided EmailMessage object.
    """
    if not isinstance(content, EmailMessage):
        print("Provided content is not an EmailMessage object.")
        return

    content_types = {}

    for part in content.iter_parts():
        content_type = part.get_content_type()
        content_types[content_type] = content_types.get(content_type, 0) + 1

    print("Summary of content types found:")
    for ctype, count in content_types.items():
        print(f"{ctype}: {count}")

# Example usage
# summarize_email_content(email_message)
