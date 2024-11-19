from .extensions import db
from .models import Email, EmailAddress, EmailFolder
from bs4 import BeautifulSoup
from datetime import timedelta
from email.parser import BytesParser
from email.policy import default
from imaplib import IMAP4_SSL
import re
from email.utils import parsedate_to_datetime
from email_filter.globals import scan_status
from email.message import EmailMessage
from email.utils import getaddresses

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

        return readable_text.encode('utf-8', errors='ignore').decode('utf-8')

    except Exception as e:
        print(f"Error extracting text: {str(e)}")
        return ""


def connect_email_server(data):
    """Connect to the email server and return the client."""
    try:
        # Use the provided server details directly
        email_client = IMAP4_SSL(data['imap_server'], int(data['imap_port'])) if data['imap_use_ssl'] else IMAP4_SSL(data['imap_server'])
        email_client.login(data['email_address'], data['password'])
        return email_client
    except Exception as e:
        raise Exception(f"Connection failed: {str(e)}")


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


def get_folders(email_client, account, user_id, start_date, end_date):
    """Retrieve mailboxes and create EmailFolder entries."""
    try:
        # List all mailboxes
        status, mailboxes = email_client.list()
        if status != 'OK':
            print("Failed to retrieve mailboxes")
            return []

        mailboxes_with_emails = []

        for mailbox in mailboxes:
            try:
                # Extract mailbox name
                mailbox_name = mailbox.decode().split(' "/" ')[-1].strip('"')

                # Skip folders that start with "[Gmail]"
                if mailbox_name.startswith("[Gmail]"):
                    continue
                
                # Use SELECT with readonly=True to get the number of messages without fetching all IDs
                status, data = email_client.select(mailbox_name, readonly=True)
                if status != 'OK':
                    print(f"Failed to examine mailbox {mailbox_name}")
                    continue

                # If start_date and end_date are set, filter emails by date range
                if start_date and end_date:
                    status, email_ids_data = email_client.search(None, f'(SINCE "{start_date.strftime("%d-%b-%Y")}" BEFORE "{end_date.strftime("%d-%b-%Y")}")')
                    if status != "OK":
                        print(f"Failed to search emails in mailbox {mailbox_name}")
                        continue
                    # Count the number of emails in the date range
                    email_count = len(email_ids_data[0].decode().split()) if email_ids_data[0] else 0
                else:
                    status, data = email_client.select(mailbox_name, readonly=True)
                    if status != 'OK':
                        print(f"Failed to examine mailbox {mailbox_name}")
                        continue

                    # Extract the number of messages from the response
                    email_count = int(data[0].decode())


                if email_count > 0:
                    # Store the folder with the count of emails
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
            except Exception as e:
                # print(f"Failed to read mailbox: {mailbox}, error: {e}")
                continue

        return mailboxes_with_emails

    except Exception as e:
        print(f"Error in get_folders: {str(e)}")
        return []


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
        end_date = account.end_date + timedelta(days=1) if account.end_date else None

        # Remove all previous info for this email account
        EmailFolder.query.filter_by(user_id=user_id, account_id=account.id).delete()
        Email.query.filter_by(user_id=user_id, account_id=account.id).delete()
        EmailAddress.query.filter_by(user_id=user_id, email_account_id=account.id).delete()
        db.session.commit()

        # Initialize an empty set for existing email addresses since they were just deleted
        existing_email_addresses = set()

        # Initialize a set to keep track of unique email addresses
        unique_email_addresses = set()

        # Get mailboxes with emails
        mailboxes_with_emails = get_folders(email_client, account, user_id, start_date, end_date)

        for mailbox_name in mailboxes_with_emails:
            if mailbox_name.startswith('NOTES'):
                continue
            try:
                # Use SELECT with readonly=True to get the number of messages without fetching all IDs
                status, data = email_client.select(mailbox_name, readonly=True)
                if status == 'OK':
                    if start_date and end_date:
                        status, email_ids_data = email_client.search(None, f'(SINCE "{start_date.strftime("%d-%b-%Y")}" BEFORE "{end_date.strftime("%d-%b-%Y")}")')
                    else:
                        status, email_ids_data = email_client.search(None, "ALL")
                    if status == "OK":
                        # Convert email_ids_data from bytes to a list of strings
                        email_ids = email_ids_data[0].decode().split() if email_ids_data[0] else []
                        
                        # Sort and filter for unique email IDs
                        email_ids = sorted(set(email_ids))

                        # Fetch emails in batches
                        batch_size = 20  # Define the number of emails to fetch at once
                        for i in range(0, len(email_ids), batch_size):
                            try:
                                # Check if scan_status is "stopping"
                                if scan_status.get((user_id, account.id)) == 'stopping':
                                    return {'success': False, 'error': 'stopped by user request'}

                                batch_ids = email_ids[i:i + batch_size]
                                batch_ids_str = ','.join(batch_ids)

                                # Fetch the entire raw email content
                                status, msg_data = email_client.fetch(batch_ids_str, "(BODY.PEEK[])")
                                for response_part in msg_data:
                                    try:
                                        if isinstance(response_part, tuple):
                                            raw_email_data = response_part[1]
                                            email_message = BytesParser(policy=default).parsebytes(raw_email_data)

                                            # Extract email metadata
                                            metadata = {
                                                "From": email_message.get("From"),
                                                "To": email_message.get("To"),
                                                "Date": email_message.get("Date"),
                                                "Subject": email_message.get("Subject")
                                            }

                                            email_id = response_part[0].split()[0].decode()
                                            sender = metadata["From"]
                                            sender_email = getaddresses([sender])[0][1].lower()

                                            to_recipients = email_message.get_all('To', [])
                                            cc_recipients = email_message.get_all('Cc', [])
                                            bcc_recipients = email_message.get_all('Bcc', [])
                                            all_recipients = getaddresses(to_recipients + cc_recipients + bcc_recipients)
                                            all_recipients_emails = [email[1].lower() for email in all_recipients if email[1]]

                                            # Add sender and recipients to the unique email addresses set
                                            if sender_email:
                                                unique_email_addresses.add(sender_email)
                                            unique_email_addresses.update(all_recipients_emails)

                                            email_date = parsedate_to_datetime(metadata["Date"]).strftime('%Y-%m-%d %H:%M:%S')

                                            body = email_message.get_body(preferencelist=('plain'))
                                            if body is not None:
                                                content = body.get_content()
                                            else:
                                                body = email_message.get_body(preferencelist=('html'))
                                                if body is not None:
                                                    html_content = body.get_content()
                                                    soup = BeautifulSoup(html_content, 'html.parser')
                                                    content = soup.get_text()
                                                else:
                                                    content = "No content available"

                                            email_subject = email_message['Subject']
                                            email_body = ' '.join(content.split())
                                            text_content = f"{email_subject} {email_body}"

                                            receivers_str = ','.join(all_recipients_emails)

                                            # Check if the email already exists
                                            existing_email = Email.query.filter_by(
                                                user_id=user_id,
                                                account_id=account.id,
                                                email_id=email_id
                                            ).first()

                                            if existing_email:
                                                # Update existing email if necessary
                                                existing_email.raw_data = raw_email_data
                                                # Update other fields as needed
                                            else:
                                                # Create a new email record
                                                email = Email(
                                                    user_id=user_id,
                                                    account_id=account.id,
                                                    email_id=email_id,
                                                    email_date=email_date,
                                                    sender=sender_email,
                                                    receivers=receivers_str,
                                                    folder=mailbox_name,
                                                    raw_data=raw_email_data,
                                                    text_content=text_content
                                                )
                                                db.session.add(email)
                                    except Exception as e:
                                        print(f"Error processing email {email_id}: {e}")
                                        continue
                                # Insert new email addresses into the EmailAddress table
                                new_email_addresses = unique_email_addresses - existing_email_addresses
                                for email_address in new_email_addresses:
                                    if email_address not in existing_email_addresses:
                                        existing_email_addresses.add(email_address)
                                        new_email_address = EmailAddress(user_id=user_id, email_account_id=account.id, email=email_address)
                                        db.session.add(new_email_address)

                                # Commit after processing each batch
                                db.session.commit()
                            except Exception as e:
                                print(f"Error processing emails in batch {i}: {e}")
                                continue

            except Exception as e:
                print(f"Error processing emails in mailbox {mailbox_name}: {e}")
                continue

        return {'success': True}

    except Exception as e:
        return {'success': False, 'error': str(e)}

    finally:
        if account.provider == 'IMAP':
            email_client.logout()
        scan_status[(user_id, account.id)] = 'stopped'
