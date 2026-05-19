from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone as dt_timezone
from typing import Any
from urllib.parse import urlparse

import requests
from django.conf import settings

from tuxseo.utils import get_tuxseo_logger

logger = get_tuxseo_logger(__name__)

SOURCE_SYSTEM = "tuxseo_signup"


@dataclass
class SignupSyncResult:
    status: str
    person_id: str | None = None
    company_id: str | None = None
    person_status: str | None = None
    company_status: str | None = None
    source_person_id: str | None = None
    source_organization_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TwentySignupSyncError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class TwentySignupSyncClient:
    def __init__(self, *, base_url: str, api_key: str, timeout_seconds: int, max_retries: int):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, max_retries)

    def list_people(self) -> list[dict[str, Any]]:
        return self._list_records("/rest/people")

    def list_companies(self) -> list[dict[str, Any]]:
        return self._list_records("/rest/companies")

    def create_person(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_json("POST", "/rest/people", json_payload=payload)

    def update_person(self, person_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_json("PATCH", f"/rest/people/{person_id}", json_payload=payload)

    def create_company(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_json("POST", "/rest/companies", json_payload=payload)

    def update_company(self, company_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_json("PATCH", f"/rest/companies/{company_id}", json_payload=payload)

    def _list_records(self, path: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        cursor: str | None = None
        max_pages = 50

        for _ in range(max_pages):
            params: dict[str, Any] = {"limit": 200}
            if cursor:
                params["cursor"] = cursor

            payload = self._request_json("GET", path, params=params)
            page_records = _extract_records(payload)
            records.extend(page_records)

            cursor = _next_cursor(payload)
            if not cursor:
                break

        return records

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = requests.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_payload,
                    timeout=self.timeout_seconds,
                )
            except requests.RequestException as error:
                last_error = error
                if attempt < self.max_retries:
                    time.sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                raise TwentySignupSyncError(
                    f"network_error:{error}",
                    retryable=True,
                ) from error

            if response.status_code in {429, 500, 502, 503, 504}:
                if attempt < self.max_retries:
                    time.sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                raise TwentySignupSyncError(
                    f"transient_http_error:{response.status_code}",
                    status_code=response.status_code,
                    retryable=True,
                )

            if response.status_code >= 400:
                message = _response_error_message(response)
                raise TwentySignupSyncError(
                    message,
                    status_code=response.status_code,
                    retryable=False,
                )

            if not response.content:
                return {}

            data = response.json()
            return data if isinstance(data, dict) else {"data": data}

        if last_error:
            raise TwentySignupSyncError(f"network_error:{last_error}", retryable=True)
        raise TwentySignupSyncError("unexpected_request_error", retryable=False)


def sync_signup_project_to_twenty(user, project, client: TwentySignupSyncClient | None = None) -> SignupSyncResult:
    base_url = getattr(settings, "TWENTY_CRM_BASE_URL", "")
    api_key = getattr(settings, "TWENTY_CRM_API_KEY", "")

    if not base_url or not api_key:
        return SignupSyncResult(
            status="skipped",
            source_person_id=str(getattr(user, "id", "") or ""),
            source_organization_id=str(getattr(project, "id", "") or ""),
            error_code="twenty_not_configured",
            error_message="TWENTY_CRM_BASE_URL or TWENTY_CRM_API_KEY is missing",
        )

    if client is None:
        client = TwentySignupSyncClient(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=getattr(settings, "TWENTY_SIGNUP_SYNC_TIMEOUT_SECONDS", 20),
            max_retries=getattr(settings, "TWENTY_SIGNUP_SYNC_MAX_RETRIES", 3),
        )

    source_person_id = str(user.id)
    source_organization_id = str(project.id)

    try:
        company_payload = _build_company_payload(project)
        company_id, company_status = _upsert_company(client, company_payload)

        people = client.list_people()
        person_payload = _build_person_payload(user=user, company_id=company_id)
        person_id, person_status = _upsert_person(client, people, person_payload)

        sync_status = "created" if "created" in {person_status, company_status} else "updated"
        result = SignupSyncResult(
            status=sync_status,
            person_id=person_id,
            company_id=company_id,
            person_status=person_status,
            company_status=company_status,
            source_person_id=source_person_id,
            source_organization_id=source_organization_id,
        )

        logger.info(
            "[Twenty Signup Sync] Success",
            sync_event="twenty_signup_sync",
            status=sync_status,
            event_name="twenty_signup_sync",
            personId=person_id,
            companyId=company_id,
            sourcePersonId=source_person_id,
            sourceOrganizationId=source_organization_id,
            person_id=person_id,
            company_id=company_id,
            source_person_id=source_person_id,
            source_organization_id=source_organization_id,
        )

        return result
    except TwentySignupSyncError as error:
        logger.error(
            "[Twenty Signup Sync] Failed",
            sync_event="twenty_signup_sync",
            status="failed",
            event_name="twenty_signup_sync",
            person_id=None,
            company_id=None,
            sourcePersonId=source_person_id,
            sourceOrganizationId=source_organization_id,
            source_person_id=source_person_id,
            source_organization_id=source_organization_id,
            error_code=f"http_{error.status_code}" if error.status_code else "sync_error",
            error_message=str(error),
        )
        return SignupSyncResult(
            status="failed",
            source_person_id=source_person_id,
            source_organization_id=source_organization_id,
            error_code=f"http_{error.status_code}" if error.status_code else "sync_error",
            error_message=str(error),
        )


def _upsert_company(
    client: TwentySignupSyncClient,
    company_payload: dict[str, Any],
) -> tuple[str, str]:
    companies = client.list_companies()

    existing = _find_company_match(
        companies=companies,
        source_organization_id=str(company_payload.get("sourceOrganizationId") or ""),
        domain_normalized=company_payload.get("companyDomainNormalized", ""),
    )

    if existing:
        company_id = str(existing.get("id"))
        response = client.update_company(company_id, company_payload)
        return _extract_id(response, fallback=company_id), "updated"

    try:
        response = client.create_company(company_payload)
        return _extract_id(response), "created"
    except TwentySignupSyncError as error:
        if error.status_code == 409:
            companies = client.list_companies()
            existing = _find_company_match(
                companies=companies,
                source_organization_id=str(company_payload.get("sourceOrganizationId") or ""),
                domain_normalized=company_payload.get("companyDomainNormalized", ""),
            )
            if existing:
                company_id = str(existing.get("id"))
                response = client.update_company(company_id, company_payload)
                return _extract_id(response, fallback=company_id), "updated"
        raise


def _upsert_person(
    client: TwentySignupSyncClient,
    people: list[dict[str, Any]],
    person_payload: dict[str, Any],
) -> tuple[str, str]:
    existing = _find_person_match(
        people=people,
        source_person_id=str(person_payload.get("sourcePersonId") or ""),
        email_normalized=person_payload.get("emailNormalized", ""),
    )

    if existing:
        person_id = str(existing.get("id"))
        if existing.get("doNotContact") is True:
            person_payload["doNotContact"] = True
        response = _safe_person_upsert(client.update_person, person_id, person_payload)
        return _extract_id(response, fallback=person_id), "updated"

    try:
        response = _safe_person_upsert(client.create_person, None, person_payload)
        return _extract_id(response), "created"
    except TwentySignupSyncError as error:
        if error.status_code == 409:
            people = client.list_people()
            existing = _find_person_match(
                people=people,
                source_person_id=str(person_payload.get("sourcePersonId") or ""),
                email_normalized=person_payload.get("emailNormalized", ""),
            )
            if existing:
                person_id = str(existing.get("id"))
                if existing.get("doNotContact") is True:
                    person_payload["doNotContact"] = True
                response = _safe_person_upsert(client.update_person, person_id, person_payload)
                return _extract_id(response, fallback=person_id), "updated"
        raise


def _safe_person_upsert(callable_fn, person_id: str | None, person_payload: dict[str, Any]):
    try:
        if person_id is None:
            return callable_fn(person_payload)
        return callable_fn(person_id, person_payload)
    except TwentySignupSyncError as error:
        if error.status_code == 400 and "companyId" in person_payload:
            person_payload_without_company = dict(person_payload)
            person_payload_without_company.pop("companyId", None)
            if person_id is None:
                return callable_fn(person_payload_without_company)
            return callable_fn(person_id, person_payload_without_company)
        raise


def _find_company_match(
    *,
    companies: list[dict[str, Any]],
    source_organization_id: str,
    domain_normalized: str,
) -> dict[str, Any] | None:
    for company in companies:
        if (
            str(company.get("sourceOrganizationId") or "") == source_organization_id
            and str(company.get("sourceSystem") or "") == SOURCE_SYSTEM
        ):
            return company

    if not domain_normalized:
        return None

    for company in companies:
        if _normalize_domain(company.get("companyDomainNormalized")) == domain_normalized:
            return company

    return None


def _find_person_match(
    *,
    people: list[dict[str, Any]],
    source_person_id: str,
    email_normalized: str,
) -> dict[str, Any] | None:
    for person in people:
        if (
            str(person.get("sourcePersonId") or "") == source_person_id
            and str(person.get("sourceSystem") or "") == SOURCE_SYSTEM
        ):
            return person

    for person in people:
        if _normalize_email(person.get("emailNormalized")) == email_normalized:
            return person

        emails = person.get("emails") or {}
        if isinstance(emails, dict) and _normalize_email(emails.get("primaryEmail")) == email_normalized:
            return person

    return None


def _build_person_payload(*, user, company_id: str) -> dict[str, Any]:
    email_normalized = _normalize_email(user.email)
    first_name, last_name = _split_name(user)

    payload: dict[str, Any] = {
        "sourcePersonId": str(user.id),
        "sourceSystem": SOURCE_SYSTEM,
        "emailNormalized": email_normalized,
        "emails": {"primaryEmail": email_normalized},
        "name": {
            "firstName": first_name,
            "lastName": last_name,
        },
        "leadTemperature": "WARM",
        "automationTrack": "PRODUCT_AUTO",
        "outreachStatus": "NEW",
        "readyToSend": False,
        "outreachStatusReason": "product_signup_flow",
        "lastEnrichmentSyncAt": datetime.now(dt_timezone.utc).isoformat(),
        "doNotContact": False,
    }

    city = _normalize_text(getattr(user, "city", ""))
    if city:
        payload["city"] = city


    if company_id:
        payload["companyId"] = company_id

    return payload


def _build_company_payload(project) -> dict[str, Any]:
    domain_normalized = _normalize_domain(project.url)
    project_url = (project.url or "").strip()

    return {
        "sourceOrganizationId": str(project.id),
        "sourceSystem": SOURCE_SYSTEM,
        "companyDomainNormalized": domain_normalized,
        "name": (project.name or domain_normalized or f"Project {project.id}")[:255],
        "domainName": {"primaryLinkUrl": project_url},
        "icpCohort": "signup_product",
        "warmupStatus": "SKIP_WARMUP",
        "outreachStage": "PRODUCT_SIGNUP",
    }


def _split_name(user) -> tuple[str, str]:
    first_name = (getattr(user, "first_name", "") or "").strip()
    last_name = (getattr(user, "last_name", "") or "").strip()

    if first_name or last_name:
        return first_name or "User", last_name

    username = (getattr(user, "username", "") or "").strip()
    if not username:
        return "User", ""

    parts = [part for part in username.replace("_", " ").replace("-", " ").split() if part]
    if not parts:
        return "User", ""
    return parts[0].capitalize(), " ".join(part.capitalize() for part in parts[1:])


def _normalize_email(email: Any) -> str:
    return str(email or "").strip().lower()


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_domain(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""

    if "//" in text:
        parsed = urlparse(text)
        text = parsed.netloc or ""

    text = text.split("/")[0].split(":")[0].strip()
    if text.startswith("www."):
        text = text[4:]

    return text


def _extract_records(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    direct_candidates = [
        payload.get("results"),
        payload.get("items"),
        payload.get("records"),
        payload.get("data"),
        payload.get("people"),
        payload.get("companies"),
    ]
    for candidate in direct_candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]

    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("people", "companies", "items", "records", "results", "data"):
            candidate = data.get(key)
            if isinstance(candidate, list):
                return [item for item in candidate if isinstance(item, dict)]

    return []


def _next_cursor(payload: dict[str, Any]) -> str | None:
    page_info = payload.get("pageInfo")
    if not isinstance(page_info, dict):
        data = payload.get("data")
        page_info = data.get("pageInfo") if isinstance(data, dict) else {}

    if isinstance(page_info, dict):
        for key in ("nextCursor", "next_cursor", "endCursor", "end_cursor", "cursor"):
            cursor = page_info.get(key)
            if isinstance(cursor, str) and cursor:
                return cursor

    for key in ("nextCursor", "next_cursor", "endCursor", "end_cursor", "cursor"):
        cursor = payload.get(key)
        if isinstance(cursor, str) and cursor:
            return cursor

    return None


def _extract_id(payload: dict[str, Any], fallback: str | None = None) -> str:
    if isinstance(payload.get("id"), (str, int)):
        return str(payload["id"])

    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("id"), (str, int)):
        return str(data["id"])

    if fallback:
        return fallback

    raise TwentySignupSyncError("missing_record_id")


def _response_error_message(response) -> str:
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001
        payload = None

    if isinstance(payload, dict):
        for key in ("message", "error", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value

    return f"http_error:{response.status_code}"
