"""Official ChatGPT plan connection, isolated from Terra's deterministic assessment."""

from .auth import AuthError, AuthManager

__all__ = ["AuthError", "AuthManager"]
