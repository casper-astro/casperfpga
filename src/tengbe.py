import logging
import struct
import numpy as np
import importlib.resources as resources

from .memory import Memory
from .network import Mac, IpAddress
from .gbe import Gbe

LOGGER = logging.getLogger(__name__)

STRUCT_CTYPES = {1: 'B', 2: 'H', 4: 'L', 8: 'Q'}
STRUCT_CTYPES_TO_B = {'B': 1, 'H': 2, 'L': 4, 'Q': 8}

TENGBE_UNIFIED_MMAP_TXT = resources.files('casperfpga') / 'tengbe_mmap.txt'
TENGBE_MMAP_LEGACY_TXT  = resources.files('casperfpga') / 'tengbe_mmap_legacy.txt'

# Offsets for fields in the memory map, in bytes
OFFSET_CORE_TYPE   = 0x0
OFFSET_BUFFER_SIZE = 0x4
OFFSET_WORD_LEN    = 0x8
OFFSET_MAC_ADDR    = 0xc
OFFSET_IP_ADDR     = 0x14
OFFSET_GW_ADDR     = 0x18
OFFSET_NETMASK     = 0x1c
OFFSET_MC_IP       = 0x20
OFFSET_MC_MASK     = 0x24
OFFSET_BUF_VLD     = 0x28
OFFSET_FLAGS       = 0x2c
OFFSET_PORT        = 0x30
OFFSET_STATUS      = 0x34
OFFSET_CONTROL     = 0x40
OFFSET_ARP_SIZE    = 0x44
OFFSET_TX_PKT_RATE = 0x48
OFFSET_TX_PKT_CNT  = 0x4c
OFFSET_TX_VLD_RATE = 0x50
OFFSET_TX_VLD_CNT  = 0x54
OFFSET_TX_OF_CNT   = 0x58
OFFSET_TX_AF_CNT   = 0x5c
OFFSET_RX_PKT_RATE = 0x60
OFFSET_RX_PKT_CNT  = 0x64
OFFSET_RX_VLD_RATE = 0x68
OFFSET_RX_VLD_CNT  = 0x6c
OFFSET_RX_OF_CNT   = 0x70
OFFSET_RX_AF_CNT   = 0x74
OFFSET_COUNT_RST   = 0x78

OFFSET_ARP_CACHE   = 0x1000
OFFSET_TX_BUFFER   = 0x4000
OFFSET_RX_BUFFER   = 0x8000

# Sizes for fields in the memory map, in bytes
SIZE_CORE_TYPE   = 0x4
SIZE_BUFFER_SIZE = 0x4
SIZE_WORD_LEN    = 0x4
SIZE_MAC_ADDR    = 0x8
SIZE_IP_ADDR     = 0x4
SIZE_GW_ADDR     = 0x4
SIZE_NETMASK     = 0x4
SIZE_MC_IP       = 0x4
SIZE_MC_MASK     = 0x4
SIZE_BUF_AVAIL   = 0x4
SIZE_FLAGS       = 0x4
SIZE_PORT        = 0x4
SIZE_STATUS      = 0x8
SIZE_CONTROL     = 0x8
SIZE_ARP_SIZE    = 0x4
SIZE_TX_PKT_RATE = 0x4
SIZE_TX_PKT_CNT  = 0x4
SIZE_TX_VLD_RATE = 0x4
SIZE_TX_VLD_CNT  = 0x4
SIZE_TX_OF_CNT   = 0x4
SIZE_TX_AF_CNT   = 0x4
SIZE_RX_PKT_RATE = 0x4
SIZE_RX_PKT_CNT  = 0x4
SIZE_RX_VLD_RATE = 0x4
SIZE_RX_VLD_CNT  = 0x4
SIZE_RX_OF_CNT   = 0x4
SIZE_RX_AF_CNT   = 0x4
SIZE_COUNT_RST   = 0x4

SIZE_ARP_CACHE   = 0x3000
SIZE_TX_BUFFER   = 0x4000
SIZE_RX_BUFFER   = 0x4000

