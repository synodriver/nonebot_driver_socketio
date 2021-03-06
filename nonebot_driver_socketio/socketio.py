"""
socket.io 驱动适配
================

后端使用方法请参考: `python-socketio 文档`_

.. python-socketio 文档:
    https://python-socketio.readthedocs.io/en/latest/server.html
"""

try:
    import ujson as json
except ImportError:
    import json
import asyncio
import logging
from typing import List, Optional, Callable

import uvicorn
from pydantic import BaseSettings
import socketio
from fastapi.responses import Response
from fastapi import status, Request, FastAPI, HTTPException
from starlette.websockets import WebSocketDisconnect, WebSocket as FastAPIWebSocket

from nonebot.log import logger
from nonebot.typing import overrides
from nonebot.utils import DataclassEncoder
from nonebot.exception import RequestDenied
from nonebot.config import Env, Config as NoneBotConfig
from nonebot.drivers import Driver as BaseDriver, WebSocket as BaseWebSocket


class FastapiConfig(BaseSettings):
    """
    FastAPI 驱动框架设置，详情参考 FastAPI 文档
    """
    fastapi_openapi_url: Optional[str] = None
    """
    :类型:

      ``Optional[str]``

    :说明:

      ``openapi.json`` 地址，默认为 ``None`` 即关闭
    """
    fastapi_docs_url: Optional[str] = None
    """
    :类型:

      ``Optional[str]``

    :说明:

      ``swagger`` 地址，默认为 ``None`` 即关闭
    """
    fastapi_redoc_url: Optional[str] = None
    """
    :类型:

      ``Optional[str]``

    :说明:

      ``redoc`` 地址，默认为 ``None`` 即关闭
    """
    fastapi_reload_dirs: List[str] = []
    """
    :类型:

      ``List[str]``

    :说明:

      ``debug`` 模式下重载监控文件夹列表，默认为 uvicorn 默认值
    """

    class Config:
        extra = "ignore"


class SocketIOConfig(BaseSettings):
    """
    Socket.io 驱动框架设置，详情参考 python-socketio 文档
    """
    static_files: Optional[dict] = None
    """
    :类型:

      ``Optional[dict]``

    :说明:

      ``静态文件`` 路径，默认为 ``None`` 即没有
    """
    socketio_path: Optional[str] = 'socket.io'
    """
    :类型:

      ``Optional[str]``

    :说明:

      ``socketio`` 路径，默认为 ``socket.io``
    """
    on_startup: Optional[Callable] = None
    """
    :类型:

      ``Optional[Callable]``

    :说明:

      启动回调函数，默认为 ``None`` 即没有
    """
    on_shutdown: Optional[Callable] = None
    """
    :类型:

      ``Optional[Callable]``

    :说明:

      关闭回调函数，默认为 ``None`` 即没有
    """

    class Config:
        extra = "ignore"


