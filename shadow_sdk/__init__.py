"""Small integration helpers shared by Shadow applications."""

from .agent import AgentAuthenticator, AgentAuthError, AgentIdentity
from .assets import AssetClient, AssetClientError
from .catalog import AppDescriptor, CatalogError, load_app_catalog
from .confirmation import (
    ConfirmationBinding,
    ConfirmationError,
    ConfirmationReplayStore,
    ConfirmationSigner,
    ConfirmationVerifier,
    VerifiedConfirmation,
    arguments_sha256,
    decode_confirmation_receipt,
    encode_confirmation_receipt,
    enforce_destructive_limits,
)
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
from .notifications import NotificationClient, NotificationClientError, NotificationResult
from .service_auth import ServiceAuthError, hash_service_token, load_service_token_hashes

__all__ = [
    "AgentAuthError",
    "AgentAuthenticator",
    "AgentIdentity",
    "AppDescriptor",
    "AssetClient",
    "AssetClientError",
    "AsyncLLMClient",
    "CatalogError",
    "ConfirmationBinding",
    "ConfirmationError",
    "ConfirmationReplayStore",
    "ConfirmationSigner",
    "ConfirmationVerifier",
    "JsonlUsageSink",
    "LLMClient",
    "LLMConfigError",
    "LLMRequestError",
    "LLMStreamEvent",
    "LLMUsageEvent",
    "MediaClient",
    "MediaClientError",
    "NullUsageSink",
    "NotificationClient",
    "NotificationClientError",
    "NotificationResult",
    "ResolvedLLMConfig",
    "RetryPolicy",
    "ServiceAuthError",
    "UsageSink",
    "VerifiedConfirmation",
    "VerifiedIdentity",
    "resolve_llm_config",
    "load_app_catalog",
    "hash_service_token",
    "load_service_token_hashes",
    "arguments_sha256",
    "decode_confirmation_receipt",
    "encode_confirmation_receipt",
    "enforce_destructive_limits",
]
