from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_socketio import SocketIO
from flask_wtf.csrf import CSRFProtect


csrf = CSRFProtect()
socketio = SocketIO(async_mode="threading", manage_session=False)
limiter = Limiter(key_func=get_remote_address)
