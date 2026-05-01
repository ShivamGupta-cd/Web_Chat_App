from flask_wtf import FlaskForm
from wtforms import FileField, PasswordField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=30)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8, max=128)])
    submit = SubmitField("Log In")


class SignupForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=30)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8, max=128)])
    submit = SubmitField("Sign Up")


class MessageForm(FlaskForm):
    message = StringField("Message", validators=[DataRequired(), Length(max=500)])
    submit = SubmitField("Send")


class SearchForm(FlaskForm):
    q = StringField("q", validators=[Optional(), Length(max=120)])


class EditMessageForm(FlaskForm):
    message = StringField("Message", validators=[DataRequired(), Length(max=500)])


class ProfileForm(FlaskForm):
    display_name = StringField("Display Name", validators=[Optional(), Length(max=60)])
    bio = TextAreaField("Bio", validators=[Optional(), Length(max=300)])
    avatar = FileField("Avatar")


class PasswordResetRequestForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=30)])


class PasswordResetForm(FlaskForm):
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8, max=128)])
