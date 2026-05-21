from flask import Flask, g

from app.api.auth_middleware import get_current_user_display_name


def test_get_current_user_display_name_prefers_middleware_user_data():
    app = Flask(__name__)

    with app.app_context():
        g.user_data = {
            "display_name": "Tech One",
            "email": "tech1@example.com",
        }

        assert get_current_user_display_name() == "Tech One"


def test_get_current_user_display_name_does_not_fallback_to_email():
    app = Flask(__name__)

    with app.app_context():
        g.user_data = {"email": "tech1@example.com"}

        assert get_current_user_display_name() == "system"