def read_memory_map_definition(filename):
    """ Read memory map definition from text file.

    Returns a python dictionary:
        {REGISTER_NAME1: {'offset': offset, 'size': size, 'rwflag': rwflag},
         REGISTER_NAME2: {'offset': offset, 'size': size, 'rwflag': rwflag}
         ...}

    Notes:
        Used by TenGbe.configure_core() to write to mmap.
    """
    mmap_arr = np.genfromtxt(filename, dtype='str', skip_header=1)
    mmap_keys    = list(mmap_arr[:, 0])
    mmap_offsets = [int(x, 0) for x in mmap_arr[:, 1]]
    mmap_size    = [int(x, 0) for x in mmap_arr[:, 2]]
    mmap_rw      = list(mmap_arr[:, 3])
    mmap  = {}
    for ii, k in enumerate(mmap_keys):
        mmap[k] = {'offset': mmap_offsets[ii], 'size': mmap_size[ii], 'rwflag': mmap_rw[ii]}
    return mmap

def read_memory_map_definition(filename):
    """ Read memory map definition from text file.

    Returns a python dictionary:
        {REGISTER_NAME1: {'offset': offset, 'size': size, 'rwflag': rwflag},
         REGISTER_NAME2: {'offset': offset, 'size': size, 'rwflag': rwflag}
         ...}

    Notes:
        Used by TenGbe.configure_core() to write to mmap.
    """
    mmap_arr = np.genfromtxt(filename, dtype='str', skip_header=1)
    mmap_keys    = list(mmap_arr[:, 0])
    mmap_offsets = [int(x, 0) for x in mmap_arr[:, 1]]
    mmap_size    = [int(x, 0) for x in mmap_arr[:, 2]]
    mmap_rw      = list(mmap_arr[:, 3])
    mmap  = {}
    for ii, k in enumerate(mmap_keys):
        mmap[k] = {'offset': mmap_offsets[ii], 'size': mmap_size[ii], 'rwflag': mmap_rw[ii]}
    return mmap

