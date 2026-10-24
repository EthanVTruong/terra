"""Local, durable Terra conversation; response selection precedes inference."""

__all__ = ["ConversationService", "RequestInProgress"]


def __getattr__(name):
    if name in __all__:
        from .service import ConversationService, RequestInProgress
        return {"ConversationService": ConversationService, "RequestInProgress": RequestInProgress}[name]
    raise AttributeError(name)
