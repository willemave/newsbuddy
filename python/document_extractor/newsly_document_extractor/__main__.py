"""Run the document extractor service."""

import uvicorn

from newsly_document_extractor.settings import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "newsly_document_extractor.api:app",
        host=settings.bind_host,
        port=settings.port,
        proxy_headers=False,
        server_header=False,
        access_log=True,
    )


if __name__ == "__main__":
    main()
