"""Small integration helpers shared by Shadow applications."""

from .agent import AgentAuthenticator, AgentAuthError, AgentIdentity
from .catalog import AppDescriptor, CatalogError, load_app_catalog
from .identity import VerifiedIdentity
from .llm import LLMConfigError, ResolvedLLMConfig, resolve_llm_config
from .llm_client import (
    AsyncLLMClient,
    JsonlUsageSink,
    LLMClient,
    LLMRequestError,
    LLMStreamEvent,
    LLMUsageEvent,
    NullUsageSink,
    RetryPolicy,
    UsageSink,
)
from .media import MediaClient, MediaClientError
from .service_auth import ServiceAuthError, hash_service_token, load_service_token_hashes

__all__ = [
    "AgentAuthError",
    "AgentAuthenticator",
    "AgentIdentity",
    "AppDescriptor",
    "AsyncLLMClient",
    "CatalogError",
    "JsonlUsageSink",
    "LLMClient",
    "LLMConfigError",
    "LLMRequestError",
    "LLMStreamEvent",
    "LLMUsageEvent",
    "MediaClient",
    "MediaClientError",
    "NullUsageSink",
    "ResolvedLLMConfig",
    "RetryPolicy",
    "ServiceAuthError",
    "UsageSink",
    "VerifiedIdentity",
    "resolve_llm_config",
    "load_app_catalog",
    "hash_service_token",
    "load_service_token_hashes",
]
