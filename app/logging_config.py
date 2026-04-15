import logging.config

from app.config import Settings, get_settings


LOGGING_TEMPLATE = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'default': {
            'format': '%(asctime)s | %(levelname)s | %(name)s | %(message)s',
        }
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'default',
        }
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
}


def configure_logging(settings: Settings | None = None) -> None:
    current_settings = settings or get_settings()
    config = dict(LOGGING_TEMPLATE)
    config['root'] = dict(LOGGING_TEMPLATE['root'])
    config['root']['level'] = current_settings.log_level.upper()
    logging.config.dictConfig(config)
