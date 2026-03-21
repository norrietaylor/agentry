"""Unit tests for T02.2: DNS filtering proxy.

All upstream DNS calls are mocked -- no network access is required.

Tests cover:
- build_allow_set() merges explicit domains with LLM provider domains.
- build_allow_set() always includes default LLM domains when provider is None.
- build_allow_set() normalises domains (lowercase, strip trailing dot).
- is_domain_allowed() returns True for exact matches.
- is_domain_allowed() returns True for subdomain matches.
- is_domain_allowed() returns False for unlisted domains.
- is_domain_allowed() returns False for empty domain strings.
- DNSFilteringProxy.resolve_query() returns (True, ip) for allowed domains.
- DNSFilteringProxy.resolve_query() returns (False, None) for blocked domains.
- DNSFilteringProxy.resolve_query() logs all queries in query_log.
- DNSFilteringProxy.allow_set includes LLM API domain automatically.
- DNSFilteringProxy with empty allow list still includes default LLM domains.
- DNSFilteringProxy.get_container_dns_config() returns correct Docker kwargs.
- DNSFilteringProxy.get_sidecar_container_config() returns correct Docker kwargs.
- PROVIDER_DOMAINS maps known providers to their API domains.
"""

from __future__ import annotations

from agentry.runners.dns_proxy import (
    DEFAULT_LLM_DOMAINS,
    PROVIDER_DOMAINS,
    DNSFilteringProxy,
    DNSProxyConfig,
    DNSQueryLog,
    build_allow_set,
    is_domain_allowed,
)

# ---------------------------------------------------------------------------
# build_allow_set
# ---------------------------------------------------------------------------


class TestBuildAllowSet:
    def test_includes_explicit_domains(self) -> None:
        result = build_allow_set(["example.com", "pypi.org"], provider="anthropic")
        assert "example.com" in result
        assert "pypi.org" in result

    def test_includes_anthropic_provider_domain(self) -> None:
        result = build_allow_set([], provider="anthropic")
        assert "api.anthropic.com" in result

    def test_includes_openai_provider_domain(self) -> None:
        result = build_allow_set([], provider="openai")
        assert "api.openai.com" in result

    def test_includes_default_domains_when_provider_is_none(self) -> None:
        result = build_allow_set([])
        for domain in DEFAULT_LLM_DOMAINS:
            assert domain in result

    def test_normalises_to_lowercase(self) -> None:
        result = build_allow_set(["Example.COM"])
        assert "example.com" in result

    def test_strips_trailing_dot(self) -> None:
        result = build_allow_set(["example.com."])
        assert "example.com" in result
        assert "example.com." not in result

    def test_unknown_provider_falls_back_to_defaults(self) -> None:
        result = build_allow_set([], provider="unknown-provider")
        for domain in DEFAULT_LLM_DOMAINS:
            assert domain in result

    def test_merges_explicit_and_provider_domains(self) -> None:
        result = build_allow_set(["custom.io"], provider="anthropic")
        assert "custom.io" in result
        assert "api.anthropic.com" in result

    def test_deduplicates_domains(self) -> None:
        result = build_allow_set(
            ["api.anthropic.com", "API.ANTHROPIC.COM"], provider="anthropic"
        )
        # Should contain only one entry for api.anthropic.com.
        assert result == build_allow_set(["api.anthropic.com"], provider="anthropic")


# ---------------------------------------------------------------------------
# is_domain_allowed
# ---------------------------------------------------------------------------


class TestIsDomainAllowed:
    def test_exact_match(self) -> None:
        allow_set = {"example.com", "api.anthropic.com"}
        assert is_domain_allowed("example.com", allow_set) is True

    def test_exact_match_case_insensitive(self) -> None:
        allow_set = {"example.com"}
        assert is_domain_allowed("EXAMPLE.COM", allow_set) is True

    def test_exact_match_with_trailing_dot(self) -> None:
        allow_set = {"example.com"}
        assert is_domain_allowed("example.com.", allow_set) is True

    def test_subdomain_match(self) -> None:
        allow_set = {"example.com"}
        assert is_domain_allowed("sub.example.com", allow_set) is True

    def test_deep_subdomain_match(self) -> None:
        allow_set = {"example.com"}
        assert is_domain_allowed("a.b.c.example.com", allow_set) is True

    def test_blocked_domain(self) -> None:
        allow_set = {"example.com"}
        assert is_domain_allowed("evil.com", allow_set) is False

    def test_partial_match_not_allowed(self) -> None:
        """Domains that share a suffix but are not subdomains should be blocked."""
        allow_set = {"example.com"}
        assert is_domain_allowed("notexample.com", allow_set) is False

    def test_empty_domain_blocked(self) -> None:
        allow_set = {"example.com"}
        assert is_domain_allowed("", allow_set) is False

    def test_empty_allow_set_blocks_all(self) -> None:
        assert is_domain_allowed("example.com", set()) is False

    def test_dot_only_blocked(self) -> None:
        allow_set = {"example.com"}
        assert is_domain_allowed(".", allow_set) is False


