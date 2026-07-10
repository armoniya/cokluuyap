"""
UYAP Çekirdek Paketi (uyap_core)
--------------------------------
GUI'den bağımsız, import edilebilir çekirdek. Daha büyük bir proje (ör. masaüstü kabuk ya
da bir servis) bu paketi import edip iki rolü doğrudan çalıştırabilir; tkinter/arayüze
bağımlılık YOKTUR.

Modüller:
  • uyap_proxy  — UYAP oturumunu kuran/canlı tutan FastAPI proxy (e-imza tarafı çekirdeği).
  • p2p_wire    — WebRTC DataChannel çerçeveleme + ICE yardımcıları.
  • office_agent— PAYLAŞAN taraf: tek UYAP oturumunu LAN + WebRTC ile N istemciye sunar.
  • home_client — ALAN taraf: yerel 127.0.0.1 sunucusu + ofise LAN/WebRTC tüneli.

Hızlı kullanım:
    import asyncio, argparse
    from uyap_core import run_office, run_client

    # Ofis (paylaşan):
    args = argparse.Namespace(signaling="wss://.../ws", room="<ODA>", password="<PAROLA>",
                              pin="<PIN>", cert_id=None, no_verify=False, interval=5,
                              proxy_port=8800, host="0.0.0.0", user="<ODA>")
    asyncio.run(run_office(args))

    # İstemci (alan):
    args = argparse.Namespace(signaling="wss://.../ws", room="<ODA>", password="<PAROLA>",
                              host="127.0.0.1", port=8800)
    asyncio.run(run_client(args))

Not: Bu paket SUNUCU tarafı (vendor_server.py / accounts.py / signaling_server.py) ile
karışmaz — onlar ayrı, bulutta çalışan satıcı bileşenleridir.
"""

from . import uyap_proxy, p2p_wire, office_agent, home_client, jobs, job_handlers
from .office_agent import run_office
from .home_client import amain as run_client
from .jobs import MANAGER as job_manager, job_type, registered_types

__all__ = [
    "uyap_proxy", "p2p_wire", "office_agent", "home_client", "jobs", "job_handlers",
    "run_office", "run_client",
    "job_manager", "job_type", "registered_types",
]
