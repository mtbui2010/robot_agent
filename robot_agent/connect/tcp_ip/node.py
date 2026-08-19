from .server import TcpIpServer
from .client import TcpIpClient
from ..helpers import update_dict


def get_tcpip_server_configs(**kwargs):
    return update_dict({
        "conn_type":   "tcpip_server",
        "host":        "0.0.0.0",
        "port":        8888,
        "num_connect": 5,
        "run_func":    None,
    }, kwargs)


def get_tcpip_client_configs(**kwargs):
    return update_dict({
        "conn_type": "tcpip_client",
        "host":      "localhost",
        "port":      8888,
    }, kwargs)


_AGENT_CLASS = {
    "tcpip_server": TcpIpServer,
    "tcpip_client": TcpIpClient,
}


def make_agent(**kwargs):
    conn_type  = kwargs.get("conn_type", "tcpip_server")
    cfg        = (get_tcpip_server_configs if "server" in conn_type else get_tcpip_client_configs)(**kwargs)
    agent_name = cfg.get("agent_name", f"{conn_type}_unnamed")
    agent      = _AGENT_CLASS[conn_type](**{k: v for k, v in cfg.items() if k not in ("conn_type", "agent_name")})
    if "server" in conn_type:
        agent.listen(run_thread=True)
    return {agent_name: agent}


class TcpIpNode:
    def add_agent(self, **kwargs):
        if hasattr(self, "agents"):
            self.agents.update(make_agent(**kwargs))
        else:
            self.agents = make_agent(**kwargs)
