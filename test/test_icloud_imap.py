import imaplib
import os
from email import policy
from email.parser import BytesParser
from bs4 import BeautifulSoup
import quopri

def fetch_icloud_emails(username, password):
    # Set up directory for storing email content
    current_dir = os.getcwd()
    emails_dir = os.path.join(current_dir, "emails")
    os.makedirs(emails_dir, exist_ok=True)

    # Connect to the iCloud IMAP server
    imap_server = "imap.mail.me.com"
    mail = imaplib.IMAP4_SSL(imap_server)
    mail.login(username, password)
    mail.select("inbox")

    # Search for all emails in the inbox
    status, messages = mail.search(None, "ALL")
    email_ids = messages[0].split()

    for email_id in email_ids:
        # Convert email ID from bytes to an integer
        email_id_int = int(email_id.decode('utf-8'))

        # Fetch the entire raw email content
        status, msg_data = mail.fetch(email_id, "(BODY.PEEK[])")
        if msg_data and isinstance(msg_data[0], tuple):
            raw_email_content = msg_data[0][1]

            # Parse the email content
            email_message = BytesParser(policy=policy.default).parsebytes(raw_email_content)

            # Extract email details
            sender = email_message['From']
            receiver = email_message['To']
            date = email_message['Date']
            subject = email_message['Subject']
            # message_id = email_message['Message-ID']

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

            # Print email details
            print(f"Email ID: {email_id_int}")
            print(f"Sender: {sender}")
            print(f"Receiver: {receiver}")
            print(f"Date: {date}")
            print(f"Subject: {subject}")
            print(f"Content: {content}")

            # Write the raw email content to an mbox file
            # mbox_file = os.path.join(emails_dir, "emails.mbox")
            # with open(mbox_file, "ab") as file:
            #     file.write(b"From - \n")  # mbox format requires a "From " line
            #     file.write(raw_email_content)
            #     file.write(b"\n\n")  # Ensure separation between emails
            # print(f"Raw email content written to {mbox_file}")

        print("--------------------------------")

    mail.logout()

# Example usage
username = "peterstrimbu@me.com"
password = "cewk-qotr-rvfc-jiag"
fetch_icloud_emails(username, password)
