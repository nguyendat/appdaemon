import json
import threading
import uuid

from aiohttp import web
import ssl
import traceback

import appdaemon.utils as utils
from appdaemon.appdaemon import AppDaemon


app = web.Application()

class ADAPI():

    def __init__(self, ad: AppDaemon, loop, logging, **config):

        self.AD = ad
        self.logging = logging
        self.logger = ad.logging.get_child("_run_restapi")
        self.access = ad.logging.get_access()

        self.api_key = None
        self._process_arg("api_key", config)

        self.api_ssl_certificate = None
        self._process_arg("api_ssl_certificate", config)

        self.api_ssl_key = None
        self._process_arg("api_ssl_key", config)

        self.api_port = 0
        self._process_arg("api_port", config)

        self.endpoints = {}
        self.endpoints_lock = threading.RLock()

        try:
            self.setup_api()

            if self.api_ssl_certificate is not None and self.api_ssl_key is not None:
                context = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
                context.load_cert_chain(self.api_ssl_certificate, self.api_ssl_key)
            else:
                context = None

            handler = app.make_handler()

            f = loop.create_server(handler, "0.0.0.0", int(self.api_port), ssl=context)
            loop.create_task(f)
        except:
            self.logger.warning('-' * 60)
            self.logger.warning("Unexpected error in api thread")
            self.logger.warning('-' * 60)
            self.logger.warning(traceback.format_exc())
            self.logger.warning('-' * 60)

    def _process_arg(self, arg, kwargs):
        if kwargs:
            if arg in kwargs:
                setattr(self, arg, kwargs[arg])

    @staticmethod
    def get_response(code, error):
        res = "<html><head><title>{} {}</title></head><body><h1>{} {}</h1>Error in API Call</body></html>".format(code, error, code, error)
        return res

    async def call_api(self, request):

        code = 200
        ret = ""

        app = request.match_info.get('app')

        if self.api_key is not None:
            if (("x-ad-access" not in request.headers) or (request.headers["x-ad-access"] != self.api_key)) \
                    and (("api_password" not in request.query) or (request.query["api_password"] != self.api_key)):

                code = 401
                response = "Unauthorized"
                res = self.get_response(code, response)
                self.access.info("API Call to %s: status: %s %s", app, code, response)
                return web.Response(body=res, status=code)

        try:
            args = await request.json()
        except json.decoder.JSONDecodeError:
            code = 400
            response = "JSON Decode Error"
            res = self.get_response(code, response)
            self.logger.warning("API Call to %s: status: %s %s", app, code, response)
            return web.Response(body = res, status = code)

        try:
            ret, code = await self.AD.api.dispatch_app_by_name(app, args)
        except:
            self.logger.warning('-' * 60)
            self.logger.warning("Unexpected error during API call")
            self.logger.warning('-' * 60)
            self.logger.warning(traceback.format_exc())
            self.logger.warning('-' * 60)

        if code == 404:
            response = "App Not Found"
            res = self.get_response(code, response)
            self.access.info("API Call to %s: status: %s %s", app, code, response)
            return web.Response(body = res, status = code)

        response = "OK"
        res = self.get_response(code, response)
        self.access.info("API Call to %s: status: %s %s", app, code, response)

        return web.json_response(ret, status = code)

    # Routes, Status and Templates

    def setup_api(self):
        app.router.add_post('/api/appdaemon/{app}', self.call_api)


    def register_endpoint(self, cb, name):

        handle = uuid.uuid4()

        with self.endpoints_lock:
            if name not in self.endpoints:
                self.endpoints[name] = {}
            self.endpoints[name][handle] = {"callback": cb, "name": name}

        return handle

    def unregister_endpoint(self, handle, name):
        with self.endpoints_lock:
            if name in self.endpoints and handle in self.endpoints[name]:
                del self.endpoints[name][handle]


    async def dispatch_app_by_name(self, name, args):
        with self.endpoints_lock:
            callback = None
            for app in self.endpoints:
                for handle in self.endpoints[app]:
                    if self.endpoints[app][handle]["name"] == name:
                        callback = self.endpoints[app][handle]["callback"]
        if callback is not None:
            return await utils.run_in_executor(self.AD.loop, self.AD.executor, callback, args)
        else:
            return '', 404

    def term_object(self, name):
        with self.endpoints_lock:
            if name in self.endpoints:
                del self.endpoints[name]

