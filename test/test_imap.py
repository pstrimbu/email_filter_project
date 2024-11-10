import imaplib
import os
from email import policy
from email.parser import BytesParser
from bs4 import BeautifulSoup
import quopri
from email.utils import getaddresses

def read_emails(email_client, mailboxes_with_emails):
    for mailbox_name in mailboxes_with_emails:
        print(f"Processing mailbox: {mailbox_name}")

        # Select the mailbox
        email_client.select(mailbox_name)

        # Search for all emails in the mailbox
        status, messages = email_client.search(None, "ALL")
        if status != 'OK' or not messages or not messages[0]:
            print(f"Failed to retrieve emails from {mailbox_name} or no emails found")
            continue

        email_ids = messages[0].split()
        print(f"Email count in {mailbox_name}: {len(email_ids)}")

        # Process emails in batches of 10
        for i in range(0, len(email_ids), 10):
            batch_ids = email_ids[i:i+10]

            for email_id in batch_ids:
                # Convert email ID from bytes to an integer
                email_id_int = int(email_id.decode('utf-8'))

                # Fetch the entire raw email content
                status, msg_data = email_client.fetch(email_id, "(BODY.PEEK[])")
                if msg_data and isinstance(msg_data[0], tuple):
                    raw_email_content = msg_data[0][1]

                    # Parse the email content
                    email_message = BytesParser(policy=policy.default).parsebytes(raw_email_content)

                    # Extract email details
                    sender = email_message['From']
                    to_recipients = email_message.get_all('To', [])
                    cc_recipients = email_message.get_all('Cc', [])
                    bcc_recipients = email_message.get_all('Bcc', [])

                    date = email_message['Date']
                    subject = email_message['Subject']

                    # Attempt to get the plain text content
                    body = email_message.get_body(preferencelist=('plain'))
                    if body is not None:
                        content = body.get_content()
                    else:
                        # Fallback to HTML content if plain text is not available
                        body = email_message.get_body(preferencelist=('html'))
                        if body is not None:
                            html_content = body.get_content()
                            # Convert HTML to plain text
                            soup = BeautifulSoup(html_content, 'html.parser')
                            content = soup.get_text()
                        else:
                            content = "No content available"

                    # Clean up content by removing extra spaces and lines
                    content = ' '.join(content.split())

                    # Strip actual email addresses and convert to lowercase
                    sender_email = getaddresses([sender])[0][1].lower()
                    all_recipients = getaddresses(to_recipients + cc_recipients + bcc_recipients)
                    all_recipients_emails = [email[1].lower() for email in all_recipients]

                    # Print email details
                    print(f"Email ID: {email_id_int}")
                    print(f"Sender: {sender_email}")
                    print(f"Recipients: {all_recipients_emails}")
                    print(f"Date: {date}")
                    print(f"Subject: {subject}")
                    print(f"Content: {content}")

                print("--------------------------------")

def get_mailboxes(email_client):
    # List all mailboxes
    status, mailboxes = email_client.list()
    if status != 'OK':
        print("Failed to retrieve mailboxes")
        return

    mailboxes_with_emails = []

    for mailbox in mailboxes:
        # Extract mailbox name
        mailbox_name = mailbox.decode().split(' "/" ')[-1]
        # print(f"Processing mailbox: {mailbox_name}")

        # Use SELECT with readonly=True to get the number of messages without fetching all IDs
        status, data = email_client.select(mailbox_name, readonly=True)
        if status != 'OK':
            print(f"Failed to examine mailbox {mailbox_name}")
            continue

        # Extract the number of messages from the response
        num_messages = int(data[0].decode())
        if num_messages > 0:
            print(f"Email count in {mailbox_name}: {num_messages}")
            mailboxes_with_emails.append(mailbox_name)
    
    return mailboxes_with_emails

# username = "peterstrimbu@me.com"
# password = "cewk-qotr-rvfc-jiag"
# imap_server = "imap.mail.me.com"

username = "peter@strimbu.com"
password = "tyct mvou tbro pnhl"
imap_server = "imap.gmail.com"

# Set up directory for storing email content
current_dir = os.getcwd()
emails_dir = os.path.join(current_dir, "emails")
os.makedirs(emails_dir, exist_ok=True)

# Connect to the iCloud IMAP server
email_client = imaplib.IMAP4_SSL(imap_server)
email_client.login(username, password)

get_mailboxes(email_client)
# read_emails(email_client, mailboxes_with_emails)

email_client.logout()