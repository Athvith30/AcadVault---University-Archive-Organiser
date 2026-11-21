"""Small compatibility helpers for the original AcadVault Jinja views.

Keeps the view functions and HTML templates readable while FastAPI serves HTTP.
"""
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

_here = Path(__file__).resolve().parent
_current_request = ContextVar('acadvault_request')
_templates = Jinja2Templates(directory=str(_here / 'templates'))

class RequestProxy:
    @property
    def endpoint(self):
        route = _current_request.get().scope.get("route")
        return route.name if route else None
    @property
    def method(self):
        return _current_request.get().method
    @property
    def args(self):
        return _current_request.get().query_params
    @property
    def form(self):
        return _current_request.get().state.form
    @property
    def files(self):
        return _current_request.get().state.files
    @property
    def headers(self):
        return _current_request.get().headers

class SessionProxy:
    def _data(self):
        return _current_request.get().session
    def get(self, key, default=None):
        return self._data().get(key, default)
    def clear(self):
        self._data().clear()
    def __getitem__(self, key):
        return self._data()[key]
    def __setitem__(self, key, value):
        self._data()[key] = value
    def __contains__(self, key):
        return key in self._data()

request = RequestProxy()
session = SessionProxy()

def flash(message, category='message'):
    session._data().setdefault('_flashes', []).append((category, message))

def get_flashed_messages(with_categories=False):
    messages = session._data().pop('_flashes', [])
    return messages if with_categories else [message for _, message in messages]

def url_for(endpoint, **values):
    req = _current_request.get()
    if endpoint == 'static':
        return str(req.url_for('static', path=values.pop('filename')))
    path = str(req.url_for(endpoint))
    return path + ('?' + urlencode(values) if values else '')

def redirect(location):
    return RedirectResponse(location, status_code=303)

def render_template(filename, **context):
    req = _current_request.get()
    req.endpoint = request.endpoint
    req.args = req.query_params
    return _templates.TemplateResponse(req, filename, context)

class UploadCompat:
    def __init__(self, item):
        self.filename = item.filename
        self.stream = item.file

class AcadVaultApp(FastAPI):
    def __init__(self, secret_key):
        super().__init__()
        self.add_middleware(SessionMiddleware, secret_key=secret_key, same_site='lax', https_only=False)
        self.mount('/static', StaticFiles(directory=str(_here / 'static')), name='static')
        self.jinja_env = _templates.env
        self.jinja_env.globals.update({
            'url_for': url_for,
            'get_flashed_messages': get_flashed_messages,
            'session': session,
        })

        @self.middleware('http')
        async def with_request_context(req: Request, call_next):
            token = _current_request.set(req)
            try:
                if req.method in ('POST', 'PUT', 'PATCH'):
                    form = await req.form(max_files=10, max_fields=1000)
                    req.state.form = form
                    req.state.files = {key: UploadCompat(value) for key, value in form.multi_items()
                                       if hasattr(value, 'filename') and hasattr(value, 'file')}
                else:
                    req.state.form = {}
                    req.state.files = {}
                return await call_next(req)
            finally:
                _current_request.reset(token)

    def route(self, path, methods=None, **kwargs):
        methods = methods or ['GET']
        def decorator(fn):
            # The original views use the request proxy; async endpoints preserve
            # the request ContextVar and call the existing synchronous view.
            async def endpoint():
                return fn()
            endpoint.__name__ = fn.__name__
            self.add_api_route(path, endpoint, methods=methods, name=fn.__name__, **kwargs)
            return fn
        return decorator
