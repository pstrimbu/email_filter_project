import mailbox

def display_mbox_emails(mbox_file_path):
    try:
        # Open the mbox file
        mbox = mailbox.mbox(mbox_file_path)

        # Loop through each message in the mbox file
        for i, message in enumerate(mbox):
            print(f"Email #{i + 1}")
            print("From:", message.get("From"))
            print("To:", message.get("To"))
            print("Date:", message.get("Date"))
            print("Subject:", message.get("Subject"))

            # Retrieve and display the email content based on content type
            if message.is_multipart():
                for part in message.walk():
                    content_type = part.get_content_type()
                    if content_type == "text/plain":
                        print("\nPlain Text Content:\n", part.get_payload(decode=True).decode(errors="replace"))
                    elif content_type == "text/html":
                        print("\nHTML Content:\n", part.get_payload(decode=True).decode(errors="replace"))
            else:
                # For single-part messages
                print("\nContent:\n", message.get_payload(decode=True).decode(errors="replace"))

            print("-" * 50)
    except Exception as e:
        print("Error reading mbox file:", e)

# Set the path to your mbox file
mbox_file_path = "/Users/peterstrimbu/dev/email_filter_project/emails/emails.mbox"
display_mbox_emails(mbox_file_path)
