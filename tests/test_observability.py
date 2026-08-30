"""What the service reports, and when it stops paying for reporting nobody collects.

The wiring itself lives in `edutap.observability_settings` and is tested there. What
these tests hold is this package's half of the contract: that the call happens at all,
that it happens before anything else resolves settings, and that instrumentation is
gated on there actually being a receiver.
"""

from importlib.metadata import version

from edutap.observability_settings import ObservabilitySettings

from edutap.wallet_apple_vas_web_service import standalone


def test_service_names_itself_by_its_distribution():
    """One spelling across a span, a log line and the installed distribution.

    In Loki `service_name` is the only indexed label; a name that disagrees with the
    distribution is one nobody thinks to select on.
    """
    assert standalone.SERVICE_NAME == "edutap.wallet_apple_vas_web_service"
    assert version(standalone.SERVICE_NAME) == standalone.__version__


def test_observability_is_installed_at_import():
    """The settings object exists before any request, not on first use.

    `install_observability` must run before the module resolves the settings the
    service needs -- `AppleWalletWebServiceSettings()` can raise on a malformed value,
    and that failure is exactly the one nobody would otherwise see.
    """
    assert standalone.observability is not None


def test_observability_reads_the_shared_edutap_prefix(monkeypatch):
    """`EDUTAP_`, not a private name.

    A deployment sets `EDUTAP_ENVIRONMENT` once for a whole stack and means the same
    thing in every eduTAP service. Reading it under a private name would make this
    service the one that quietly reports into the wrong environment.
    """
    monkeypatch.setenv("EDUTAP_ENVIRONMENT", "staging")
    assert ObservabilitySettings().environment == "staging"


def test_instrumentation_is_gated_on_there_being_a_receiver(monkeypatch):
    """Instrumentation costs something on every request; it is not installed blind.

    It patches the application whether or not anything collects what it produces, so
    the gate is what keeps an unmonitored deployment from paying for spans nobody
    reads.
    """
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert not standalone.exports_to_a_collector()

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.invalid:4318")
    assert standalone.exports_to_a_collector()

    monkeypatch.setenv("EDUTAP_TELEMETRY_ENABLED", "false")
    standalone.observability = ObservabilitySettings()
    assert not standalone.exports_to_a_collector()
