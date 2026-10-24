"""Run with PYTHONPATH=backend python -m terra_monitoring.demo_simulation."""
import argparse

import uvicorn

from .server import create_app


def main():
    parser = argparse.ArgumentParser(description="Offline local Terra station and grower demonstration")
    parser.add_argument("--host", choices=["127.0.0.1", "localhost"], default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8520)
    parser.add_argument("--state-dir")
    parser.add_argument("--static-dir")
    args = parser.parse_args()
    uvicorn.run(create_app(state_dir=args.state_dir, static_dir=args.static_dir), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
