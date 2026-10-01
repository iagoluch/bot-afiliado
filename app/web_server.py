from __future__ import annotations

import uvicorn

from app.config import Settings


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run("app.web:app", host=settings.web_bind_host, port=settings.web_bind_port)


if __name__ == "__main__":
    main()
