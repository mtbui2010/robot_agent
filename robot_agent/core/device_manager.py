import uuid, time, threading, json, importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional


def _resolve_dotted(path: str):
    """Resolve a dotted (or slashed) path to a Python object.

    Examples:
        'pyconnect.ros.utils.encode_imgmsg'   -> function
        'sensor_msgs/msg/Image'               -> class
        'rosinterfaces.srv.SendStringData'    -> class
    """
    parts = path.replace('/', '.').split('.')
    for i in range(len(parts) - 1, 0, -1):
        try:
            mod = importlib.import_module('.'.join(parts[:i]))
        except ImportError:
            continue
        obj = mod
        try:
            for p in parts[i:]:
                obj = getattr(obj, p)
            return obj
        except AttributeError:
            continue
    raise ImportError(f'Cannot resolve: {path}')


ConnectType = Literal['ros_service', 'ros_topic', 'ros_action', 'webrtc', 'llm',
                      'tcp', 'zmq', 'websocket', 'http', 'visionserve']


@dataclass
class ConnectEntry:
    id: str
    name: str
    type: ConnectType
    config: dict
    client: Any = field(default=None, repr=False)
    connected: bool = False
    error: str = ''
    last_checked: float = field(default_factory=time.time)
    is_camera: bool = False


