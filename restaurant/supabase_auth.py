import json
import urllib.request
import urllib.error
import urllib.parse
from django.conf import settings


def _get_headers():
    anon_key = getattr(settings, "SUPABASE_ANON_KEY", "")
    return {
        "apikey": anon_key,
        "Authorization": f"Bearer {anon_key}",
        "Content-Type": "application/json",
    }


def supabase_sign_up(email, password, name, phone_number, redirect_to=None):
    """
    Register a customer using official Supabase GoTrue Auth API v1.
    Supabase sends an email confirmation link if 'Confirm email' is enabled in the Supabase project.
    """
    base_url = getattr(settings, "SUPABASE_URL", "").rstrip("/")
    anon_key = getattr(settings, "SUPABASE_ANON_KEY", "")

    if not base_url or not anon_key:
        return False, None, "Supabase authentication is not configured in settings (SUPABASE_URL or SUPABASE_ANON_KEY missing)."

    url = f"{base_url}/auth/v1/signup"
    if redirect_to:
        url = f"{url}?redirect_to={urllib.parse.quote(redirect_to)}"

    payload = {
        "email": email.strip().lower(),
        "password": password,
        "data": {
            "name": name.strip(),
            "phone_number": phone_number.strip(),
        },
    }
    if redirect_to:
        payload["options"] = {"emailRedirectTo": redirect_to}

    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data_bytes, headers=_get_headers(), method="POST")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return True, data, None
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            err_data = json.loads(error_body)
            raw_msg = (
                err_data.get("msg")
                or err_data.get("message")
                or err_data.get("error_description")
                or f"Sign up failed (HTTP {e.code})."
            )
            err_code = (err_data.get("error_code") or err_data.get("code") or "").lower()
            if "error sending confirmation email" in raw_msg.lower() or err_code == "unexpected_failure":
                msg = (
                    "Error sending confirmation email: Supabase could not deliver the confirmation email. "
                    "This occurs when custom SMTP credentials in the Supabase Dashboard (Authentication -> SMTP Provider) "
                    "are rejected or timeout (e.g. Gmail requires a 16-character Google App Password, port 587 TLS, and matching sender address), "
                    "or when the Supabase default email rate limit is exceeded."
                )
            else:
                msg = raw_msg
        except Exception:
            msg = f"HTTP Error {e.code}: {e.reason}"
        return False, None, msg
    except urllib.error.URLError as e:
        if "timed out" in str(e).lower():
            return (
                False,
                None,
                "The authentication request to Supabase timed out while attempting to dispatch the confirmation email. "
                "Please verify your SMTP settings in the Supabase Project Dashboard.",
            )
        return False, None, str(e)
    except Exception as e:
        return False, None, str(e)


def supabase_sign_in(email, password):
    """
    Authenticate user using Supabase GoTrue Auth API v1 (token?grant_type=password).
    Returns:
      (True, data, None) on successful login
      (False, 'email_not_confirmed', msg) if email confirmation is required
      (False, 'invalid_credentials', msg) if wrong credentials
      (False, 'error', msg) for other errors
    """
    base_url = getattr(settings, "SUPABASE_URL", "").rstrip("/")
    anon_key = getattr(settings, "SUPABASE_ANON_KEY", "")

    if not base_url or not anon_key:
        return False, "error", "Supabase authentication is not configured in settings."

    url = f"{base_url}/auth/v1/token?grant_type=password"
    payload = {
        "email": email.strip().lower(),
        "password": password,
    }
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data_bytes, headers=_get_headers(), method="POST")

    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return True, data, None
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            err_data = json.loads(error_body)
            desc = (err_data.get("error_description") or err_data.get("msg") or err_data.get("message") or "").lower()
            code = (err_data.get("error_code") or err_data.get("error") or "").lower()

            if "email not confirmed" in desc or code == "email_not_confirmed":
                return False, "email_not_confirmed", "Email not confirmed. Please check your inbox for the confirmation link sent by Supabase."
            if "invalid login credentials" in desc or "invalid" in desc or code == "invalid_grant":
                return False, "invalid_credentials", "Invalid email or password. Please try again."

            return False, "error", err_data.get("error_description") or err_data.get("msg") or "Authentication failed."
        except Exception:
            return False, "error", f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        return False, "error", str(e)


def supabase_resend_confirmation(email, redirect_to=None):
    """
    Trigger a new confirmation email from Supabase GoTrue Auth API v1 (/auth/v1/resend).
    """
    base_url = getattr(settings, "SUPABASE_URL", "").rstrip("/")
    anon_key = getattr(settings, "SUPABASE_ANON_KEY", "")

    if not base_url or not anon_key:
        return False, "Supabase authentication is not configured in settings."

    url = f"{base_url}/auth/v1/resend"
    if redirect_to:
        url = f"{url}?redirect_to={urllib.parse.quote(redirect_to)}"

    payload = {
        "type": "signup",
        "email": email.strip().lower(),
    }
    if redirect_to:
        payload["options"] = {"emailRedirectTo": redirect_to}

    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data_bytes, headers=_get_headers(), method="POST")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return True, None
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            err_data = json.loads(error_body)
            raw_msg = (
                err_data.get("msg")
                or err_data.get("message")
                or err_data.get("error_description")
                or "Resend failed."
            )
            err_code = (err_data.get("error_code") or err_data.get("code") or "").lower()
            if "error sending confirmation email" in raw_msg.lower() or err_code == "unexpected_failure":
                msg = (
                    "Error sending confirmation email: Supabase could not deliver the email. "
                    "Please check your Supabase SMTP settings or rate limits."
                )
            else:
                msg = raw_msg
        except Exception:
            msg = f"HTTP Error {e.code}"
        return False, msg
    except urllib.error.URLError as e:
        if "timed out" in str(e).lower():
            return False, "The resend request timed out while contacting the SMTP server. Please check your Supabase SMTP settings."
        return False, str(e)
    except Exception as e:
        return False, str(e)


def supabase_sign_out(access_token=None):
    """
    Sign out user from Supabase Auth (/auth/v1/logout).
    """
    base_url = getattr(settings, "SUPABASE_URL", "").rstrip("/")
    anon_key = getattr(settings, "SUPABASE_ANON_KEY", "")

    if not base_url or not anon_key:
        return

    url = f"{base_url}/auth/v1/logout"
    headers = _get_headers()
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    req = urllib.request.Request(url, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            pass
    except Exception:
        pass


def supabase_get_user(access_token):
    """
    Retrieves user object from Supabase Auth (/auth/v1/user) using their access token.
    """
    base_url = getattr(settings, "SUPABASE_URL", "").rstrip("/")
    anon_key = getattr(settings, "SUPABASE_ANON_KEY", "")

    if not base_url or not anon_key or not access_token:
        return None

    url = f"{base_url}/auth/v1/user"
    headers = {
        "apikey": anon_key,
        "Authorization": f"Bearer {access_token}",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
