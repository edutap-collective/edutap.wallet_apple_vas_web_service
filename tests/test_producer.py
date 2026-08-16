"""Fetching a built pass from the configured producer."""

import pytest

from edutap.wallet_apple_vas_web_service.config import AppleWalletWebServiceSettings
from edutap.wallet_apple_vas_web_service.producer import (
    PassNotAvailable,
    ProducerError,
    fetch_pass,
)

PTID = "pass.de.lmu.events"
SERIAL = "serial-one"
TEMPLATE = "https://builder.invalid/api/v1/passes/{pass_type_identifier}/{serial_number}"
EXPECTED_URL = f"https://builder.invalid/api/v1/passes/{PTID}/{SERIAL}"


@pytest.fixture
def settings():
    return AppleWalletWebServiceSettings(
        producer_pass_url_template=TEMPLATE,
        producer_api_token="a-producer-token",
    )


def test_the_template_names_the_pass(settings, requests_mock):
    requests_mock.get(EXPECTED_URL, content=b"PK\x03\x04")
    assert fetch_pass(settings, PTID, SERIAL) == b"PK\x03\x04"


def test_the_producer_token_is_sent(settings, requests_mock):
    requests_mock.get(EXPECTED_URL, content=b"PK\x03\x04")
    fetch_pass(settings, PTID, SERIAL)
    assert requests_mock.last_request.headers["Authorization"] == "Bearer a-producer-token"


@pytest.mark.parametrize("status", [404, 410])
def test_a_withdrawn_pass_raises_pass_not_available(settings, requests_mock, status):
    requests_mock.get(EXPECTED_URL, status_code=status)
    with pytest.raises(PassNotAvailable):
        fetch_pass(settings, PTID, SERIAL)


def test_a_failing_producer_raises_producer_error(settings, requests_mock):
    requests_mock.get(EXPECTED_URL, status_code=500)
    with pytest.raises(ProducerError):
        fetch_pass(settings, PTID, SERIAL)


def test_an_unconfigured_producer_raises_producer_error():
    with pytest.raises(ProducerError):
        fetch_pass(AppleWalletWebServiceSettings(), PTID, SERIAL)