class DeviceManager:
    def __init__(self, data_dir: Path, node_name: str = 'robot_agent'):
        self._data_dir = Path(data_dir)
        self._persist_file = self._data_dir / 'connections.json'
        self._node_name = node_name
        self._connects: dict[str, ConnectEntry] = {}
        self._ros_node = None
        self._lock = threading.Lock()
        self._loading = False  # suppresses _save() during load_saved()

    # ------------------------------------------------------------------
    # ROS2 node (lazy, shared across all ROS clients)
    # ------------------------------------------------------------------
    def _get_ros_node(self):
        if self._ros_node is None:
            from pyconnect.ros.custom_node import CustomNode
            self._ros_node = CustomNode(name=self._node_name, num_callbackgroup=4)
            self._ros_node.spin(run_thread=True)
        return self._ros_node

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def scan_ros(self) -> dict:
        from pyconnect.ros.utils import get_ros2_node_names_and_types
        self._get_ros_node()  # ensure node is spinning — required to see other nodes
        return get_ros2_node_names_and_types()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @staticmethod
    def _compile_run_func(config: dict):
        """Compile a server-side ``run_func`` from a source string in config.

        Shared by the tcp / zmq / websocket / http server branches. Returns
        ``None`` when no (valid) source is provided, so the server falls back
        to echoing the request.
        """
        code = config.get('run_func')
        if not (isinstance(code, str) and code.strip()):
            return None
        ns: dict = {}
        exec(code, ns)  # noqa: S102
        return ns.get('run_func') or next(
            (v for v in ns.values() if callable(v) and not isinstance(v, type)),
            None,
        )

    def add_connect(self, conn_type: ConnectType, name: str, config: dict) -> tuple[str, str]:
        agent_name = config.get('agent_name') or config.get('conn_name') or str(uuid.uuid4())[:8]
        entry = ConnectEntry(id=agent_name, name=name, type=conn_type, config=config)
        error = ''
        try:
            if 'ros' in conn_type:
                is_client = config.get('is_client', True)
                if is_client:
                    if 'topic' in conn_type:
                        from pyconnect.ros.utils import get_sub_configs
                        get_configs_func = get_sub_configs
                    elif 'action' in conn_type:
                        from pyconnect.ros.utils import get_action_client_configs
                        get_configs_func = get_action_client_configs
                    else:
                        from pyconnect.ros.utils import get_service_client_configs
                        get_configs_func = get_service_client_configs
                else:
                    if 'topic' in conn_type:
                        from pyconnect.ros.utils import get_pub_configs
                        get_configs_func = get_pub_configs
                    elif 'action' in conn_type:
                        from pyconnect.ros.utils import get_action_server_configs
                        get_configs_func = get_action_server_configs
                    else:
                        from pyconnect.ros.utils import get_service_server_configs
                        get_configs_func = get_service_server_configs

                # Defaults assume SendStringData (service/action) or std_msgs/String (topic):
                # data_interface, encode_func, decode_func are all pre-populated.
                ros_cfg = get_configs_func(agent_name=agent_name, conn_name=config['conn_name'])

                for key in ('data_interface', 'encode_func', 'decode_func'):
                    val = config.get(key)
                    if not val:
                        continue
                    if isinstance(val, str):
                        if key in ('encode_func', 'decode_func') and ('def ' in val or 'lambda ' in val):
                            ns: dict = {}
                            exec(val, ns)  # noqa: S102
                            ros_cfg[key] = ns.get(key) or next(
                                (v for v in ns.values() if callable(v) and not isinstance(v, type)),
                                None,
                            )
                        else:
                            ros_cfg[key] = _resolve_dotted(val)
                    else:
                        ros_cfg[key] = val

                node = self._get_ros_node()
                node.add_agent(**ros_cfg)
                entry.client = node.agents[agent_name]
                entry.connected = True
                entry.is_camera = bool(config.get('is_camera', False))

            elif conn_type == 'webrtc':
                import asyncio
                from pyconnect.webrtc.client import WebRTCClient
                try:
                    asyncio.get_event_loop()
                except RuntimeError:
                    asyncio.set_event_loop(asyncio.new_event_loop())
                client = WebRTCClient(host=config['host'], port=config.get('port', 8443))
                entry.is_camera = bool(config.get('is_camera', False))
                entry.client = client
                entry.connected = (
                    self._tcp_reachable(config['host'], config.get('port', 8443))
                    if entry.is_camera else client.connected
                )

            elif conn_type == 'llm':
                from pyconnect.utils import init_llm_client
                _INTERNAL_KEYS = {'agent_name', 'conn_name', 'conn_type'}
                llm_cfg = {k: v for k, v in config.items() if k not in _INTERNAL_KEYS}
                entry.client = init_llm_client(cfg=llm_cfg)
                entry.connected = True

            elif conn_type == 'tcp':
                host = config.get('host', 'localhost')
                port = int(config.get('port', 8888))
                if config.get('is_client', True):
                    from pyconnect.tcp_ip.client import TcpIpClient
                    client = TcpIpClient(host=host, port=port)
                    entry.client = client
                    entry.connected = client.server_connected
                else:
                    from pyconnect.tcp_ip.server import TcpIpServer
                    run_func = None
                    code = config.get('run_func')
                    if isinstance(code, str) and code.strip():
                        ns: dict = {}
                        exec(code, ns)  # noqa: S102
                        run_func = ns.get('run_func') or next(
                            (v for v in ns.values() if callable(v) and not isinstance(v, type)),
                            None,
                        )
                    server = TcpIpServer(host=host, port=port, run_func=run_func)
                    server.spin(run_thread=True)
                    entry.client = server
                    entry.connected = True

            elif conn_type == 'zmq':
                host = config.get('host', 'localhost')
                port = int(config.get('port', 8888))
                if config.get('is_client', True):
                    from pyconnect.zmq.client import ZmqClient
                    client = ZmqClient(host=host, port=port,
                                       timeout=config.get('timeout', 10000))
                    entry.client = client
                    entry.connected = client.server_connected
                else:
                    from pyconnect.zmq.server import ZmqServer
                    server = ZmqServer(host=host, port=port,
                                       run_func=self._compile_run_func(config))
                    server.spin(run_thread=True)
                    entry.client = server
                    entry.connected = True

            elif conn_type == 'websocket':
                host = config.get('host', 'localhost')
                port = int(config.get('port', 8888))
                if config.get('is_client', True):
                    from pyconnect.websocket.client import WebSocketClient
                    client = WebSocketClient(
                        host=host, port=port,
                        path=config.get('path', '/'),
                        secure=bool(config.get('secure', False)),
                        timeout=config.get('timeout', 10.0),
                    )
                    entry.client = client
                    entry.connected = client.server_connected
                else:
                    from pyconnect.websocket.server import WebSocketServer
                    server = WebSocketServer(host=host, port=port,
                                             run_func=self._compile_run_func(config))
                    server.spin(run_thread=True)
                    entry.client = server
                    entry.connected = True

            elif conn_type == 'http':
                if config.get('is_client', True):
                    from pyconnect.http.client import HttpClient
                    token = config.get('token') or (
                        __import__('os').getenv(config['token_env'])
                        if config.get('token_env') else None
                    )
                    client = HttpClient(
                        url=config.get('url', ''),
                        method=config.get('method', 'POST'),
                        headers=config.get('headers'),
                        token=token,
                        timeout=config.get('timeout', 30.0),
                    )
                    entry.client = client
                    entry.connected = client.server_connected
                else:
                    from pyconnect.http.server import HttpServer
                    server = HttpServer(
                        host=config.get('host', '0.0.0.0'),
                        port=int(config.get('port', 8888)),
                        run_func=self._compile_run_func(config),
                        path=config.get('path', '/run'),
                    )
                    server.spin(run_thread=True)
                    entry.client = server
                    entry.connected = True

            elif conn_type == 'visionserve':
                # Inference client only — the server is the visionserve binary.
                # from pyconnect.visionserve.client import VisionServeClient
                # client = VisionServeClient(
                #     url=config.get('url', 'http://localhost:11435'),
                #     model=config.get('model', 'rf-detr'),
                #     timeout=config.get('timeout', 30.0),
                # )
                # entry.client = client
                # entry.connected = client.server_connected
                from visionserve import Client, VisionServeError
                try: 
                    client = Client(config.get('url', 'http://localhost:11435'))
                    entry.client = client
                    entry.connected = client.health()['status']=='ok'
                except VisionServeError as e:
                    print(e)


        except Exception as e:
            entry.connected = False
            entry.error = str(e)
            error = str(e)

        with self._lock:
            self._connects[agent_name] = entry
        self._save()
        return agent_name, error

    def remove_connect(self, cid: str) -> bool:
        with self._lock:
            if cid not in self._connects:
                return False
            entry = self._connects.pop(cid)
        if self._ros_node is not None and entry.type in ('ros_service', 'ros_topic', 'ros_action'):
            self._ros_node.agents.pop(cid, None)
        if entry.type in ('tcp', 'zmq', 'websocket', 'http', 'visionserve') and entry.client is not None:
            try:
                if entry.config.get('is_client', True):
                    entry.client.close()
                else:
                    entry.client.stop()
            except Exception as e:
                print(f'[DeviceManager] Error closing {entry.type} {cid}: {e}')
        self._save()
        return True

    def update_connect(self, cid: str, name: str, config: dict) -> tuple[str, str]:
        with self._lock:
            entry = self._connects.get(cid)
        if entry is None:
            return cid, 'Client not found'
        conn_type_ = entry.type
        new_id, error = self.add_connect(conn_type=conn_type_, name=name, config=config)
        if new_id != cid:
            self.remove_connect(cid)
        return new_id, error

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def get_all(self) -> list[dict]:
        with self._lock:
            return [self._to_dict(e) for e in self._connects.values()]

    def get_connect(self, cid: str) -> Optional[ConnectEntry]:
        return self._connects.get(cid)

    def get_client(self, name: str) -> Any:
        # Special alias: 'llm' resolves to whichever type='llm' entry is
        # marked active in its config. Falls back to the first connected llm
        # entry so legacy callers still get something usable.
        if name == 'llm' and name not in self._connects:
            llm_entries = [e for e in self._connects.values() if e.type == 'llm']
            active = next((e for e in llm_entries if e.config.get('is_active')), None)
            if active is None:
                active = next((e for e in llm_entries if e.connected), None)
            return active.client if active is not None else None
        entry = self._connects.get(name)
        return entry.client if entry is not None else None

    def active_llm_config(self) -> dict:
        """Config (``url`` / ``model`` / …) of the active ``type='llm'``
        connection, or ``{}``. Mirrors the ``get_client('llm')`` resolution
        (active flag, else first connected) but returns the config so the
        planner can use it as its LLM config when no explicit ``_llm_cfg`` was
        set — i.e. selecting an LLM in the dashboard drives planning and
        survives a restart (the connection lives in ``connections.json``)."""
        llm_entries = [e for e in self._connects.values() if e.type == 'llm']
        active = next((e for e in llm_entries if e.config.get('is_active')), None)
        if active is None:
            active = next((e for e in llm_entries if e.connected), None)
        if active is None:
            return {}
        # Keep `name` — `init_llm_client` reads it as the backend id
        # (llama/chatgpt/…); only drop the connection-plumbing keys. Mirrors the
        # filtering used when the connection itself is initialised.
        _INTERNAL = {'agent_name', 'conn_name', 'conn_type', 'is_active'}
        return {k: v for k, v in active.config.items() if k not in _INTERNAL}

    def set_active(self, cid: str) -> bool:
        """Mark a single type='llm' entry as active and clear the flag on its
        peers. Persists immediately. Returns False if `cid` is not an llm entry."""
        with self._lock:
            target = self._connects.get(cid)
            if target is None or target.type != 'llm':
                return False
            for e in self._connects.values():
                if e.type == 'llm':
                    e.config['is_active'] = (e.id == cid)
        self._save()
        return True

    def get_status(self) -> dict[str, bool]:
        with self._lock:
            entries = list(self._connects.values())
        out = {}
        for e in entries:
            e.connected = self._ping(e)
            e.last_checked = time.time()
            out[e.id] = e.connected
        return out

    @staticmethod
    def _tcp_reachable(host: str, port: int, timeout: float = 1.0) -> bool:
        import socket
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def _ping(self, entry: ConnectEntry) -> bool:
        try:
            if entry.type == 'webrtc':
                if entry.is_camera:
                    host = entry.config.get('host', '')
                    port = entry.config.get('port', 8443)
                    return self._tcp_reachable(host, port)
                return entry.client.connected
            elif entry.type in ('ros_service', 'ros_action'):
                return getattr(entry.client, 'connected', False)
            elif entry.type == 'ros_topic':
                return entry.client.rev_data is not None
            elif entry.type == 'llm':
                return True
            elif entry.type in ('tcp', 'zmq', 'websocket', 'http', 'visionserve'):
                if not entry.config.get('is_client', True):
                    return bool(getattr(entry.client, 'active', False))
                # HTTP / visionserve are stateless — actively re-check reachability.
                if entry.type in ('http', 'visionserve') and hasattr(entry.client, 'ping'):
                    return bool(entry.client.ping())
                return bool(getattr(entry.client, 'server_connected', False))
        except Exception:
            return False
        return False

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _save(self):
        if self._loading:
            return
        with self._lock:
            data = [
                {'type': e.type, 'name': e.name, 'config': e.config}
                for e in self._connects.values()
            ]
        text = json.dumps(data, indent=2)
        persist = self._persist_file
        tmp = persist.with_suffix('.json.tmp')
        bak = persist.with_suffix('.json.bak')
        try:
            persist.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(text)
            if persist.exists():
                persist.replace(bak)
            tmp.replace(persist)
        except Exception as e:
            print(f'[DeviceManager] Could not save devices: {e}')

    def set_data_dir(self, new_data_dir: Path):
        """Repoint persistence at a new directory without touching the live
        connections (used by rename, where the files move with the dir)."""
        self._data_dir = Path(new_data_dir)
        self._persist_file = self._data_dir / 'connections.json'

    def _teardown_all(self):
        """Disconnect and forget every device, keeping the shared ROS node
        alive. Does NOT persist — callers repoint + reload right after."""
        with self._lock:
            entries = list(self._connects.values())
            self._connects = {}
        for e in entries:
            try:
                if self._ros_node is not None and e.type in ('ros_service', 'ros_topic', 'ros_action'):
                    self._ros_node.agents.pop(e.id, None)
                elif e.type == 'tcp' and e.client is not None:
                    if e.config.get('is_client', True):
                        e.client.close()
                    else:
                        e.client.stop()
                elif e.type == 'webrtc' and e.client is not None:
                    close = getattr(e.client, 'close', None)
                    if callable(close):
                        close()
            except Exception as ex:
                print(f'[DeviceManager] Error tearing down {e.id}: {ex}')

    def reload_from(self, new_data_dir: Path):
        """Hot-switch to a new connections.json: tear down current devices,
        repoint, then reconnect everything from the new file. Reuses the
        existing ROS node (rclpy can't be re-initialised per switch)."""
        self._teardown_all()
        self.set_data_dir(new_data_dir)
        self.load_saved()

    def load_saved(self):
        persist = self._persist_file
        if not persist.exists():
            return
        try:
            data = json.loads(persist.read_text())
        except Exception as e:
            print(f'[DeviceManager] Could not load saved devices: {e}')
            # try backup
            bak = persist.with_suffix('.json.bak')
            if bak.exists():
                try:
                    data = json.loads(bak.read_text())
                    print(f'[DeviceManager] Recovered from backup')
                except Exception:
                    return
            else:
                return
        self._loading = True
        try:
            for item in data:
                _, error = self.add_connect(conn_type=item['type'], name=item['name'], config=item['config'])
                status = 'ok' if not error else f'error: {error}'
                print(f"[DeviceManager] Restored '{item['name']}' ({item['type']}) — {status}")
        finally:
            self._loading = False
            self._save()

    def _to_dict(self, e: ConnectEntry) -> dict:
        return {
            'id': e.id,
            'name': e.name,
            'type': e.type,
            'config': e.config,
            'connected': e.connected,
            'is_camera': e.is_camera,
            'is_active': bool(e.config.get('is_active', False)) if e.type == 'llm' else False,
            'error': e.error,
            'last_checked': e.last_checked,
        }
