import re

from core.utils import generate_random_key


API_KEY_PATTERN = re.compile(r"^tuxseo_[0-9a-f]{40}$")


def test_generate_random_key_uses_secure_prefixed_format():
    key = generate_random_key()

    assert API_KEY_PATTERN.fullmatch(key)
    assert len(key) == 47