# ---------------------------------------------------------------------------
# DNSFilteringProxy -- resolve_query
# ---------------------------------------------------------------------------


class TestDNSFilteringProxyResolve:
    def _make_proxy(
        self,
        allowed: list[str] | None = None,
        provider: str | None = "anthropic",
        resolver_return: str = "1.2.3.4",
    ) -> DNSFilteringProxy:
        """Create a proxy with a mock upstream resolver."""
        return DNSFilteringProxy(
            allowed_domains=allowed or ["api.anthropic.com"],
            provider=provider,
            upstream_resolver=lambda _d, _q: resolver_return,
        )

    def test_allowed_domain_resolves(self) -> None:
        proxy = self._make_proxy(allowed=["example.com"])
        allowed, ip = proxy.resolve_query("example.com", "A")
        assert allowed is True
        assert ip == "1.2.3.4"

    def test_blocked_domain_returns_nxdomain(self) -> None:
        proxy = self._make_proxy(allowed=["example.com"])
        allowed, ip = proxy.resolve_query("evil.com", "A")
        assert allowed is False
        assert ip is None

    def test_llm_api_domain_always_allowed(self) -> None:
        proxy = self._make_proxy(allowed=[], provider="anthropic")
        allowed, ip = proxy.resolve_query("api.anthropic.com", "A")
        assert allowed is True
        assert ip == "1.2.3.4"

    def test_query_log_records_allowed(self) -> None:
        proxy = self._make_proxy(allowed=["example.com"])
        proxy.resolve_query("example.com", "A")
        assert len(proxy.query_log) == 1
        entry = proxy.query_log[0]
        assert entry.domain == "example.com"
        assert entry.query_type == "A"
        assert entry.allowed is True
        assert entry.timestamp  # non-empty

    def test_query_log_records_blocked(self) -> None:
        proxy = self._make_proxy(allowed=["example.com"])
        proxy.resolve_query("evil.com", "A")
        assert len(proxy.query_log) == 1
        entry = proxy.query_log[0]
        assert entry.domain == "evil.com"
        assert entry.allowed is False

    def test_multiple_queries_logged(self) -> None:
        proxy = self._make_proxy(allowed=["example.com"])
        proxy.resolve_query("example.com", "A")
        proxy.resolve_query("evil.com", "A")
        proxy.resolve_query("sub.example.com", "AAAA")
        assert len(proxy.query_log) == 3

    def test_subdomain_resolves_when_parent_allowed(self) -> None:
        proxy = self._make_proxy(allowed=["example.com"])
        allowed, ip = proxy.resolve_query("sub.example.com", "A")
        assert allowed is True

    def test_upstream_resolver_failure_returns_none(self) -> None:
        proxy = DNSFilteringProxy(
            allowed_domains=["example.com"],
            provider="anthropic",
            upstream_resolver=lambda _d, _q: None,
        )
        allowed, ip = proxy.resolve_query("example.com", "A")
        assert allowed is True
        assert ip is None


# ---------------------------------------------------------------------------
# DNSFilteringProxy -- allow_set property
# ---------------------------------------------------------------------------


