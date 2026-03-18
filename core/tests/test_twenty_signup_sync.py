from types import SimpleNamespace
from unittest.mock import patch

import requests

from core.twenty_signup_sync import (
    SOURCE_SYSTEM,
    TwentySignupSyncClient,
    _normalize_domain,
    sync_signup_project_to_twenty,
)


class FakeClient:
    def __init__(self, *, people=None, companies=None):
        self.people = people or []
        self.companies = companies or []
        self.created_person_payload = None
        self.updated_person_payload = None
        self.created_company_payload = None
        self.updated_company_payload = None

    def list_people(self):
        return list(self.people)

    def list_companies(self):
        return list(self.companies)

    def create_person(self, payload):
        self.created_person_payload = payload
        return {"id": "person-created"}

    def update_person(self, person_id, payload):
        self.updated_person_payload = (person_id, payload)
        return {"id": person_id}

    def create_company(self, payload):
        self.created_company_payload = payload
        return {"id": "company-created"}

    def update_company(self, company_id, payload):
        self.updated_company_payload = (company_id, payload)
        return {"id": company_id}


@patch("core.twenty_signup_sync.settings.TWENTY_CRM_BASE_URL", "https://crm.example.com")
@patch("core.twenty_signup_sync.settings.TWENTY_CRM_API_KEY", "token")
def test_signup_sync_creates_person_and_company_when_no_matches():
    user = SimpleNamespace(
        id=10,
        email="Warm.Lead@example.com",
        first_name="Warm",
        last_name="Lead",
        username="warm-lead",
    )
    project = SimpleNamespace(
        id=20,
        name="Warm Lead Co",
        url="https://www.warmlead.com",
    )

    client = FakeClient()
    result = sync_signup_project_to_twenty(user=user, project=project, client=client)

    assert result.status == "created"
    assert result.person_status == "created"
    assert result.company_status == "created"
    assert result.person_id == "person-created"
    assert result.company_id == "company-created"

    assert client.created_company_payload["sourceSystem"] == SOURCE_SYSTEM
    assert client.created_company_payload["sourceOrganizationId"] == str(project.id)
    assert client.created_company_payload["companyDomainNormalized"] == "warmlead.com"

    assert client.created_person_payload["sourceSystem"] == SOURCE_SYSTEM
    assert client.created_person_payload["sourcePersonId"] == str(user.id)
    assert client.created_person_payload["emailNormalized"] == "warm.lead@example.com"
    assert client.created_person_payload["readyToSend"] is False
    assert client.created_person_payload["leadTemperature"] == "WARM"


@patch("core.twenty_signup_sync.settings.TWENTY_CRM_BASE_URL", "https://crm.example.com")
@patch("core.twenty_signup_sync.settings.TWENTY_CRM_API_KEY", "token")
def test_signup_sync_updates_existing_records_and_preserves_do_not_contact():
    user = SimpleNamespace(
        id=11,
        email="found@example.com",
        first_name="Found",
        last_name="Person",
        username="found-person",
    )
    project = SimpleNamespace(
        id=21,
        name="Found Co",
        url="https://found.co",
    )

    client = FakeClient(
        companies=[
            {
                "id": "company-1",
                "sourceSystem": SOURCE_SYSTEM,
                "sourceOrganizationId": str(project.id),
                "companyDomainNormalized": "found.co",
            }
        ],
        people=[
            {
                "id": "person-1",
                "sourceSystem": SOURCE_SYSTEM,
                "sourcePersonId": str(user.id),
                "emailNormalized": "found@example.com",
                "doNotContact": True,
            }
        ],
    )

    result = sync_signup_project_to_twenty(user=user, project=project, client=client)

    assert result.status == "updated"
    assert result.company_status == "updated"
    assert result.person_status == "updated"

    assert client.created_company_payload is None
    assert client.created_person_payload is None

    assert client.updated_company_payload is not None
    assert client.updated_person_payload is not None

    _, person_payload = client.updated_person_payload
    assert person_payload["doNotContact"] is True


@patch("core.twenty_signup_sync.settings.TWENTY_CRM_BASE_URL", "")
@patch("core.twenty_signup_sync.settings.TWENTY_CRM_API_KEY", "")
def test_signup_sync_returns_skipped_when_twenty_not_configured():
    user = SimpleNamespace(
        id=12,
        email="nocrm@example.com",
        first_name="",
        last_name="",
        username="no-crm",
    )
    project = SimpleNamespace(
        id=22,
        name="No CRM",
        url="https://no-crm.example.com",
    )

    result = sync_signup_project_to_twenty(user=user, project=project, client=FakeClient())

    assert result.status == "skipped"
    assert result.error_code == "twenty_not_configured"


@patch("core.twenty_signup_sync.time.sleep")
@patch("core.twenty_signup_sync.requests.request")
def test_client_retries_transient_errors(mock_request, _mock_sleep):
    class _Resp:
        status_code = 200
        content = b'{"data": []}'

        @staticmethod
        def json():
            return {"data": []}

    mock_request.side_effect = [
        requests.RequestException("temporary network issue"),
        _Resp(),
    ]

    client = TwentySignupSyncClient(
        base_url="https://crm.example.com",
        api_key="token",
        timeout_seconds=5,
        max_retries=2,
    )

    payload = client._request_json("GET", "/rest/people")

    assert payload == {"data": []}
    assert mock_request.call_count == 2


def test_normalize_domain_handles_url_and_www_prefix():
    assert _normalize_domain("https://www.Example.com/path") == "example.com"
    assert _normalize_domain("sub.example.com") == "sub.example.com"
