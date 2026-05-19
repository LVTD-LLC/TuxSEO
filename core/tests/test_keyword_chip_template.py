from django.template.loader import render_to_string


def test_keyword_chip_renders_keyboard_and_tooltip_dismiss_actions():
    rendered_keyword_chip = render_to_string(
        "components/keyword_chip.html",
        {
            "keyword": "growth marketing",
            "project_id": 42,
            "keyword_in_use": False,
        },
    )

    assert "mouseenter->keyword-hover#mouseenter" in rendered_keyword_chip
    assert "mouseleave->keyword-hover#mouseleave" in rendered_keyword_chip
    assert "focus->keyword-hover#focus" in rendered_keyword_chip
    assert "blur->keyword-hover#blur" in rendered_keyword_chip
    assert "keydown->keyword-hover#keydown" in rendered_keyword_chip
    assert 'tabindex="0"' in rendered_keyword_chip
    assert 'role="button"' in rendered_keyword_chip


def test_keyword_chip_renders_in_use_styles_when_keyword_is_active():
    rendered_keyword_chip = render_to_string(
        "components/keyword_chip.html",
        {
            "keyword": "content strategy",
            "project_id": 42,
            "keyword_in_use": True,
        },
    )

    assert "text-green-700" in rendered_keyword_chip
    assert "bg-green-50" in rendered_keyword_chip
    assert "border-green-200" in rendered_keyword_chip
