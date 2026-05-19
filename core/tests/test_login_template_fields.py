from pathlib import Path


def test_login_template_renders_email_hint_for_login_field():
    template_path = Path(__file__).resolve().parents[2] / "frontend/templates/account/login.html"
    template = template_path.read_text(encoding="utf-8")

    assert 'for="email"' in template
    assert 'placeholder="Email"' in template
    assert '>Username<' not in template
    assert 'placeholder="Username"' not in template
