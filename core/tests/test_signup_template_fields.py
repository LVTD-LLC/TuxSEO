from pathlib import Path


def test_signup_template_renders_email_and_single_password_fields_only():
    template_path = Path(__file__).resolve().parents[2] / "frontend/templates/account/signup.html"
    template = template_path.read_text(encoding="utf-8")

    assert 'name="email"' in template
    assert 'name="password1"' in template
    assert 'name="username"' not in template
    assert 'name="password2"' not in template
