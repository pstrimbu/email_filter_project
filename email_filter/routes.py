import os
from flask import render_template, url_for, flash, redirect, request, jsonify, send_from_directory
from flask_login import login_user, current_user, logout_user, login_required
from .forms import RegistrationForm, LoginForm, EmailAccountForm, CSRFTokenForm, JobForm
from .models import Filter, EmailAccount, EmailAddress, User, Email, EmailFolder, AIPrompt, Result, email_receivers
from datetime import datetime
from .email_processor import read_imap_emails
from imaplib import IMAP4_SSL
from email.policy import default
from . import bcrypt, db
from email_filter.globals import scan_status, processing_status
from .export_processor import process_emails, stop
from sqlalchemy import or_, func, case, distinct, and_
from sqlalchemy.orm import aliased
from email_filter.aws import SpotInstanceManager, delete_file_from_s3
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def init_routes(app):
    # Initialize the SpotInstanceManager
    spot_instance_manager = SpotInstanceManager()

    @app.errorhandler(401)
    async def unauthorized(error):
        logger.warning('Unauthorized access attempt.')
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('login', next=request.url))


    @app.route("/process_email_results", methods=['GET', 'POST'])
    @login_required
    async def process_email_results():
        if request.method == 'GET':
            email_account_id = request.args.get('email_account_id')
            if not email_account_id:
                return jsonify(success=False, message='Account ID is required'), 400

            try:
                email_account_id = int(email_account_id)
                results = Result.query.filter_by(user_id=current_user.id, email_account_id=email_account_id).all()
                
                if results:
                    process_data = {
                        'status': results[0].status,
                        'log_entry': results[0].log_entry,
                        'id': results[0].id,
                        'name': results[0].name,
                        'file_url': results[0].file_url,
                        'zip_password': results[0].zip_password
                    }
                else:
                    process_data = {
                        'status': 'not started',
                        'log_entry': '',
                        'id': '0',
                        'name': None,
                        'file_url': None,
                        'zip_password': None
                    }
                return render_template('process.html', process_data=process_data, csrf_form=CSRFTokenForm())
            
            except Exception as e:
                logger.error(f"Error in process_email_results: {e}")
                return jsonify(success=False, message='An internal error occurred'), 500

        elif request.method == 'POST':
            data = request.get_json()
            email_account_id = data.get('email_account_id')
            if not email_account_id:
                return jsonify(success=False, message='Account ID is required'), 400

            try:
                email_account_id = int(email_account_id)
                # Await the async process_emails function
                await process_emails(current_user.id, email_account_id)
                
                # Fetch the first result for the user and account
                result = Result.query.filter_by(user_id=current_user.id, email_account_id=email_account_id).first()
                
                if result:
                    process_data = {
                        'status': result.status,
                        'log_entry': result.log_entry,
                        'id': result.id,
                        'name': result.name,
                        'file_url': result.file_url,
                        'zip_password': result.zip_password
                    }
                    return jsonify(success=True, process_data=process_data)
                else:
                    return jsonify(success=False, message='No result found'), 404
            except Exception as e:
                logger.error(f"Error processing email results: {e}")
                return jsonify(success=False, error="An error occurred while processing email results"), 500
        
   
    @app.route("/register", methods=['GET', 'POST'])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for('home'))
        form = RegistrationForm()
        if form.validate_on_submit():
            invite_code = request.form.get('invite_code')
            if invite_code != 'ntech':
                flash('Invalid Invite Code', 'danger')
                return redirect(url_for('register'))
            
            hashed_password = bcrypt.generate_password_hash(form.password.data).decode('utf-8')
            user = User(email_address=form.email.data, password=hashed_password)
            db.session.add(user)
            db.session.commit()
            flash('Your account has been created!', 'success')
            return redirect(url_for('login'))
        return render_template('register.html', title='Register', form=form)

    @app.route("/login", methods=['GET', 'POST'])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for('home'))
        form = LoginForm()
        if form.validate_on_submit():
            user = User.query.filter_by(email_address=form.email.data).first()
            if user and bcrypt.check_password_hash(user.password, form.password.data):
                login_user(user, remember=form.remember.data)
                next_page = request.args.get('next')
                return redirect(next_page) if next_page else redirect(url_for('home'))
            else:
                flash('Login unsuccessful. Please check email and password', 'danger')
        return render_template('login.html', title='Login', form=form)

    @app.route("/logout")
    def logout():
        logout_user()
        return redirect(url_for('login'))

    @app.route("/")
    @app.route("/home", methods=['GET', 'POST'])
    @login_required
    def home():
        csrf_form = CSRFTokenForm()
        job_form = JobForm()
        email_accounts_data = EmailAccount.query.filter_by(user_id=current_user.id).all()
        
        # Fetch data for each email account
        email_accounts = []
        for account in email_accounts_data:
            email_accounts.append({
                'id': account.id,
                'email_address': account.email_address,
                'start_date': account.start_date,
                'end_date': account.end_date
            })

        return render_template('home.html', csrf_form=csrf_form, email_accounts=email_accounts)

    # Add a route to check the database connection
    @app.route('/check_db')
    def check_db():
        try:
            users = User.query.all()
            return f"Database connected successfully. Number of users: {len(users)}"
        except Exception as e:
            return f"Error connecting to database: {str(e)}"

    @app.route('/get_email_accounts', methods=['GET'])
    @login_required
    def get_email_accounts():
        email_accounts = EmailAccount.query.filter_by(user_id=current_user.id).all()
        accounts_data = [{'id': account.id, 'email_address': account.email_address} for account in email_accounts]
        return jsonify({'success': True, 'email_accounts': accounts_data})

    @app.route("/email_accounts")
    @login_required
    def email_accounts():
        # Fetch email_address accounts for the current user
        email_accounts = EmailAccount.query.filter_by(user_id=current_user.id).all()
        return render_template('email_accounts.html', email_accounts=email_accounts, csrf_form=CSRFTokenForm())

    @app.route('/email_account_view/<int:email_account_id>', methods=['GET'])
    @login_required
    def email_account_view(email_account_id):
        email_account_id = int(email_account_id)
        account = EmailAccount.query.get_or_404(email_account_id)

        # Obfuscate the password
        account.password = '*' * len(account.password)

        # Create a form instance with the obfuscated password
        form = EmailAccountForm(obj=account)

        return render_template('email_account_view.html', email_form=form, account=account)

    @app.route('/email_account_add', methods=['GET', 'POST'])
    @login_required
    def email_account_add():
        email_form = EmailAccountForm()

        if request.method == 'POST':
            if email_form.validate_on_submit():

                try:                  
                    new_account = EmailAccount(
                        email_address=email_form.email_address.data,
                        password=email_form.password.data,
                        provider=email_form.email_type.data,
                        user_id=current_user.id
                    )

                    if email_form.email_type.data == 'GMAIL':
                        new_account.imap_server = 'imap.gmail.com'
                        new_account.imap_port = 993
                        new_account.imap_use_ssl = True
                    elif email_form.email_type.data == 'APPLE':
                        new_account.imap_server = 'imap.mail.me.com'
                        new_account.imap_port = 993
                        new_account.imap_use_ssl = True
                    elif email_form.email_type.data == 'OFFICE':
                        new_account.imap_server = 'outlook.office365.com'
                        new_account.imap_port = 993
                        new_account.imap_use_ssl = True

                    db.session.add(new_account)
                    db.session.commit()
                    return jsonify(success=True, message='Email account added successfully!', email_account_id=new_account.id)
                except Exception as e:
                    return jsonify(success=False, error=str(e))
            else:
                print("Form validation errors:", email_form.errors)
                return jsonify(success=False, error="Form validation failed")

        return render_template('email_account_add.html', email_form=email_form)

    @app.route('/email_account_edit/<int:email_account_id>', methods=['GET', 'POST'])
    @login_required
    def email_account_edit(email_account_id):
        email_account_id = int(email_account_id)
        account = EmailAccount.query.get_or_404(email_account_id)
        form = EmailAccountForm(obj=account, is_edit=True)

        if request.method == 'POST':
            if form.validate_on_submit():
                try:
                    # Only update the password if a new one is provided
                    if form.password.data and form.password.data != '*' * len(account.password):
                        account.password = form.password.data
                    else:
                        form.password.data = account.password  # Keep the existing password

                    if form.email_type.data == 'GMAIL':
                        account.imap_server = 'imap.gmail.com'
                        account.imap_port = 993
                        account.imap_use_ssl = True
                    elif form.email_type.data == 'APPLE':
                        account.imap_server = 'imap.mail.me.com'
                        account.imap_port = 993
                        account.imap_use_ssl = True
                    elif form.email_type.data == 'OFFICE':
                        account.imap_server = 'outlook.office365.com'
                        account.imap_port = 993
                        account.imap_use_ssl = True

                    form.populate_obj(account)  # Update other fields
                    db.session.commit()  # Save changes to the database
                    return jsonify(success=True, message='Account updated successfully!')
                except Exception as e:
                    db.session.rollback()  # Rollback the session in case of an error
                    return jsonify(success=False, error=str(e))
            else:
                # Handle form validation errors
                return jsonify(success=False, error=form.errors)
        # For GET request, obfuscate the password for display
        account.password = '*' * len(account.password)
        return render_template('email_account_edit.html', email_form=form, account=account)

    @app.route("/email_account_delete/<int:email_account_id>", methods=['POST'])
    @login_required
    def email_account_delete(email_account_id):
        try:
            email_account_id = int(email_account_id)
            account = EmailAccount.query.get_or_404(email_account_id)
            db.session.delete(account)
            db.session.commit()

            return jsonify(success=True)
        except Exception as e:
            return jsonify(success=False, error=str(e))

    @app.route('/emails')
    @login_required
    def emails_view():
         return render_template('emails.html')

    @app.route("/toggle_email_address_state", methods=['POST'])
    @login_required
    def toggle_email_address_state():
        data = request.json
        address_id = data.get('address_id')
        new_state = data.get('new_state')

        if not address_id or not new_state:
            return jsonify({'success': False, 'message': 'Missing required data'}), 400

        email_address = EmailAddress.query.get_or_404(address_id)

        if email_address.user_id != current_user.id:
            return jsonify({'success': False, 'message': 'Unauthorized action'}), 403

        if new_state not in ['include', 'ignore', 'exclude']:
            return jsonify({'success': False, 'message': 'Invalid state'}), 400

        email_address.action = new_state
        db.session.commit()

        return jsonify({
            'success': True,
            'message': f'Email address state updated to {new_state}',
            'new_state': new_state
        })

    @app.route('/delete_emails/<int:email_account_id>', methods=['POST'])
    @login_required
    def delete_emails(email_account_id):
        try:
            email_account_id = int(email_account_id)
            account = EmailAccount.query.get_or_404(email_account_id)
            if account.user_id != current_user.id:
                return jsonify({'success': False, 'message': 'Unauthorized action'}), 403

            # Define batch size
            batch_size = 1000

            # Fetch email IDs in batches
            email_ids_query = db.session.query(Email.id).filter_by(user_id=current_user.id, email_account_id=email_account_id)
            email_ids = email_ids_query.all()

            for i in range(0, len(email_ids), batch_size):
                batch = email_ids[i:i + batch_size]

                # Delete email receivers for the batch
                db.session.execute(
                    email_receivers.delete().where(
                        email_receivers.c.email_id.in_([email_id[0] for email_id in batch])
                    )
                )

                # Delete emails for the batch
                Email.query.filter(Email.id.in_([email_id[0] for email_id in batch])).delete(synchronize_session=False)
                db.session.commit()

            # Set count to 0 for all email addresses instead of deleting them
            EmailAddress.query.filter_by(user_id=current_user.id, email_account_id=email_account_id).update({'count': 0})
            db.session.commit()

            # Delete email folders
            EmailFolder.query.filter_by(user_id=current_user.id, email_account_id=email_account_id).delete()
            db.session.commit()

            return jsonify({'success': True, 'message': 'Emails, email addresses, and email folders cleared successfully.'})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)})

    @app.route('/scan_emails/<int:email_account_id>', methods=['POST'])
    @login_required
    def scan_emails(email_account_id):
        email_account_id = int(email_account_id)
        email_account = EmailAccount.query.get(email_account_id)

        if email_account.user_id != current_user.id:
            return jsonify({'success': False, 'message': 'Unauthorized action'}), 403

        return read_imap_emails(email_account, current_user.id)


    def test_email_connection_logic(email_address, password, email_type, server, port):
        try:
            if email_type in ['GMAIL', 'APPLE', 'OFFICE']:
                imap = IMAP4_SSL(server, port)
                imap.login(email_address, password)
                imap.logout()
            return True, None
        except Exception as e:
            return False, str(e)

    @app.route('/test_new_email_connection', methods=['POST'])
    @login_required
    def test_new_email_connection():
        # Extract form data
        email_address = request.form.get('email_address')
        password = request.form.get('password')
        email_type = request.form.get('email_type')

        if email_type == 'GMAIL':
            server = 'imap.gmail.com'
            port = 993
        elif email_type == 'APPLE':
            server = 'imap.mail.me.com'
            port = 993       

        # Use the shared logic to test the connection
        success, error = test_email_connection_logic(email_address, password, email_type, server, port)

        if success:
            return jsonify(success=True)
        else:
            return jsonify(success=False, error=error)

    @app.route('/test_email_connection/<int:email_account_id>', methods=['POST'])
    @login_required
    def test_email_connection(email_account_id):
        # Fetch the account details from the database
        email_account_id = int(email_account_id)
        account = EmailAccount.query.get_or_404(email_account_id)

        # Ensure the account belongs to the current user
        if account.user_id != current_user.id:
            return jsonify({'success': False, 'message': 'Unauthorized action'}), 403

        # Extract the necessary details from the account
        email_address = account.email_address
        password = account.password
        email_type = account.provider
        server = account.imap_server
        port = account.imap_port

        # Use the shared logic to test the connection
        success, error = test_email_connection_logic(email_address, password, email_type, server, port)

        if success:
            return jsonify(success=True)
        else:
            return jsonify(success=False, error=error)

    @app.route('/get_folder_counts/<int:email_account_id>', methods=['GET'])
    @login_required
    def get_folder_counts(email_account_id):
        email_account_id = int(email_account_id)
        folders = EmailFolder.query.filter_by(email_account_id=email_account_id).all()
        folder_data = []

        for folder in folders:
            email_count = folder.email_count
            found_count = Email.query.filter_by(email_account_id=email_account_id, email_folder_id=folder.id).count()

            folder_data.append({
                'folder_name': folder.folder_name,
                'email_count': email_count,
                'found_count': found_count
            })

        return jsonify({'folders': folder_data})

    @app.route('/check_scan_status/<int:email_account_id>', methods=['GET'])
    @login_required
    def check_scan_status(email_account_id):
        email_account_id = int(email_account_id)
        status = scan_status.get((current_user.id, email_account_id), 'stopped')
        return jsonify({'status': status})

    @app.route('/stop_scan/<int:email_account_id>', methods=['POST'])
    @login_required
    def stop_scan(email_account_id):
        email_account_id = int(email_account_id)
        scan_status[(current_user.id, email_account_id)] = 'stopping' # this triggers the email_processor.py to exit the while true loop
        return jsonify({'success': True})


    @app.route('/email_addresses')
    @login_required
    def email_addresses_view():
        email_account_id = request.args.get('email_account_id') or request.form.get('email_account_id')
        if email_account_id:
            try:
                email_account_id = int(email_account_id)
            except ValueError:
                flash('Invalid Account ID', 'danger')
                return redirect(url_for('some_default_view'))

        if not email_account_id:
            flash('Account ID is required', 'danger')
            return redirect(url_for('some_default_view'))

        try:
            # Fetch email addresses with their counts directly from the EmailAddress model
            email_addresses = EmailAddress.query.filter_by(email_account_id=email_account_id).order_by(EmailAddress.count.desc()).all()

            return render_template(
                'email_addresses.html',
                email_addresses=email_addresses,
                email_account_id=email_account_id
            )

        except Exception as e:
            flash(f'Error fetching email addresses: {str(e)}', 'danger')
            return render_template(
                'email_addresses.html',
                email_addresses=[],
                email_account_id=email_account_id
            )

    

    @app.route("/filters", methods=['GET', 'POST'])
    @login_required
    def filters_view():
        email_account_id = request.args.get('email_account_id') or request.json.get('email_account_id')
        if email_account_id:
            email_account_id = int(email_account_id)
        
        if not email_account_id:
            return jsonify(success=False, message='Account ID is required'), 400

        if request.method == 'POST':
            filter_word = request.json.get('filter', '')
            filter_action = request.json.get('action', 'include')

            # Determine the highest current order value
            max_order = db.session.query(db.func.max(Filter.order)).filter_by(user_id=current_user.id, email_account_id=email_account_id).scalar()
            new_order = (max_order or 0) + 1  # If max_order is None, start from 1

            # Create a new filter with the calculated order
            new_filter = Filter(user_id=current_user.id, email_account_id=email_account_id, filter=filter_word, action=filter_action, order=new_order)
            db.session.add(new_filter)
            db.session.commit()
            return jsonify(success=True, message='Filter added successfully')

        # Fetch existing filters for the user and email account, ordered by the 'order' column
        filters = Filter.query.filter_by(user_id=current_user.id, email_account_id=email_account_id).order_by(Filter.order).all()

        # Calculate unique email count for each filter
        for filter in filters:
            # Create a subquery to select distinct email IDs that match the filter text
            subquery = db.session.query(Email.id).filter(
                Email.email_account_id == email_account_id,
                Email.text_content.contains(filter.filter)
            ).distinct().subquery()

            # Count the distinct email IDs from the subquery
            filter.email_count = db.session.query(func.count(subquery.c.id)).scalar()

        return render_template('filters.html', filters=filters)

    @app.route("/ai_prompts", methods=['GET', 'POST'])
    @login_required
    def ai_prompts_view():
        if request.method == 'POST':
            data = request.get_json()  # Get JSON data from the request body
            email_account_id = int(data.get('email_account_id'))

            if not email_account_id:
                return jsonify(success=False, message='Account ID is required'), 400

            prompt_id = data.get('id')
            prompt_text = data.get('prompt_text')
            prompt_order = data.get('order')
            prompt_action = data.get('action')

            if prompt_id:
                # Update existing prompt
                ai_prompt = AIPrompt.query.get(prompt_id)
                if ai_prompt and ai_prompt.user_id == current_user.id and ai_prompt.email_account_id == email_account_id:
                    ai_prompt.prompt_text = prompt_text
                    ai_prompt.order = prompt_order
                    ai_prompt.action = prompt_action
            else:
                # Add new prompt
                new_prompt = AIPrompt(user_id=current_user.id, email_account_id=email_account_id, prompt_text=prompt_text, order=prompt_order, action=prompt_action)
                db.session.add(new_prompt)
            db.session.commit()
            return jsonify(success=True, message='AI Prompts updated successfully')

        # For GET requests, retrieve prompts for the given email_account_id
        email_account_id = request.args.get('email_account_id')
        if email_account_id:
            email_account_id = int(email_account_id)
        ai_prompts = AIPrompt.query.filter_by(user_id=current_user.id, email_account_id=email_account_id).order_by(AIPrompt.order).all()
        return render_template('ai_prompts.html', ai_prompts=ai_prompts)

    @app.route("/dates", methods=['GET', 'POST'])
    @login_required
    def dates_view():
        email_account_id = request.args.get('email_account_id') or request.form.get('email_account_id')
        if email_account_id:
            email_account_id = int(email_account_id)
        account = EmailAccount.query.filter_by(id=email_account_id, user_id=current_user.id).first_or_404()

        if request.method == 'POST':
            start_date = request.form.get('start_date', '')
            end_date = request.form.get('end_date', '')

            # Check if limitDates is checked
            limit_dates_checked = request.form.get('limit_dates') == 'on'

            if limit_dates_checked:
                # Parse dates only if limitDates is checked
                account.start_date = datetime.strptime(start_date, '%Y-%m-%d') if start_date else None
                account.end_date = datetime.strptime(end_date, '%Y-%m-%d') if end_date else None
            else:
                # Set dates to None if limitDates is not checked
                account.start_date = None
                account.end_date = None

            db.session.commit()
            return jsonify(success=True, message='Dates updated successfully')

        start_date = account.start_date.strftime('%Y-%m-%d') if account.start_date else ''
        end_date = account.end_date.strftime('%Y-%m-%d') if account.end_date else ''
        return render_template('dates.html', start_date=start_date, end_date=end_date)

    @app.route("/download/<filename>")
    @login_required
    def download_file(filename):
        # Implement file download logic here
        return send_from_directory('results', filename, as_attachment=True)

    @app.route('/delete_filter/<int:filter_id>', methods=['POST'])
    @login_required
    def delete_filter(filter_id):
        # Fetch the filter by ID
        filter_to_delete = Filter.query.get_or_404(filter_id)
        
        # Ensure the filter belongs to the current user
        if filter_to_delete.user_id != current_user.id:
            return jsonify({'success': False, 'message': 'Unauthorized action'}), 403

        # Delete the filter
        db.session.delete(filter_to_delete)
        db.session.commit()

        return jsonify({'success': True, 'message': 'Filter deleted successfully'})

    @app.route('/filters/reorder', methods=['POST'])
    @login_required
    def reorder_filters():
        data = request.json
        items = data.get('items', [])

        try:
            for item in items:
                filter_id = item.get('id')
                new_order = item.get('order')

                # Fetch the filter by ID
                filter_to_update = Filter.query.get_or_404(filter_id)

                # Ensure the filter belongs to the current user
                if filter_to_update.user_id != current_user.id:
                    return jsonify({'success': False, 'message': 'Unauthorized action'}), 403

                # Update the order
                filter_to_update.order = new_order

            db.session.commit()
            return jsonify({'success': True, 'message': 'Filters reordered successfully'})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)}), 500

    @app.route('/prompts/reorder', methods=['POST'])
    @login_required
    def reorder_prompts():
        data = request.json
        items = data.get('items', [])

        try:
            for item in items:
                prompt_id = item.get('id')
                new_order = item.get('order')

                # Fetch the prompt by ID
                prompt_to_update = AIPrompt.query.get_or_404(prompt_id)

                # Ensure the prompt belongs to the current user
                if prompt_to_update.user_id != current_user.id:
                    return jsonify({'success': False, 'message': 'Unauthorized action'}), 403

                # Update the order
                prompt_to_update.order = new_order

            db.session.commit()
            return jsonify({'success': True, 'message': 'Prompts reordered successfully'})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': str(e)}), 500

    @app.route('/delete_prompt/<int:prompt_id>', methods=['POST'])
    @login_required
    def delete_prompt(prompt_id):
        # Fetch the prompt by ID
        prompt_to_delete = AIPrompt.query.get_or_404(prompt_id)
        
        # Ensure the prompt belongs to the current user
        if prompt_to_delete.user_id != current_user.id:
            return jsonify({'success': False, 'message': 'Unauthorized action'}), 403

        # Delete the prompt
        db.session.delete(prompt_to_delete)
        db.session.commit()

        return jsonify({'success': True, 'message': 'Prompt deleted successfully'})

    @app.route('/update_filter_action/<int:filter_id>', methods=['POST'])
    @login_required
    def update_filter_action(filter_id):
        data = request.json
        new_action = data.get('action')

        if new_action not in ['include', 'exclude']:
            return jsonify({'success': False, 'message': 'Invalid action'}), 400

        filter_to_update = Filter.query.get_or_404(filter_id)

        # Ensure the filter belongs to the current user
        if filter_to_update.user_id != current_user.id:
            return jsonify({'success': False, 'message': 'Unauthorized action'}), 403

        # Update the filter action
        filter_to_update.action = new_action
        db.session.commit()

        return jsonify({'success': True, 'message': 'Filter action updated successfully'})

    @app.route('/update_prompt_action/<int:prompt_id>', methods=['POST'])
    @login_required
    def update_prompt_action(prompt_id):
        data = request.json
        new_action = data.get('action')

        if new_action not in ['include', 'exclude']:
            return jsonify({'success': False, 'message': 'Invalid action'}), 400

        prompt_to_update = AIPrompt.query.get_or_404(prompt_id)

        # Ensure the prompt belongs to the current user
        if prompt_to_update.user_id != current_user.id:
            return jsonify({'success': False, 'message': 'Unauthorized action'}), 403

        # Update the prompt action
        prompt_to_update.action = new_action
        db.session.commit()

        return jsonify({'success': True, 'message': 'Prompt action updated successfully'})

    @app.route('/delete_file/<string:file_key>', methods=['POST'])
    @login_required
    def delete_file(file_key):
        bucket_name = os.getenv('S3_BUCKET_NAME')
        if delete_file_from_s3(bucket_name, file_key):
            # Find and delete the corresponding result entry
            result = Result.query.filter_by(user_id=current_user.id, name=file_key).first()
            if result:
                db.session.delete(result)
                db.session.commit()
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'message': 'Failed to delete file from S3'})
       
    @app.route('/check_processing_status/<int:email_account_id>', methods=['GET'])
    @login_required
    def check_processing_status(email_account_id):
        email_account_id = int(email_account_id)
        status = processing_status.get((current_user.id, email_account_id), 'stopped')
        return jsonify({'status': status})

    @app.route('/stop_processing/<int:email_account_id>', methods=['POST'])
    @login_required
    def stop_processing(email_account_id):
        email_account_id = int(email_account_id)
        stop(current_user.id, email_account_id)
        return jsonify({'success': True})


    @app.route('/get_email_ids_for_address/<int:email_address_id>', methods=['GET'])
    @login_required
    def get_emails_for_address(email_address_id):
        try:
            batch_number = int(request.args.get('batch', 1)) - 1  # Default to batch 1 if not provided
            batch_size = 20
            start = max(batch_number * batch_size, 0)  # Ensure start is never negative
            end = start + batch_size

            # Fetch the email address using the provided ID
            email_address = EmailAddress.query.get_or_404(email_address_id)
            email_str = email_address.email_address

            # Query emails where the email address is in the sender or receivers
            emails = Email.query.filter(
                or_(
                    Email.sender_id == email_address.id,
                    Email.receivers.any(EmailAddress.id == email_address.id)
                )
            ).slice(start, end).all()

            email_data = [{
                'id': email.id
            } for email in emails]

            total_emails = Email.query.filter(
                or_(
                    Email.sender_id == email_address.id,
                    Email.receivers.any(EmailAddress.id == email_address.id)
                )
            ).count()

            return jsonify(success=True, email_ids=email_data, total=total_emails)
        except Exception as e:
            return jsonify(success=False, message=str(e))

    @app.route('/get_email_data', methods=['POST'])
    @login_required
    def get_email_data():
        try:
            # Get the list of email IDs from the request
            email_ids_data = request.json.get('email_ids', [])
            if not email_ids_data:
                return jsonify(success=False, message='No email IDs provided'), 400

            # Extract the actual IDs
            email_ids = [email['id'] for email in email_ids_data]
            
            # Define batch size
            batch_size = 1000
            email_data = []

            # Process in batches
            for i in range(0, len(email_ids), batch_size):
                batch = email_ids[i:i + batch_size]
                emails = Email.query.filter(Email.id.in_(batch)).all()

                # Append results
                email_data.extend([{
                    'id': email.id,
                    'email_date': email.email_date.strftime('%Y-%m-%d %H:%M:%S') if email.email_date else '',
                    'sender': EmailAddress.query.get(email.sender_id).email_address if email.sender_id else '',
                    'receivers': [receiver.email_address for receiver in email.receivers],
                    'email_folder_id': email.email_folder_id,
                    'text_content': email.text_content
                } for email in emails])

            return jsonify(success=True, emails=email_data)
        except Exception as e:
            return jsonify(success=False, message=str(e)), 500


    @app.route('/get_email_ids_for_filter/<int:filter_id>', methods=['GET'])
    @login_required
    def get_emails_for_filter(filter_id):
        try:
            filter_obj = Filter.query.get_or_404(filter_id)
            if filter_obj.user_id != current_user.id:
                return jsonify({'success': False, 'message': 'Unauthorized action'}), 403

            email_ids = db.session.query(Email.id).filter(
                Email.email_account_id == filter_obj.email_account_id,
                Email.text_content.contains(filter_obj.filter)
            ).all()

            email_data = [{'id': id[0]} for id in email_ids]
            return jsonify(success=True, email_ids=email_data)
        except Exception as e:
            return jsonify(success=False, message=str(e))

    @app.route('/get_emails_for_address_modal/<address>')
    @login_required
    def get_emails_for_address_modal(address):
        try:
            # First find the EmailAddress record
            email_address = EmailAddress.query.filter_by(email_address=address).first()
            if not email_address:
                return jsonify(success=False, message='Email address not found'), 404

            # Get only email IDs where this address is either sender or receiver
            email_ids = db.session.query(Email.id).filter(
                or_(
                    Email.sender_id == email_address.id,
                    Email.receivers.any(EmailAddress.id == email_address.id)
                )
            ).all()

            # Convert list of tuples to list of dicts directly
            email_data = [{'id': id[0]} for id in email_ids]
            return jsonify(success=True, email_ids=email_data)
        except Exception as e:
            return jsonify(success=False, message=str(e)), 500