class TestDNSFilteringProxyAllowSet:
    def test_allow_set_includes_configured_domains(self) -> None:
        proxy = DNSFilteringProxy(
            allowed_domains=["example.com", "pypi.org"],
            provider="anthropic",
            upstream_resolver=lambda _d, _q: None,
        )
        assert "example.com" in proxy.allow_set
        assert "pypi.org" in proxy.allow_set

    def test_allow_set_includes_llm_domain(self) -> None:
        proxy = DNSFilteringProxy(
            allowed_domains=[],
            provider="anthropic",
            upstream_resolver=lambda _d, _q: None,
        )
        assert "api.anthropic.com" in proxy.allow_set

    def test_empty_allow_list_still_has_default_llm_domains(self) -> None:
        proxy = DNSFilteringProxy(
            allowed_domains=[],
            provider=None,
            upstream_resolver=lambda _d, _q: None,
        )
        for domain in DEFAULT_LLM_DOMAINS:
            assert domain in proxy.allow_set

    def test_allow_set_is_copy(self) -> None:
        proxy = DNSFilteringProxy(
            allowed_domains=["example.com"],
            provider="anthropic",
            upstream_resolver=lambda _d, _q: None,
        )
        # Mutating the returned set should not affect the proxy.
        s = proxy.allow_set
        s.add("injected.com")
        assert "injected.com" not in proxy.allow_set


# ---------------------------------------------------------------------------
# DNSFilteringProxy -- config-based construction
# ---------------------------------------------------------------------------


class TestDNSFilteringProxyConfig:
    def test_config_based_construction(self) -> None:
        config = DNSProxyConfig(
            allowed_domains=["example.com"],
            upstream_dns="1.1.1.1",
            listen_port=5353,
            provider="openai",
        )
        proxy = DNSFilteringProxy(
            config=config,
            upstream_resolver=lambda _d, _q: "5.6.7.8",
        )
        assert "example.com" in proxy.allow_set
        assert "api.openai.com" in proxy.allow_set

    def test_config_overrides_keyword_args(self) -> None:
        config = DNSProxyConfig(
            allowed_domains=["from-config.com"],
            provider="anthropic",
        )
        proxy = DNSFilteringProxy(
            config=config,
            allowed_domains=["from-kwargs.com"],
            provider="openai",
            upstream_resolver=lambda _d, _q: None,
        )
        # Config should win.
        assert "from-config.com" in proxy.allow_set
        assert "api.anthropic.com" in proxy.allow_set


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------


class TestDockerHelpers:
    def test_get_container_dns_config(self) -> None:
        config = DNSFilteringProxy.get_container_dns_config("172.18.0.2")
        assert config["dns"] == ["172.18.0.2"]
        assert config["dns_search"] == []

    def test_get_sidecar_container_config_with_execution_id(self) -> None:
        config = DNSFilteringProxy.get_sidecar_container_config(
            image="python:3.12-slim",
            network_id="net123",
            execution_id="exec-abc",
        )
        assert config["name"] == "agentry-dns-exec-abc"
        assert config["network"] == "net123"
        assert config["detach"] is True
        assert config["labels"]["agentry.role"] == "dns-proxy"
        assert config["labels"]["agentry.execution_id"] == "exec-abc"
        assert config["labels"]["agentry.managed"] == "true"

    def test_get_sidecar_container_config_defaults(self) -> None:
        config = DNSFilteringProxy.get_sidecar_container_config()
        assert config["name"] == "agentry-dns"
        assert config["image"] == "python:3.12-slim"


# ---------------------------------------------------------------------------
# PROVIDER_DOMAINS
# ---------------------------------------------------------------------------


class TestProviderDomains:
    def test_anthropic_mapped(self) -> None:
        assert "anthropic" in PROVIDER_DOMAINS
        assert "api.anthropic.com" in PROVIDER_DOMAINS["anthropic"]

    def test_openai_mapped(self) -> None:
        assert "openai" in PROVIDER_DOMAINS
        assert "api.openai.com" in PROVIDER_DOMAINS["openai"]

    def test_default_llm_domains_not_empty(self) -> None:
        assert len(DEFAULT_LLM_DOMAINS) > 0


# ---------------------------------------------------------------------------
# DNSQueryLog dataclass
# ---------------------------------------------------------------------------


class TestDNSQueryLog:
    def test_fields(self) -> None:
        log = DNSQueryLog(
            domain="example.com",
            query_type="A",
            allowed=True,
            timestamp="2026-01-24T15:00:00Z",
        )
        assert log.domain == "example.com"
        assert log.query_type == "A"
        assert log.allowed is True
        assert log.timestamp == "2026-01-24T15:00:00Z"
