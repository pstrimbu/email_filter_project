import threading
import time
from datetime import datetime
from flask import current_app
from .models import db, Job, Prompt
from imapclient import IMAPClient
import openai

def process_emails(job_id):
    with current_app.app_context():
        job = Job.query.get(job_id)
        if not job:
            print(f"No job found with ID {job_id}")
            return

        # Update job status to 'Processing'
        job.status = 'Processing'
        db.session.commit()

        user = job.user

        # Connect to IMAP server and process emails
        imap_client = IMAPClient(user.imap_server, use_uid=True, ssl=user.imap_use_ssl)
        imap_client.login(user.email_address, user.password)

        imap_client.select_folder('INBOX')
        messages = imap_client.search(['SINCE', job.start_date.strftime('%d-%b-%Y'), 'BEFORE', (job.end_date + timedelta(days=1)).strftime('%d-%b-%Y')])

        for message_id in messages:
            email_data = imap_client.fetch([message_id], ['BODY[TEXT]', 'BODY[HEADER.FIELDS (SUBJECT)]'])
            email_body = email_data[message_id][b'BODY[TEXT]'].decode('utf-8')
            email_subject = email_data[message_id][b'BODY[HEADER.FIELDS (SUBJECT)]'].decode('utf-8')

            for prompt in job.prompts:
                response = openai.Completion.create(
                    engine="text-davinci-003",
                    prompt=f"{prompt.criteria}\n\nEmail Subject: {email_subject}\n\nEmail Body: {email_body}",
                    max_tokens=1000
                )

                if "positive match" in response.choices[0].text.lower():
                    tag_folder = f"{user.id}_{prompt.tag}"
                    if not os.path.exists(tag_folder):
                        os.makedirs(tag_folder)

                    filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{email_subject}.txt"
                    filepath = os.path.join(tag_folder, filename)
                    with open(filepath, 'w') as f:
                        f.write(email_body)

        # Update job status to 'Completed'
        job.status = 'Completed'
        db.session.commit()
