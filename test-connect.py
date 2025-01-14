import imaplib
import ssl

def test_outlook_imap_connectivity(email, password):
    # IMAP server details
    IMAP_SERVER = "outlook.office365.com"
    IMAP_PORT = 993

    try:
        # Establish SSL connection to the IMAP server with enhanced security
        context = ssl.create_default_context()
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        print(f"Connecting to IMAP server {IMAP_SERVER}:{IMAP_PORT}...")
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT, ssl_context=context)

        # Attempt login
        print("Attempting to log in...")
        mail.login(email, password)
        print("Login successful!")

        # List mailboxes as a test query
        print("Listing mailboxes...")
        status, mailboxes = mail.list()
        if status == "OK":
            print("Mailboxes:")
            for mailbox in mailboxes:
                print(mailbox.decode())
        else:
            print("Failed to retrieve mailboxes.")

        # Logout from the server
        mail.logout()
        print("Logged out successfully.")

    except imaplib.IMAP4.error as e:
        print(f"IMAP error: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    # Input user credentials
    email = "peter@travelminds.com"
    password = "9rs9HK64LDu%"

    # Test IMAP connectivity
    test_outlook_imap_connectivity(email, password)
