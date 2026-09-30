from .base import *  # noqa: F401,F403
from .base import LOGGING

DEBUG = True
LOGGING["formatters"]["json"] = {"format": "%(levelname)s %(name)s [%(request_id)s] %(message)s"}
