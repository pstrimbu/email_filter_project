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
        print(f"Processing email ID: {email_id.decode()}")

        # Fetch email metadata using HEADER only
        status, msg_data = mail.fetch(email_id, "(BODY.PEEK[HEADER])")
        if msg_data and isinstance(msg_data[0], tuple):
            headers = BytesParser(policy=policy.default).parsebytes(msg_data[0][1])
            metadata = {
                "From": headers.get("From"),
                "To": headers.get("To"),
                "Date": headers.get("Date"),
                "Subject": headers.get("Subject")
            }
            print("Metadata:", metadata)

            # Write metadata to a file
            metadata_file = os.path.join(emails_dir, f"{email_id.decode()}_metadata.txt")
            with open(metadata_file, "w", encoding="utf-8") as file:
                for key, value in metadata.items():
                    file.write(f"{key}: {value}\n")
            print(f"Metadata written to {metadata_file}")
        else:
            print(f"Failed to retrieve metadata for email ID: {email_id.decode()}")

        # Initialize content variables
        plain_text_content = ""
        html_text_content = ""

        # Try to fetch the email content using BODY.PEEK[]
        status, msg_data = mail.fetch(email_id, "(BODY.PEEK[])")
        if msg_data and isinstance(msg_data[0], tuple):
            email_bytes = msg_data[0][1]
            msg = BytesParser(policy=policy.default).parsebytes(email_bytes)

            # Check if the email is multipart
            if msg.is_multipart():
                for part in msg.iter_parts():
                    content_type = part.get_content_type()
                    print(f"Found part with content type: {content_type}")

                    # Process plain text parts
                    if content_type == "text/plain":
                        plain_text_content += part.get_content().strip()
                    # Process HTML parts
                    elif content_type == "text/html":
                        html_content = part._payload
                        decoded_html_content = quopri.decodestring(html_content).decode('utf-8')
                        soup = BeautifulSoup(decoded_html_content, 'html.parser')
                        # Extract and clean text
                        html_text_content += "\n".join(line.strip() for line in soup.get_text().splitlines() if line.strip())
            else:
                # For non-multipart emails, handle as either plain text or HTML
                content_type = msg.get_content_type()
                print(f"Single-part email with content type: {content_type}")
                
                if content_type == "text/plain":
                    plain_text_content += msg.get_content().strip()
                elif content_type == "text/html":
                    # Parse HTML content from msg._payload
                    html_content = msg._payload
                    # Decode quoted-printable content
                    decoded_html_content = quopri.decodestring(html_content).decode('utf-8')
                    soup = BeautifulSoup(decoded_html_content, 'html.parser')
                    # Extract and clean text
                    html_text_content = "\n".join(line.strip() for line in soup.get_text().splitlines() if line.strip())

        # Clean up plain text content by removing excessive newlines
        plain_text_content = "\n".join(
            line.strip() for line in plain_text_content.splitlines() if line.strip()
        )
        
        # Write plain text and parsed HTML content to files
        if plain_text_content and plain_text_content != "":
            plain_text_file = os.path.join(emails_dir, f"{email_id.decode()}_plain_text.txt")
            with open(plain_text_file, "w", encoding="utf-8") as file:
                file.write(plain_text_content)
            print(f"Plain text content written to {plain_text_file}")

        if html_text_content and html_text_content != "":
            html_text_file = os.path.join(emails_dir, f"{email_id.decode()}_html_text.txt")
            with open(html_text_file, "w", encoding="utf-8") as file:
                file.write(html_text_content if html_text_content else "No HTML content retrieved.")
            print(f"HTML content written to {html_text_file}")

        # Fetch the entire raw email content
        status, msg_data = mail.fetch(email_id, "(BODY.PEEK[])")
        if msg_data and isinstance(msg_data[0], tuple):
            raw_email_content = msg_data[0][1]

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
