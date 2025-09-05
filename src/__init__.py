"""
control and monitor fpga-based casper designs.
"""

# import all the main classes that we'll use often
from .bitfield import Bitfield, Field
from .katadc import KatAdc
from .casperfpga import CasperFpga
from .transport_katcp import KatcpTransport
from .transport_tapcp import TapcpTransport
from .transport_redis import RedisTapcpTransport
from .transport_localpcie import LocalPcieTransport
from .transport_remotepcie import RemotePcieTransport
from .transport_alveo import AlveoTransport
from .memory import Memory
from .network import IpAddress, Mac
from .qdr import Qdr
from .register import Register
from .sbram import Sbram
from .snap import Snap
from .snapadc import SnapAdc
from .tengbe import TenGbe

# BEGIN VERSION CHECK
# Get package version when locally imported from repo or via -e develop install
try:
    import katversion as _katversion
except ImportError:
    import time as _time
    __version__ = "0.0+unknown.{}".format(_time.strftime('%Y%m%d%H%M'))
else:
    __version__ = _katversion.get_version(__path__[0])
# END VERSION CHECK

name = "casperfpga"

# end
