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

    # Fetch emails in batches
    batch_size = 10  # Define the number of emails to fetch at once
    for i in range(0, len(email_ids), batch_size):
        batch_ids = email_ids[i:i + batch_size]
        batch_ids_str = ','.join(email_id.decode() for email_id in batch_ids)
        
        print(f"Processing email IDs: {batch_ids_str}")

        # Fetch email metadata using HEADER only
        status, msg_data = mail.fetch(batch_ids_str, "(BODY.PEEK[HEADER])")
        for response_part in msg_data:
            if isinstance(response_part, tuple):
                headers = BytesParser(policy=policy.default).parsebytes(response_part[1])
                metadata = {
                    "From": headers.get("From"),
                    "To": headers.get("To"),
                    "Date": headers.get("Date"),
                    "Subject": headers.get("Subject")
                }
                email_id = headers.get("Message-ID").strip('<>')
                print("Metadata:", metadata)

                # Write metadata to a file
                metadata_file = os.path.join(emails_dir, f"{email_id}_metadata.txt")
                with open(metadata_file, "w", encoding="utf-8") as file:
                    for key, value in metadata.items():
                        file.write(f"{key}: {value}\n")
                print(f"Metadata written to {metadata_file}")
            else:
                print(f"Failed to retrieve metadata for email ID: {email_id}")

        # Fetch the entire raw email content
        status, msg_data = mail.fetch(batch_ids_str, "(BODY.PEEK[])")
        for response_part in msg_data:
            if isinstance(response_part, tuple):
                raw_email_content = response_part[1]

                # Write the raw email content to an mbox file
                mbox_file = os.path.join(emails_dir, "emails.mbox")
                with open(mbox_file, "ab") as file:
                    file.write(b"From - \n")  # mbox format requires a "From " line
                    file.write(raw_email_content)
                    file.write(b"\n\n")  # Ensure separation between emails
                print(f"Raw email content written to {mbox_file}")

        print("--------------------------------")

    mail.logout()

# Example usage
username = "peterstrimbu@me.com"
password = "cewk-qotr-rvfc-jiag"
fetch_icloud_emails(username, password)