class Driver(BaseDriver):
    """
    Socket.io 驱动框架，兼容原Fastapi 驱动框架

    :Fastapi上报地址:

      * ``/{adapter name}/``: HTTP POST 上报
      * ``/{adapter name}/http/``: HTTP POST 上报
      * ``/{adapter name}/ws``: WebSocket 上报
      * ``/{adapter name}/ws/``: WebSocket 上报

    :Socket.io上报地址:
    """

    def __init__(self, env: Env, config: NoneBotConfig):
        super().__init__(env, config)

        self.fastapi_config = FastapiConfig(**config.dict())
        self.socketio_config = SocketIOConfig(**config.dict())
        self._fastapi_app = FastAPI(
            debug=config.debug,
            openapi_url=self.fastapi_config.fastapi_openapi_url,
            docs_url=self.fastapi_config.fastapi_docs_url,
            redoc_url=self.fastapi_config.fastapi_redoc_url,
        )

        self._fastapi_app.post("/{adapter}/")(self._handle_http)
        self._fastapi_app.post("/{adapter}/http")(self._handle_http)
        self._fastapi_app.websocket("/{adapter}/ws")(self._handle_ws_reverse)
        self._fastapi_app.websocket("/{adapter}/ws/")(self._handle_ws_reverse)

        sio = socketio.AsyncServer()
        sio.on('OnGroupMsgs', namespace='/')(self._handle_socketio_event)
        sio.on('OnFriendMsgs', namespace='/')(self._handle_socketio_event)
        sio.on('OnEvents', namespace='/')(self._handle_socketio_event)
        self._server_app = socketio.ASGIApp(sio, self._fastapi_app, **self.socketio_config.dict())

    @property
    @overrides(BaseDriver)
    def type(self) -> str:
        """驱动名称: ``socketio``"""
        return "socketio"

    @property
    @overrides(BaseDriver)
    def server_app(self) -> FastAPI:
        """``SocketIO APP`` 对象"""
        return self._server_app

    @property
    @overrides(BaseDriver)
    def asgi(self):
        """``SocketIO APP`` 对象"""
        return self._server_app

    @property
    def secondary_app(self):
        """``FastAPI APP`` 对象"""
        return self._fastapi_app

    @property
    @overrides(BaseDriver)
    def logger(self) -> logging.Logger:
        """socketio 使用的 logger"""
        return logging.getLogger("socketio")

    @overrides(BaseDriver)
    def on_startup(self, func: Callable) -> Callable:
        """参考文档: `Events <https://fastapi.tiangolo.com/advanced/events/#startup-event>`_"""
        return self.secondary_app.on_event("startup")(func)

    @overrides(BaseDriver)
    def on_shutdown(self, func: Callable) -> Callable:
        """参考文档: `Events <https://fastapi.tiangolo.com/advanced/events/#startup-event>`_"""
        return self.secondary_app.on_event("shutdown")(func)

    @overrides(BaseDriver)
    def run(self,
            host: Optional[str] = None,
            port: Optional[int] = None,
            *,
            app: Optional[str] = None,
            **kwargs):
        """使用 ``uvicorn`` 启动 FastAPI"""
        super().run(host, port, app, **kwargs)
        LOGGING_CONFIG = {
            "version": 1,
            "disable_existing_loggers": False,
            "handlers": {
                "default": {
                    "class": "nonebot.log.LoguruHandler",
                },
            },
            "loggers": {
                "uvicorn.error": {
                    "handlers": ["default"],
                    "level": "INFO"
                },
                "uvicorn.access": {
                    "handlers": ["default"],
                    "level": "INFO",
                },
            },
        }
        uvicorn.run(app or self.server_app,
                    host=host or str(self.config.host),
                    port=port or self.config.port,
                    reload=bool(app) and self.config.debug,
                    reload_dirs=self.fastapi_config.fastapi_reload_dirs or None,
                    debug=self.config.debug,
                    log_config=LOGGING_CONFIG,
                    **kwargs)

    @overrides(BaseDriver)
    async def _handle_http(self, adapter: str, request: Request):
        data = await request.body()
        data_dict = json.loads(data.decode())

        if not isinstance(data_dict, dict):
            logger.warning("Data received is invalid")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)

        if adapter not in self._adapters:
            logger.warning(
                f"Unknown adapter {adapter}. Please register the adapter before use."
            )
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="adapter not found")

        # 创建 Bot 对象
        BotClass = self._adapters[adapter]
        headers = dict(request.headers)
        try:
            x_self_id = await BotClass.check_permission(self, "http", headers,
                                                        data)
        except RequestDenied as e:
            raise HTTPException(status_code=e.status_code,
                                detail=e.reason) from None

        if x_self_id in self._clients:
            logger.warning("There's already a reverse websocket connection,"
                           "so the event may be handled twice.")

        bot = BotClass("http", x_self_id)

        asyncio.create_task(bot.handle_message(data_dict))
        return Response("", 204)

    @overrides(BaseDriver)
    async def _handle_ws_reverse(self, adapter: str,
                                 websocket: FastAPIWebSocket):
        ws = WebSocket(websocket)

        if adapter not in self._adapters:
            logger.warning(
                f"Unknown adapter {adapter}. Please register the adapter before use."
            )
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        # Create Bot Object
        BotClass = self._adapters[adapter]
        headers = dict(websocket.headers)
        try:
            x_self_id = await BotClass.check_permission(self, "websocket",
                                                        headers, None)
        except RequestDenied:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        if x_self_id in self._clients:
            logger.opt(colors=True).warning(
                "There's already a reverse websocket connection, "
                f"<y>{adapter.upper()} Bot {x_self_id}</y> ignored.")
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        bot = BotClass("websocket", x_self_id, websocket=ws)

        await ws.accept()
        logger.opt(colors=True).info(
            f"WebSocket Connection from <y>{adapter.upper()} "
            f"Bot {x_self_id}</y> Accepted!")

        self._bot_connect(bot)

        try:
            while not ws.closed:
                data = await ws.receive()

                if not data:
                    continue

                asyncio.create_task(bot.handle_message(data))
        finally:
            self._bot_disconnect(bot)

    async def _handle_socketio_event(self, event: dict):
        pass # todo 完成socketio的callack


class WebSocket(BaseWebSocket):

    def __init__(self, websocket: FastAPIWebSocket):
        super().__init__(websocket)
        self._closed = False

    @property
    @overrides(BaseWebSocket)
    def closed(self):
        return self._closed

    @overrides(BaseWebSocket)
    async def accept(self):
        await self.websocket.accept()
        self._closed = False

    @overrides(BaseWebSocket)
    async def close(self, code: int = status.WS_1000_NORMAL_CLOSURE):
        await self.websocket.close(code=code)
        self._closed = True

    @overrides(BaseWebSocket)
    async def receive(self) -> Optional[dict]:
        data = None
        try:
            data = await self.websocket.receive_json()
            if not isinstance(data, dict):
                data = None
                raise ValueError
        except ValueError:
            logger.warning("Received an invalid json message.")
        except WebSocketDisconnect:
            self._closed = True
            logger.error("WebSocket disconnected by peer.")

        return data

    @overrides(BaseWebSocket)
    async def send(self, data: dict) -> None:
        text = json.dumps(data, cls=DataclassEncoder)
        await self.websocket.send({"type": "websocket.send", "text": text})


'''
class SocketIOAdapter(BaseWebSocket):
    def __init__(self, websocket: socketio.AsyncServer):
        super().__init__(websocket)
        self._closed = False

    @property
    @overrides(BaseWebSocket)
    def closed(self):
        return self._closed

    @overrides(BaseWebSocket)
    async def accept(self):
        self._closed = False

    @overrides(BaseWebSocket)
    async def close(self, code: int = status.WS_1000_NORMAL_CLOSURE):
        await self.websocket.close(code=code)
        self._closed = True

    @overrides(BaseWebSocket)
    async def receive(self) -> Optional[dict]:
        data = None
        try:
            data = await self.websocket.receive_json()
            if not isinstance(data, dict):
                data = None
                raise ValueError
        except ValueError:
            logger.warning("Received an invalid json message.")
        except WebSocketDisconnect:
            self._closed = True
            logger.error("WebSocket disconnected by peer.")

        return data

    @overrides(BaseWebSocket)
    async def send(self, data: dict) -> None:
        text = json.dumps(data, cls=DataclassEncoder)
        await self.websocket.send({"type": "websocket.send", "text": text})
'''
