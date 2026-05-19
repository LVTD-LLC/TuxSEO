from unittest.mock import Mock, patch

import requests

from core.api.schemas import ValidateUrlIn
from core.api.views import validate_url


class TestValidateUrlEndpoint:
    def test_returns_error_when_url_is_empty(self):
        response = validate_url(request=None, data=ValidateUrlIn(url="   "))

        assert response["status"] == "error"
        assert response["reachable"] is False
        assert response["message"] == "URL cannot be empty"

    def test_returns_error_when_url_has_no_http_scheme(self):
        response = validate_url(request=None, data=ValidateUrlIn(url="example.com"))

        assert response["status"] == "error"
        assert response["reachable"] is False
        assert response["message"] == "URL must start with http:// or https://"

    @patch("core.api.views.requests.get")
    def test_returns_success_when_url_is_reachable(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        response = validate_url(request=None, data=ValidateUrlIn(url="https://example.com"))

        assert response["status"] == "success"
        assert response["reachable"] is True
        assert response["message"] == "URL is reachable"

    @patch("core.api.views.requests.get")
    def test_returns_unreachable_when_status_code_is_400_or_higher(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        response = validate_url(request=None, data=ValidateUrlIn(url="https://example.com"))

        assert response["status"] == "success"
        assert response["reachable"] is False
        assert response["message"] == "URL returned status code 404"

    @patch("core.api.views.requests.get", side_effect=requests.Timeout)
    def test_returns_timeout_error_when_request_times_out(self, mock_get):
        response = validate_url(request=None, data=ValidateUrlIn(url="https://example.com"))

        assert response["status"] == "error"
        assert response["reachable"] is False
        assert response["message"] == "Request timed out - website took too long to respond"

    @patch("core.api.views.requests.get", side_effect=requests.ConnectionError)
    def test_returns_connection_error_when_request_cannot_connect(self, mock_get):
        response = validate_url(request=None, data=ValidateUrlIn(url="https://example.com"))

        assert response["status"] == "error"
        assert response["reachable"] is False
        assert response["message"] == "Cannot connect to this URL"

    @patch("core.api.views.requests.get", side_effect=RuntimeError("unexpected"))
    def test_returns_generic_error_for_unexpected_exception(self, mock_get):
        response = validate_url(request=None, data=ValidateUrlIn(url="https://example.com"))

        assert response["status"] == "error"
        assert response["reachable"] is False
        assert response["message"] == "Could not validate URL"
