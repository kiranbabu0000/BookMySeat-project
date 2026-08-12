from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm

class UserRegisterForm(UserCreationForm):
    email = forms.EmailField()

    class Meta:
        model = User
        fields = ['username', 'email', 'password1', 'password2']

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError(
                'This name is already registered. Please use a different name.'
            )
        return username

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                'An account with this email is already registered.'
            )
        return email

class UserUpdateForm(forms.ModelForm):
    first_name = forms.CharField(
        required=False,
        max_length=150,
        label='First name',
        widget=forms.TextInput(attrs={'placeholder': 'First name', 'maxlength': '150'}),
    )
    last_name = forms.CharField(
        required=False,
        max_length=150,
        label='Last name',
        widget=forms.TextInput(attrs={'placeholder': 'Last name', 'maxlength': '150'}),
    )
    email = forms.EmailField()

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email']

    def clean(self):
        cleaned = super().clean()
        first = (cleaned.get('first_name') or '').strip()
        last = (cleaned.get('last_name') or '').strip()
        if not (first or last):
            raise forms.ValidationError('Please provide your first or last name.')
        return cleaned

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        exists = (
            User.objects.filter(email__iexact=email)
            .exclude(pk=self.instance.pk)
            .exists()
        )
        if exists:
            raise forms.ValidationError(
                'An account with this email is already registered.'
            )
        return email

class ProfileUpdateForm(forms.ModelForm):
    class Meta:
        model = User  # If adding more profile fields, change to a Profile model
        fields = ['password']  # User can reset password
