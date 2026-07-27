"""pyhart: a native, cross-platform HART master (Universal + Common Practice)."""
from . import frame, codec, units          # pure, always importable

__all__ = ["frame", "codec", "units",
           "HartTransport", "TransportConfig", "list_serial_ports",
           "HartMaster", "HartError", "HartTimeout", "HartCommError"]

# transport/master need pyserial; import lazily so the pure core works without it
try:
    from .transport import (HartTransport, TransportConfig,
                            list_serial_ports, HartTimeout, HartCommError)
    from .master import HartMaster, HartError
except ImportError:
    pass
