from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, SelectField, BooleanField, TextAreaField, DateField
from wtforms.validators import DataRequired, Email, EqualTo, Optional


class CSRFTokenForm(FlaskForm):
    pass

class RegistrationForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Sign Up')

class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember = BooleanField('Remember Me')
    submit = SubmitField('Login')

class JobForm(FlaskForm):
    start_date = DateField('Start Date', validators=[DataRequired()])
    end_date = DateField('End Date', validators=[DataRequired()])
    tag = StringField('Tag', validators=[DataRequired()])
    prompt = TextAreaField('Prompt', validators=[DataRequired()])
    exclusion_words = StringField('Exclusion Words')
    inclusion_words = StringField('Inclusion Words')  # Add this line
    submit = SubmitField('Create Job')



class FilterForm(FlaskForm):
    criteria = StringField('Prompt Text', validators=[DataRequired()])
    tag = StringField('Tag', validators=[DataRequired()])
    submit = SubmitField('Save')

class ChatGPTForm(FlaskForm):
    chatgpt_api_key = StringField('ChatGPT API Key', validators=[DataRequired()])
    submit = SubmitField('Save')

class FiltersTagForm(FlaskForm):
    criteria = StringField('Criteria', validators=[DataRequired()])
    tag = StringField('Tag', validators=[DataRequired()])
    submit = SubmitField('Add')

class EmailAccountForm(FlaskForm):
    email_address = StringField('Email Address', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[Optional()])
    email_type = SelectField('Email Type', choices=[
        ('IMAP', 'IMAP'),
        ('POP3', 'POP3')
    ], validators=[DataRequired()])
    imap_server = StringField('Email Server', validators=[Optional()])
    imap_port = StringField('Email Port', validators=[Optional()])
    imap_use_ssl = BooleanField('Use SSL', default=True)
    submit = SubmitField('Save')