class TenGbe(Memory, Gbe):
    """
    To do with the CASPER ten GBE yellow block implemented on FPGAs,
    and interfaced-to via KATCP memory reads/writes.
    """
    def __init__(self, parent, name, address, length_bytes, device_info=None):
        """

        :param parent: Parent object who owns this TenGbe instance
        :param name: Unique name of the instance
        :param address:
        :param length_bytes:
        :param device_info: Information about this device
        """
        Memory.__init__(self, name, 32, address, length_bytes)
        Gbe.__init__(self, parent, name, address, length_bytes, device_info)
        self.memmap_compliant = self._check_memmap_compliance()
        if self.memmap_compliant:
            self.memmap = read_memory_map_definition(TENGBE_UNIFIED_MMAP_TXT)
        else:
            self.memmap = read_memory_map_definition(TENGBE_MMAP_LEGACY_TXT)

    @property
    def mac(self):
        return self.get_gbe_core_details()['mac']

    @property
    def ip_address(self):
        return self.get_gbe_core_details()['ip']

    @property
    def port(self):
        return self.get_gbe_core_details()['fabric_port']

    def _check_memmap_compliance(self):
        """
        Look at the first word of the core's memory map and try to
        figure out if it compliant with the harmonized ethernet map.
        This isn't flawless, but unless the user sets a very weird
        MAC address for their core (which is what the old core's map
        stored in register 0, it should be OK).
        """
        x = self.parent.read(self.name, 4)
        cpu_tx_en, cpu_rx_en, rev, core_type = struct.unpack('4B', x)
        if (cpu_tx_en > 1) or (cpu_rx_en > 1) or ((core_type != 4) and (core_type != 2)):
            return False
        else:
            return True

    def post_create_update(self, raw_device_info):
        """
        Update the device with information not available at creation.

        :param raw_device_info: info about this block that may be useful
        """
        super(TenGbe, self).post_create_update(raw_device_info)
        self.snaps = {'tx': None, 'rx': None}
        for snapshot in self.parent.snapshots:
            if snapshot.name.find(self.name + '_') == 0:
                name = snapshot.name.replace(self.name + '_', '')
                if name == 'txs_ss':
                    self.snaps['tx'] = snapshot.name
                elif name == 'rxs_ss':
                    self.snaps['rx'] = snapshot.name
                else:
                    errmsg = '%s: incorrect snap %s under tengbe ' \
                             'block' % (self.fullname, snapshot.name)
                    LOGGER.error(errmsg)
                    raise RuntimeError(errmsg)

    def read_txsnap(self):
        """
        Read the TX snapshot embedded in this TenGBE yellow block
        """
        return self.snaps['tx'].read(timeout=10)['data']

    def read_rxsnap(self):
        """
        Read the RX snapshot embedded in this TenGBE yellow block
        """
        return self.snaps['rx'].read(timeout=10)['data']

    def _memmap_write(self, register, value):
        """ Write to memory map
        :param register: register to write to. Register must be in memmap.
        :param value: Value to write.
        """
        offset   = self.memmap[register]['offset']
        bytesize = self.memmap[register]['size']
        rw_addr  = offset - offset % 4               # Round in case of non 32-bit writes
        ctype    = STRUCT_CTYPES.get(bytesize, 'L')  # Treat as 32-bit if longer than 8B

        if self.memmap[register]['rwflag'] == 'r':
            raise RuntimeError("Warning: %s is read-only!" % register)

        if bytesize in (1, 2):
            n_elem = int(4 / bytesize)
            pcode  = '>%i%s' % (n_elem, ctype)
            current_value  = self.parent.read(self.name, size=4, offset=rw_addr)
            new_arr = list(struct.unpack(pcode, current_value))[::-1]
            new_arr[offset % n_elem] = value
            new_arr = new_arr[::-1]
            packed = struct.pack(pcode, *new_arr)

        elif bytesize in (4, 8):
            if isinstance(value, str):
                packed = value
            else:
                packed = struct.pack('>%s' % ctype, value)
        else:
            raise RuntimeError("Can only write 1,2,4,8 Byte registers with this function.")
        self.parent.blindwrite(self.name, packed, offset=rw_addr)

    def _memmap_read(self, register):
        """ Read from memory map

        :param register: register to read from. Must be in memmap.
        """
        offset   = self.memmap[register]['offset']
        bytesize = self.memmap[register]['size']
        ctype    = STRUCT_CTYPES.get(bytesize)  # Treat as 32-bit if longer than 8B

        if bytesize in (4, 8):
            value = self.parent.read(self.name, size=bytesize, offset=offset)
            value = struct.unpack('>%s' % ctype, value)[0]
        elif bytesize in (1, 2):
            if bytesize == 2 and offset % 4 not in (0, 2):
                raise RuntimeError("Attempted to read 16-bits from 32-bit word with %iB offset. "
                                   "Not supported." % (offset%4))
            read_addr = offset - offset % 4
            value = self.parent.read(self.name, size=4, offset=read_addr)
            valuearr = struct.unpack('>%i%s' % (int(4 / bytesize), ctype), value)
            value = valuearr[::-1][int((offset % 4) / bytesize)]
        else:
            raise RuntimeError("Cannot read %s of size %i: only 1,2,4,8 B supported" % (register, bytesize))
        return value

    def _memmap_read_array(self, register, ctype='L'):
        """ Read array chunk from mem-map """
        offset   = self.memmap[register]['offset']
        bytesize = self.memmap[register]['size']

        if isinstance(ctype, str):
            wsize = STRUCT_CTYPES_TO_B[ctype]
        elif isinstance(ctype, int):
            wsize = ctype
            ctype = STRUCT_CTYPES[ctype]
        else:
            raise RuntimeError('Unknown ctype: %s' % ctype)

        value = self.parent.read(self.name, size=bytesize, offset=offset)
        value = struct.unpack('>%i%s' % (int(bytesize / wsize), ctype), value)
        return value

    def _memmap_write_array(self, register, value, ctype='L'):
        offset   = self.memmap[register]['offset']
        bytesize = self.memmap[register]['size']

        if isinstance(ctype, str):
            wsize = STRUCT_CTYPES_TO_B[ctype]
        elif isinstance(ctype, int):
            wsize = ctype
            ctype = STRUCT_CTYPES[ctype]
        else:
            raise RuntimeError('Unknown ctype: %s' % ctype)

        n_elem = int(bytesize / wsize)  # Differs to n_elem in small bytesize code!
        if len(value) != n_elem:
            raise RuntimeError("Register is %i 32-bit words long, but array is "
                               "of length %i. Make sure these match." % (len(value), n_elem))
        if isinstance(value, str):
            packed = value
        else:
            packed = struct.pack('>%i%s' % (n_elem, ctype), *value)
        self.parent.blindwrite(self.name, packed, offset=offset)

    def configure_core(self, mac, ip, port, gateway=None, subnet_mask=None):
        """
        Configure the interface by writing to its internal configuration registers.

        :param mac: String or Integer input, MAC address (e.g. '02:00:00:00:00:01')
        :param ipaddress: String or Integer input, IP address (eg '10.0.0.1')
        :param port: String or Integer input
        :param gateway: String or Integer input, an IP address
        :param subnet_mask: string or integer, subnet mask (e.g. '255.255.255.0')
        """

        mac_to_write         = Mac(mac).mac_int
        ip_address_to_write  = IpAddress(ip).ip_int
        port_to_write        = port if isinstance(port, int) else int(port)
        gateway_to_write = IpAddress(gateway).ip_int if gateway is not None else 1
        subnet_mask_to_write = IpAddress(subnet_mask).ip_int if subnet_mask is not None else 0xffffff00

        self._memmap_write('MAC_ADDR', mac_to_write)
        self._memmap_write('IP_ADDR',  ip_address_to_write)
        self._memmap_write('NETMASK',  subnet_mask_to_write)
        self._memmap_write('GW_ADDR',  gateway_to_write)
        self._memmap_write('PORT',     port_to_write)

        self.fabric_enable()
        self.fabric_soft_reset_toggle()

    def dhcp_start(self):
        """
        Configure this interface, then start a DHCP client on ALL interfaces.
        """
        #if self.mac is None:
            # TODO get MAC from EEPROM serial number and assign here
            #self.mac = '0'
        reply, _ = self.parent.transport.katcprequest(
            name='tap-start', request_timeout=5,
            require_ok=True,
            request_args=(self.name, self.name, '0.0.0.0',
                          str(self.port), str(self.mac), ))
        if reply.arguments[0] != 'ok':
            raise RuntimeError('%s: failure starting tap driver.' % self.name)

        reply, _ = self.parent.transport.katcprequest(
            name='tap-arp-config', request_timeout=1,
            require_ok=True,
            request_args=(self.name, 'mode', '0'))
        if reply.arguments[0] != 'ok':
            raise RuntimeError('%s: failure disabling ARP.' % self.name)

        reply, _ = self.parent.transport.katcprequest(
            name='tap-dhcp', request_timeout=30,
            require_ok=True,
            request_args=(self.name, ))
        if reply.arguments[0] != 'ok':
            raise RuntimeError('%s: failure starting DHCP client.' % self.name)

        reply, _ = self.parent.transport.katcprequest(
            name='tap-arp-config', request_timeout=1,
            require_ok=True,
            request_args=(self.name, 'mode', '-1'))
        if reply.arguments[0] != 'ok':
            raise RuntimeError('%s: failure re-enabling ARP.' % self.name)
        # it looks like the command completed without error, so
        # update the basic core details
        self.get_gbe_core_details()

    def tap_start(self, restart=False):
        """
        Program a 10GbE device and start the TAP driver.

        :param restart: stop before starting
        """
        if len(self.name) > 8:
            raise NameError('%s: tap device identifier must be shorter than 9 '
                            'characters..' % self.fullname)
        if restart:
            self.tap_stop()
        if self.tap_running():
            LOGGER.info('%s: tap already running.' % self.fullname)
            return
        LOGGER.info('%s: starting tap driver.' % self.fullname)
        reply, _ = self.parent.transport.katcprequest(
            name='tap-start', request_timeout=-1, require_ok=True,
            request_args=(self.name, self.name, str(self.ip_address),
                          str(self.port), str(self.mac), ))
        if reply.arguments[0] != 'ok':
            raise RuntimeError('%s: failure starting tap driver.' %
                               self.fullname)

    def tap_stop(self):
        """
        Stop a TAP driver.
        """
        if not self.tap_running():
            return
        LOGGER.info('%s: stopping tap driver.' % self.fullname)
        reply, _ = self.parent.transport.katcprequest(
            name='tap-stop', request_timeout=-1,
            require_ok=True, request_args=(self.name, ))
        if reply.arguments[0] != 'ok':
            raise RuntimeError('%s: failure stopping tap '
                               'device.' % self.fullname)

    def tap_info(self):
        """
        Get info on the tap instance running on this interface.
        """
        uninforms = []

        def handle_inform(msg):
            uninforms.append(msg)

        self.parent.unhandled_inform_handler = handle_inform
        _, informs = self.parent.transport.katcprequest(
            name='tap-info', request_timeout=-1,
            require_ok=False, request_args=(self.name, ))
        self.parent.unhandled_inform_handler = None
        # process the tap-info
        if len(informs) == 1:
            return {'name': informs[0].arguments[0],
                    'ip': informs[0].arguments[1]}
        elif len(informs) == 0:
            return {'name': '', 'ip': ''}
        else:
            raise RuntimeError('%s: invalid return from tap-info?' %
                               self.fullname)
        # TODO - this request should return okay if the tap isn't
        # running - it shouldn't fail
        # if reply.arguments[0] != 'ok':
        #     log_runtime_error(LOGGER, 'Failure getting tap info for '
        #                               'device %s." % str(self))

    def tap_running(self):
        """
        Determine if an instance if tap is already running on for this
        ten GBE interface.
        """
        tapinfo = self.tap_info()
        if tapinfo['name'] == '':
            return False
        return True

    def tap_arp_reload(self):
        """
        Have the tap driver reload its ARP table right now.
        """
        reply, _ = self.parent.transport.katcprequest(
            name="tap-arp-reload", request_timeout=-1,
            require_ok=True, request_args=(self.name, ))
        if reply.arguments[0] != 'ok':
            raise RuntimeError('Failure requesting ARP reload for tap '
                               'device %s.' % str(self))

    def multicast_receive(self, ip_str, group_size):
        """
        Send a request to KATCP to have this tap instance send a multicast
        group join request.

        :param ip_str: A dotted decimal string representation of the base
            mcast IP address.
        :param group_size: An integer for how many mcast addresses from
            base to respond to.
        """
        # mask = 255*(2 ** 24) + 255*(2 ** 16) + 255*(2 ** 8) + (255-group_size)
        # self.parent.write_int(self.name, str2ip(ip_str), offset=12)
        # self.parent.write_int(self.name, mask, offset=13)

        # mcast_group_string = ip_str + '+' + str(group_size)
        mcast_group_string = ip_str
        reply, _ = self.parent.transport.katcprequest(
            'tap-multicast-add', -1, True, request_args=(self.name, 'recv',
                                                         mcast_group_string, ))
        if reply.arguments[0] == 'ok':
            if mcast_group_string not in self.multicast_subscriptions:
                self.multicast_subscriptions.append(mcast_group_string)
            return
        else:
            raise RuntimeError('%s: failed adding multicast receive %s to '
                               'tap device.' % (self.fullname,
                                                mcast_group_string))

    def multicast_remove(self, ip_str):
        """
        Send a request to be removed from a multicast group.

        :param ip_str: A dotted decimal string representation of the base
            mcast IP address.
        """
        try:
            reply, _ = self.parent.transport.katcprequest(
                'tap-multicast-remove', -1, True,
                request_args=(self.name, IpAddress.str2ip(ip_str), ))
        except:
            raise RuntimeError('%s: tap-multicast-remove does not seem to '
                               'be supported on %s' % (self.fullname,
                                                       self.parent.host))
        if reply.arguments[0] == 'ok':
            if ip_str not in self.multicast_subscriptions:
                LOGGER.warning(
                    '%s: That is odd, %s removed from mcast subscriptions, but '
                    'it was not in its list of sbscribed addresses.' % (
                        self.fullname, ip_str))
                self.multicast_subscriptions.remove(ip_str)
            return
        else:
            raise RuntimeError('%s: failed removing multicast address %s '
                               'from tap device' % (self.fullname,
                                                    IpAddress.str2ip(ip_str)))

    def _fabric_enable_disable(self, target_val):
        """

        :param target_val:
        """
        self._memmap_write('ENABLE', target_val)


    def fabric_enable(self):
        """
        Enable the core fabric
        """
        self._fabric_enable_disable(1)

    def fabric_disable(self):
        """
        Enable the core fabric
        """
        self._fabric_enable_disable(0)

    def fabric_soft_reset_toggle(self):
        """
        Toggle the fabric soft reset
        """
        self._memmap_write('SOFT_RST', 0)
        self._memmap_write('SOFT_RST', 1)
        self._memmap_write('SOFT_RST', 0)

        FLAGS_OFFSET = self.memmap['FLAGS']['offset']
        FLAGS_SIZE   = self.memmap['FLAGS']['size']

    def get_gbe_core_details(self, read_arp=False, read_cpu=False, read_multicast=False):
        """
        Get 10GbE core details.

        :param read_arp (bool): Get ARP table details (default False)
        :param read_cpu (bool): Get CPU details (default False)
        :param read_multicast (bool): Get multicast address table (default False)

        :returns: dictionary of core details (IP address, subnet mask, MAC address, port, etc).
        """
        IP_ADDR   = self._memmap_read('IP_ADDR')
        IP_PREFIX = '.'.join(IpAddress(IP_ADDR).ip_str.split('.')[:3])

        returnval = {
            'ip_prefix': IP_PREFIX,
            'ip': IpAddress(IP_ADDR),
            'subnet_mask': IpAddress(self._memmap_read('NETMASK')),
            'mac': Mac(self._memmap_read('MAC_ADDR')),
            'gateway_ip': IpAddress(self._memmap_read('GW_ADDR')),
            'fabric_port': self._memmap_read('PORT'),
            'fabric_en': self._memmap_read('ENABLE'),
            'multicast': {'base_ip': IpAddress(self._memmap_read('MC_IP')),
                          'ip_mask': IpAddress(self._memmap_read('MC_MASK')),
                          'rx_ips': []}
        }

        if not self.memmap_compliant:
            data = self.parent.read(self.name, 16384)
            data = list(struct.unpack('>16384B', data))

            returnval_legacy_dict = {
                'xaui_lane_sync': [bool(data[0x27] & 4), bool(data[0x27] & 8),
                                   bool(data[0x27] & 16), bool(data[0x27] & 32)],
                'xaui_status': [data[0x24], data[0x25], data[0x26], data[0x27]],
                'xaui_chan_bond': bool(data[0x27] & 64),
                'xaui_phy': {'rx_eq_mix': data[0x28], 'rx_eq_pol': data[0x29],
                             'tx_preemph': data[0x2a], 'tx_swing': data[0x2b]},
            }
            returnval.update(returnval_legacy_dict)

        if read_multicast:
            possible_addresses = [int(returnval['multicast']['base_ip'])]
            mask_int = int(returnval['multicast']['ip_mask'])
            for ctr in range(32):
                mask_bit = (mask_int >> ctr) & 1
                if not mask_bit:
                    new_ips = []
                    for ip in possible_addresses:
                        new_ips.append(ip & (~(1 << ctr)))
                        new_ips.append(new_ips[-1] | (1 << ctr))
                    possible_addresses.extend(new_ips)
            tmp = list(set(possible_addresses))
            for ip in tmp:
                returnval['multicast']['rx_ips'].append(IpAddress(ip))

        if read_arp:
            returnval['arp'] = self.get_arp_details()
        if read_cpu:
            returnval.update(self.get_cpu_details())

        self.core_details = returnval
        return returnval

    def get_arp_details(self, N=256):
        """ Get ARP details from this interface. """
        arp_table = self._memmap_read_array('ARP_CACHE', ctype='Q')
        return list(map(Mac, arp_table[:N]))

    def get_cpu_details(self, port_dump=None):
        """
        Read details of the CPU buffers.

        :param port_dump:
        """
        #TODO Not memmap compliant
        if port_dump is None:
            port_dump = self.parent.read(self.name, 16384)
            port_dump = list(struct.unpack('>16384B', port_dump))
        returnval = {'cpu_tx': {}}
        for ctr in range(4096 / 8):
            tmp = []
            for ctr2 in range(8):
                tmp.append(port_dump[4096 + (8 * ctr) + ctr2])
            returnval['cpu_tx'][ctr*8] = tmp
        returnval['cpu_rx_buf_unack_data'] = port_dump[6 * 4 + 3]
        returnval['cpu_rx'] = {}
        for ctr in range(port_dump[6 * 4 + 3] + 8):
            tmp = []
            for ctr2 in range(8):
                tmp.append(port_dump[8192 + (8 * ctr) + ctr2])
            returnval['cpu_rx'][ctr * 8] = tmp
        return returnval

    def set_single_arp_entry(self, ip, mac):
        """
        Set a single MAC address entry in the ARP table.

        :param ip (string) : IP whose MAC we should set. E.g. '10.0.1.123'
        :param mac (int)   : MAC address to associate with this IP. Eg. 0x020304050607
        """
        arp_addr = self.memmap['ARP_CACHE']['offset']
        arp_addr += 8*int(ip.split('.')[-1])
        mac_pack = struct.pack('>Q', mac)
        self.parent.write(self.name, mac_pack, offset=arp_addr)

    def set_arp_table(self, macs):
        """Set the ARP table with a list of MAC addresses. The list, `macs`,
        is passed such that the zeroth element is the MAC address of the
        device with IP XXX.XXX.XXX.0, and element N is the MAC address of the
        device with IP XXX.XXX.XXX.N"""
        arp_addr = self.memmap['ARP_CACHE']['offset']
        macs = list(macs)
        if isinstance(macs[0], Mac):
            macs = [m.mac_int for m in macs]
        macs_packed = struct.pack('>%dQ' % (len(macs)), *macs)
        self.parent.write(self.name, macs_packed, offset=arp_addr)

# end
