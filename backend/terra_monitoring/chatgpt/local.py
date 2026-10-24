"""Mount optional ChatGPT controls without starting inference or a second worker."""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from pathlib import Path
import threading

from .api import create_router
from .auth import AuthManager
from .provider import ChatGPTPlanClient


def attach_controls(app, state_dir, *, auth=None, provider=None, preview_only=False):
    """Use the app's local guard and preserve one complete server lifespan.

    Shutdown permanently closes the OAuth manager. Create a new app instance to
    restart a server with these controls rather than reuse closed credentials.
    """
    root = Path(state_dir).expanduser().resolve()
    auth = auth if auth is not None else AuthManager(root / "auth")
    provider = provider if provider is not None else ChatGPTPlanClient(auth, root / "provider")
    app.state.chatgpt_auth = auth
    app.state.chatgpt_provider = provider
    # The parent app already has a defensive /api/{path} and static catch-all.
    # Exact guarded controls must be considered before those terminal routes.
    router = create_router(auth, provider, mutation_guard=app.state.local_mutation_guard, preview_only=preview_only)
    app.router.routes[:0] = router.routes
    original = app.router.lifespan_context
    used = False
    lifecycle_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(application):
        nonlocal used
        with lifecycle_lock:
            if used:
                raise RuntimeError("A ChatGPT-enabled app supports one server lifespan; create a new app instance to restart it.")
            used = True
        try:
            async with original(application):
                yield
        finally:
            auth.close()

    app.router.lifespan_context = lifespan
    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description="Isolated local ChatGPT connection preview; no monitor worker or SMS activation")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--chatgpt-state-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8517)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    from terra_monitoring.runtime import create_runtime_app
    from terra_monitoring.live import LiveQueryService
    from terra_monitoring.sms import SMSDispatcher
    import uvicorn
    service = LiveQueryService(app_key="", data_dir=args.state_dir.resolve() / "live-source")
    class PreviewSource:
        def context(self):
            return {**service.context(), "chatgpt_preview": True}
        def assess(self, query):
            return service.assess(query)
    sms = SMSDispatcher.from_env({"TERRA_SMS_ENABLED": "false", "TERRA_SMS_DEMO_ENABLED": "false",
                                  "TERRA_SMS_DB_PATH": str(args.state_dir.resolve() / "sms.sqlite3")})
    app = create_runtime_app(state_dir=args.state_dir.resolve(), live_service=PreviewSource(),
                             sms_dispatcher=sms, start_worker=False)
    attach_controls(app, args.chatgpt_state_dir.resolve(), preview_only=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
