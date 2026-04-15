import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
import uvicorn


from app.config import get_settings


if __name__ == '__main__':
    settings = get_settings()
    uvicorn.run('app.api.main:app', host=settings.api_host, port=settings.api_port, reload=False)
