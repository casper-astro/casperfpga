import logging
import struct
import time
import socket
from time import strptime
import string
try:
    from collections import Callable
except(ImportError):
    # starting in Python 3.10, Callable is in abc
    from collections.abc import Callable

from . import register
from . import sbram
from . import snap
from . import onegbe
from . import tengbe
from . import fortygbe
from . import onehundredgbe
from . import qdr
from . import hmc
from . import katadc
from . import snapadc
from . import sysmon
from . import xil_device
from . import adc_4x16g_asnt
from .memory import Memory
from . import rfdc

from .attribute_container import AttributeContainer
from .utils import parse_fpg, get_hostname, get_kwarg, get_git_info_from_fpg
from .transport_katcp import KatcpTransport
from .transport_tapcp import TapcpTransport
from .transport_dummy import DummyTransport
from .transport_alveo import AlveoTransport
from .casper_platform_id_map import PLATFORM_ID

from .CasperLogHandlers import configure_console_logging, configure_file_logging
from .CasperLogHandlers import getLogger


# known CASPER memory-accessible devices and their associated
# classes and containers
CASPER_MEMORY_DEVICES = {
    'xps:bram':         {'class': sbram.Sbram,       'container': 'sbrams'},
    'xps:qdr':          {'class': qdr.Qdr,           'container': 'qdrs'},
    'xps:sw_reg':       {'class': register.Register, 'container': 'registers'},
    'xps:tengbe_v2':    {'class': tengbe.TenGbe,     'container': 'gbes'},
    'xps:ten_gbe':      {'class': tengbe.TenGbe,     'container': 'gbes'},
    'xps:forty_gbe':    {'class': fortygbe.FortyGbe, 'container': 'gbes'},
    'xps:onehundred_gbe':{'class': onehundredgbe.OneHundredGbe, 'container': 'gbes'},
    'xps:onegbe':       {'class': onegbe.OneGbe,     'container': 'gbes'},
    'casper:snapshot':  {'class': snap.Snap,         'container': 'snapshots'},
    'xps:hmc':          {'class': hmc.Hmc,           'container': 'hmcs'},
    'xps:xil_device':   {'class': xil_device.Xil_Device, 'container': 'xil_device'}
}

CASPER_ADC_DEVICES = {
    'xps:katadc':                   {'class': katadc.KatAdc,        'container': 'adcs'},
    'xps:adc_4x16g_asnt':           {'class': adc_4x16g_asnt.Adc_4X16G_ASNT, 'container': 'adcs'},
    'xps:snap_adc':                 {'class': snapadc.SnapAdc,      'container': 'adcs'},
    'xps:snap_adc':                 {'class': snapadc.SnapAdc,      'container': 'adcs'},
    'xps:rfdc':                     {'class': rfdc.RFDC,            'container': 'adcs'}
}

# other devices - blocks that aren't memory devices nor ADCs, but about which we'd
# like to know tagged in the simulink diagram
CASPER_OTHER_DEVICES = {
    'casper:bitsnap':               'bitsnap',
    'casper:dec_fir':               'dec_fir',
    'casper:fft':                   'fft',
    'casper:fft_biplex_real_2x':    'fft_biplex_real_2x',
    'casper:fft_biplex_real_4x':    'fft_biplex_real_4x',
    'casper:fft_wideband_real':     'fft_wideband_real',
    'casper:info':                  'info',
    'casper:pfb_fir':               'pfb_fir',
    'casper:pfb_fir_async':         'pfb_fir_async',
    'casper:pfb_fir_generic':       'pfb_fir_generic',
    'casper:pfb_fir_real':          'pfb_fir_real',
    'casper:spead_pack':            'spead_pack',
    'casper:spead_unpack':          'spead_unpack',
    'casper:vacc':                  'vacc',
    'casper:xeng':                  'xeng',
    'xps:xsg':                      'xps',
}

class UnknownTransportError(Exception):
    pass


class CasperFpga(object):
    """
    A FPGA host board that has a CASPER design running on it. Or will soon have.
    """

    def __init__(self, *args, **kwargs):
        """
        :param args[0] - host: the hostname of this CasperFpga
        """
        if len(args) > 0:
            try:
                kwargs['host'] = args[0]
                kwargs['port'] = args[1]
            except IndexError:
                pass
        self.host, self.bitstream = get_hostname(**kwargs)

        # Need to check if any logger-based parameters have been spec'd
        self.getLogger = getLogger

        try:
            self.logger = kwargs['logger']
        except KeyError:
            # Damn
            result, self.logger = self.getLogger(name=self.host)
            if not result:
                # Problem
                if self.logger.handlers:
                    # Logger already exists
                    warningmsg = 'Logger for {} already exists'.format(self.host)
                    self.logger.warning(warningmsg)
                else:
                    errmsg = 'Problem creating logger for {}'.format(self.host)
                    raise ValueError(errmsg)

        # some transports, e.g. Skarab, need to know their parent
        kwargs['parent_fpga'] = self

        # Setup logger to be propagated through transports
        # either set log level manually or default to error
        try:
            self.set_log_level(log_level=kwargs['log_level'])
        except KeyError:
            self.set_log_level(log_level='ERROR')

        port = get_kwarg('port', kwargs)
        if not port:
          port = 7147

        # was the transport specified?
        transport = get_kwarg('transport', kwargs)
        if transport:
            self.transport = transport(**kwargs)
        else:
            transport_class = self.choose_transport(self.host, port)
            self.transport = transport_class(**kwargs)

        # this is just for code introspection
        self.devices = None
        self.memory_devices = None
        self.adc_devices = None
        self.other_devices = None
        self.sbrams = None
        self.qdrs = None
        self.hmcs = None
        self.registers = None
        self.gbes = None
        self.snapshots = None
        self.system_info = None
        self.rcs_info = None
        # /just for introspection

        self._reset_device_info()
        self.logger.debug('%s: now a CasperFpga' % self.host)

        # The Red Pitaya doesn't respect network-endianness. It should.
        # For now, detect this board so that an endianness flip can be
        # inserted between the CasperFpga and the underlying transport layer.
        # We try detection again after programming, in case this fails here.
        try:
            self._detect_little_endianness()
        except:
            pass

        # Store board ID as it may be used to make
        # comms decisions
        self.platform = PLATFORM_ID.get(self._get_platform_id(), None)
        self.transport.platform = self.platform
        
    def choose_transport(self, host_ip, port):
        """
        Test whether a given host is a katcp client or a skarab

        :param host_ip:
        """
        self.logger.debug('Trying to figure out what kind of device %s is' % host_ip)
        if host_ip.startswith('CasperDummy'):
            return DummyTransport
        try:
            if AlveoTransport.test_host_type(host_ip, port):
                self.logger.info('%s:%d seems to be an ALVEO' % (host_ip,port))
                return AlveoTransport
            elif KatcpTransport.test_host_type(host_ip):
                self.logger.info('%s seems to be ROACH' % host_ip)
                return KatcpTransport
            elif TapcpTransport.test_host_type(host_ip):
                self.logger.info('%s seems to be a TapcpTransport' % host_ip)
                return TapcpTransport
            else:
                errmsg = 'Possible that host does not follow one of the \
                            defined casperfpga transport protocols'
                raise UnknownTransportError(errmsg)
        except socket.gaierror:
            raise RuntimeError('Address/host %s makes no sense to '
                               'the OS?' % host_ip)
        #except Exception as e:
        #    raise RuntimeError('Could not connect to host {}: {}'.format(host_ip, str(e)))

    def connect(self, timeout=None):
        """
        Attempt to connect to a CASPER Hardware Target

        :param timeout: Integer value in seconds
        """
        return self.transport.connect(timeout)

    def disconnect(self):
        """
        Attempt to disconnect from a CASPER Hardware Target
        """
        return self.transport.disconnect()

    # def pdebug(self, message, *args, **kwargs):
    #     if self.isEnabledFor(PDEBUG):
    #         self.log(PDEBUG, message, *args, **kwargs)

    def set_log_level(self, log_level='DEBUG'):
        """
        Generic function to carry out a sanity check on the logging_level
        used to setup the logger

        :param log_level: String input defining the logging_level:
                           
                            Level      | Numeric Value
                            --------------------------
                            CRITICAL   | 50
                            ERROR      | 40
                            WARNING    | 30
                            INFO       | 20
                            DEBUG      | 10
                            NOTSET     | 0
        """
        log_level_numeric = getattr(logging, log_level.upper(), None)
        if not isinstance(log_level_numeric, int):
            raise ValueError('Invalid Log Level: %s' % log_level)
        # else: Continue
        self.logger.setLevel(log_level_numeric)
        infomsg = 'Log level successfully updated to: {}'.format(log_level.upper())
        self.logger.info(infomsg)
        return True

    def _get_platform_id(self):
        """
        Get the platform ID stored in the toolflow-generated system
        register `sys_board_id`
        """
        try:
            return self.read_uint("sys_board_id")
        except:
            return None

    def read(self, device_name, size, offset=0, **kwargs):
        """
        Read size-bytes of binary data with carriage-return escape-sequenced.

        :param device_name: name of memory device from which to read
        :param size: how many bytes to read
        :param offset: start at this offset, offset in bytes
        :param kwargs:
        """
        data = self.transport.read(device_name, size, offset, **kwargs)
        if self.is_little_endian:
            assert ((len(data) % 4) == 0), \
                "Can only read multiples of 4 bytes because CasperFpga is doing an endianness flip"
            # iterate through 32-bit words and flip them
            data_byte_swapped = b""
            for i in range(0, len(data), 4):
                data_byte_swapped += data[i:i+4][::-1]
            return data_byte_swapped
        return data

    def blindwrite(self, device_name, data, offset=0, **kwargs):
        if self.is_little_endian:
            assert ((len(data) % 4) == 0), \
                "Can only write multiples of 4 bytes because CasperFpga is doing an endianness flip"
            # iterate through 32-bit words and flip them
            data_byte_swapped = b""
            for i in range(0, len(data), 4):
                data_byte_swapped += data[i:i+4][::-1]
            return self.transport.blindwrite(device_name, data_byte_swapped, offset, **kwargs)
        return self.transport.blindwrite(device_name, data, offset, **kwargs)

    def listdev(self):
        """
        Get a list of the memory bus items in this design.

        :return: a list of memory devices
        """
        try:
            return self.transport.listdev()
        except AttributeError:
            return list(self.memory_devices.keys())

    def deprogram(self):
        """
        The child class will deprogram the FPGA, we just reset out
        device information
        """
        self.transport.deprogram()
        self._reset_device_info()

    def set_igmp_version(self, version):
        """
        Sets version of IGMP multicast protocol to use
        
        * This method is only available to katcp-objects
        
        :param version: IGMP protocol version, 0 for kernel default, 1, 2 or 3
        """
        return self.transport.set_igmp_version(version)

    def upload_to_ram_and_program(self, filename=None, wait_complete=True,
                                  initialise_objects=False, **kwargs):
        """
        Upload an FPG file to RAM and then program the FPGA.
        :param filename: The file to upload
        :param wait_complete: Do not wait for this operation, just return
        :param chunk_size: The SKARAB supports 1988, 3976 and 7952 byte programming packets,
                           but old bitfiles only support 1988 (the default)
                           after upload
        :param initialise_objects: Flag included in the event some child objects can be initialised
                                   upon creation/startup of the SKARAB with the new firmware
                                   - e.g. The SKARAB ADC
        :param **kwargs: chunk_size - set the chunk_size for the SKARAB platform
        :return: Boolean - True/False - Success/Fail
        """
        if filename is not None:
            self.bitstream = filename
        else:
            filename = self.bitstream

        rv = self.transport.upload_to_ram_and_program(
                filename=filename, wait_complete=wait_complete, **kwargs)

        if not wait_complete:
            return True

        # if the board returned after programming successfully, get_sys_info
        if rv:
            if self.bitstream:
                if self.bitstream[-3:] == 'fpg':
                    self.get_system_information(filename,
                                                initialise_objects=initialise_objects)


            # The Red Pitaya doesn't respect network-endianness. It should.
            # For now, detect this board so that an endianness flip can be
            # inserted between the CasperFpga and the underlying transport layer
            # This check is in upload_to_ram and program because if we connected
            # to a board that wasn't programmed the detection in __init__ won't have worked.
            try:
                self._detect_little_endianness()
            except:
                pass
            
            return True
        else:
            return False

    def is_connected(self, **kwargs):
        """
        Is the transport connected to the host?
        :return:
        """
        return self.transport.is_connected(**kwargs)

    def is_running(self):
        """
        Is the FPGA programmed and running?
        :return:
        """
        return self.transport.is_running()

    def _detect_little_endianness(self):
        """
        Return True if the board being used is little endian.
        False otherwise.
        This method works by interrogating the board_id of the system.
        It doesn't do anything truly generic, but looks to see if the
        MSB of the board ID is zero. If it isn't or the whole id is zero
        (which is the case for the red pitaya) we assume this is a little endian board.
        implicitly sets the is_little_endian attribute
        """
        self.is_little_endian = False
        board_id = self.read_uint('sys_board_id')
        msb = (board_id >> 24) & 0xff
        self.is_little_endian = (msb > 0) or (board_id == 0)
        return self.is_little_endian

    def _reset_device_info(self):
        """
        Reset information of devices this FPGA knows about.
        """
        # device dictionaries:
        #   devices: all of them
        #   memory_devices: only devices on the bus
        #   other_devices: anything not on the bus
        self.devices = {}
        self.memory_devices = {}
        self.adc_devices = {}
        self.other_devices = {}

        # containers
        for container_ in list(CASPER_MEMORY_DEVICES.values()):
            setattr(self, container_['container'], AttributeContainer())

        for container_ in list(CASPER_ADC_DEVICES.values()):
            setattr(self, container_['container'], AttributeContainer())

        # hold misc information about the bof file, program time, etc
        self.system_info = {}
        self.rcs_info = {}

    def test_connection(self):
        """
        Write to and read from the scratchpad to test the connection to the FPGA
        """
        return self.transport.test_connection()

    # def __getattribute__(self, name):
    #     if name == 'registers':
    #         return {self.memory_devices[r].name: self.memory_devices[r]
    #                 for r in self.memory_devices_memory['register']['items']}
    #     return object.__getattribute__(self, name)

    def dram_bulkread(self, device, size, offset):
        """

        :return:
        """
        raise NotImplementedError

    def read_dram(self, size, offset=0):
        """
        Reads data from a ROACH's DRAM. Reads are done up to 1MB at a time.
        The 64MB indirect address register is automatically incremented
        as necessary.

        It returns a string, as per the normal 'read' function.
        ROACH has a fixed device name for the DRAM (dram memory).
        Uses dram_bulkread internally.

        :param size: amount of data to read, in bytes
        :param offset: offset at which to read, in bytes
        :return: binary data string
        """
        data = []
        n_reads = 0
        last_dram_page = -1

        dram_indirect_page_size = (64 * 1024 * 1024)
        # read_chunk_size = (1024*1024)
        self.logger.debug('Reading a total of %8i bytes from offset %8i...' %
                         (size, offset))
        while n_reads < size:
            dram_page = (offset + n_reads) / dram_indirect_page_size
            local_offset = (offset + n_reads) % dram_indirect_page_size
            # local_reads = min(read_chunk_size, size - n_reads,
            #                   dram_indirect_page_size -
            #                   (offset % dram_indirect_page_size))
            local_reads = min(size - n_reads, dram_indirect_page_size -
                              (offset % dram_indirect_page_size))
            if last_dram_page != dram_page:
                self.write_int('dram_controller', dram_page)
                last_dram_page = dram_page
            local_data = self.dram_bulkread('dram_memory',
                                            local_reads, local_offset)
            data.append(local_data)
            self.logger.debug('Reading %8i bytes from indirect '
                              'address %4i at local offset %8i... done.' %
                              (local_reads, dram_page, local_offset))
            n_reads += local_reads
        return ''.join(data)

    def write_dram(self, data, offset=0):
        """
        Writes data to a ROACH's DRAM. Writes are done up to 512KiB at a time.
        The 64MB indirect address register is automatically
        incremented as necessary.
        ROACH has a fixed device name for the DRAM (dram memory) and so the
        user does not need to specify the write register.

        :param data: packed binary string data to write
        :param offset: the offset at which to write
        """
        size = len(data)
        n_writes = 0
        last_dram_page = -1

        dram_indirect_page_size = (64 * 1024 * 1024)
        write_chunk_size = (1024 * 512)
        self.logger.debug('Writing a total of %8i bytes from offset %8i...' %
                         (size, offset))

        while n_writes < size:
            dram_page = (offset + n_writes) / dram_indirect_page_size
            local_offset = (offset + n_writes) % dram_indirect_page_size
            local_writes = min(write_chunk_size, size - n_writes,
                               dram_indirect_page_size -
                               (offset % dram_indirect_page_size))
            self.logger.debug('Writing %8i bytes from indirect address %4i '
                              'at local offset %8i...' % (
                                  local_writes, dram_page, local_offset))
            if last_dram_page != dram_page:
                self.write_int('dram_controller', dram_page)
                last_dram_page = dram_page
            self.blindwrite(
                'dram_memory',
                data[n_writes:n_writes + local_writes], local_offset)
            n_writes += local_writes

    def write(self, device_name, data, offset=0):
        """
        Write data, then read it to confirm a successful write.

        :param device_name: memory device name to write
        :param data: packed binary data string to write
        :param offset: offset at which to write, in bytes
        """
        self.blindwrite(device_name, data, offset)
        new_data = self.read(device_name, len(data), offset)
        if new_data != data:
            # TODO - this error message won't show you the problem if
            # it's not in the first word
            unpacked_wrdata = struct.unpack('>L', data[0:4])[0]
            unpacked_rddata = struct.unpack('>L', new_data[0:4])[0]
            err_str = 'Verification of write to %s at offset %d failed. ' \
                      'Wrote 0x%08x... but got back 0x%08x.' % (
                          device_name, offset,
                          unpacked_wrdata, unpacked_rddata)
            self.logger.error(err_str)
            raise ValueError(err_str)

    def read_int(self, device_name, word_offset=0):
        """
        Read an integer from memory device.
        i.e. calls self.read(device_name, size=4, offset=0) and uses
        struct to unpack it into an integer

        :param device_name: device from which to read
        :param word_offset: the 32-bit word offset at which to read
        :return: signed 32-bit integer
        """
        data = self.read(device_name, 4, word_offset * 4)
        return struct.unpack('>i', data)[0]

    def read_uint(self, device_name, word_offset=0):
        """
        Read an unsigned integer from memory device.

        :param device_name: device from which to read
        :param word_offset: the 32-bit word offset at which to read
        :return: unsigned 32-bit integer
        """
        data = self.read(device_name, 4, word_offset * 4)
        return struct.unpack('>I', data)[0]

    def write_int(self, device_name, integer, blindwrite=False, word_offset=0):
        """
        Writes an integer to the device specified at the offset specified.
        A blind write is optional.

        :param device_name: device to be written
        :param integer: the integer to write
        :param blindwrite: True for blind write, default False
        :param word_offset: the offset at which to write, in 32-bit words
        """
        # careful of packing input data into 32 bit - check range: if
        # negative, must be signed int; if positive over 2^16, must be unsigned
        # int.
        try:
            data = struct.pack('>i' if integer < 0 else '>I', integer)
        except Exception as ve:
            self.logger.error('Writing integer {} failed with error: {}'.format(
                integer, ve))
            raise ValueError('Writing integer {} failed with error: {}'.format(
                integer, ve))
        if blindwrite:
            self.blindwrite(device_name, data, word_offset * 4)
        else:
            self.write(device_name, data, word_offset * 4)
        self.logger.debug('Write_int %8x to register %s at word offset %d '
                          'okay%s.' % (integer, device_name, word_offset,
                          ' (blind)' if blindwrite else ''))

    def _create_memory_devices(self, device_dict, memorymap_dict, legacy_reg_map=True, **kwargs):
        """
        Create memory devices from dictionaries of design information.
        
        :param device_dict: raw dictionary of information from tagged
            blocks in Simulink design, keyed on device name
        :param memorymap_dict: dictionary of information that would have been
            in coreinfo.tab - memory bus information
        """

        # create and add memory devices to the memory device dictionary
        for device_name, device_info in list(device_dict.items()):
            self.logger.debug("Creating memory device: %s" % device_name)
            if device_name == '':
                raise NameError('There\'s a problem somewhere, got a blank '
                                'device name?')
            if device_name in list(self.memory_devices.keys()):
                raise NameError('Memory device %s already exists' % device_name)
            # get the class from the known devices, if it exists there
            tag = device_info['tag']
            try:
                known_device_class = CASPER_MEMORY_DEVICES[tag]['class']
                known_device_container = CASPER_MEMORY_DEVICES[tag]['container']
            except KeyError:
                pass
            else:
                if not isinstance(known_device_class, Callable):
                    raise TypeError('%s is not a callable Memory class - '
                                    'that\'s a problem.' % known_device_class)

                new_device = known_device_class.from_device_info(
                    self, device_name, device_info, memorymap_dict, legacy_reg_map=legacy_reg_map)
                if new_device.name in list(self.memory_devices.keys()):
                    raise NameError(
                        'Device called %s of type %s already exists in '
                        'devices list.' % (new_device.name, type(new_device)))
                self.devices[device_name] = new_device
                self.memory_devices[device_name] = new_device
                container = getattr(self, known_device_container)
                setattr(container, device_name, new_device)
                assert id(getattr(container, device_name)) == id(new_device)
                assert id(new_device) == id(self.memory_devices[device_name])
            self.logger.debug("Done creating memory device: %s" % device_name)
        # allow created devices to update themselves with full device info
        # link control registers, etc
        for name, device in list(self.memory_devices.items()):
            self.logger.debug("Running post_create_update for device: %s" % name)
            try:
                device.post_create_update(device_dict)
            except AttributeError:  # the device may not have an update function
                pass
        # Add any memory entries which are not associated with known devices.
        for name, memdevice in memorymap_dict.items():
            # If this is already in the device list, skip. It should have
            # been added as a sw_reg / other thing already
            # Else, add the device as a vanilla register
            if name in device_dict.keys():
                pass
            else:
                if name in self.memory_devices:
                    raise NameError("Device named %s is already in memory device list" % name)
                self.memory_devices[name] = Memory(name, 32, memdevice['address'], memdevice['bytes'])

    def _create_casper_device_by_regname(self, device_dict):
        if 'sysmon' in device_dict:
            self.sensors = sysmon.Sysmon(self)

    def _create_casper_adc_devices(self, device_dict, initialise=False, **kwargs):
        """
        New method to instantiate CASPER ADC objects and attach them to the
        parent CasperFpga object
        :param device_dict: raw dictionary of information from tagged
        blocks in Simulink design, keyed on device name
        :param initialise: Flag included in the event some child objects can be initialised
                           upon creation/startup of the SKARAB with the new firmware
                           - e.g. The SKARAB ADC's PLL SYNC
        :return: None
        """
        for device_name, device_info in list(device_dict.items()):

            if device_name == '':
                raise NameError('There\'s a problem somewhere, got a blank '
                                'device name?')
            if device_name in list(self.adc_devices.keys()):
                raise NameError('ADC device %s already exists' % device_name)
            # get the class from the known devices, if it exists there
            tag = device_info['tag']
            try:
                known_device_class = CASPER_ADC_DEVICES[tag]['class']
                known_device_container = CASPER_ADC_DEVICES[tag]['container']
            except KeyError:
                pass
            else:
                if not isinstance(known_device_class, Callable):
                    errmsg = '{} is not a callable ADC Class'.format(known_device_class)
                    raise TypeError(errmsg)

                new_device = known_device_class.from_device_info(self,
                                device_name, device_info, initialise=initialise)

                if new_device.name in list(self.adc_devices.keys()):
                    errmsg = 'Device {} of type {} already exists in \
                             the devices list'.format(new_device.name, type(new_device))

                    raise NameError(errmsg)

                #new_device = known_device_class.from_device_info(self, initialize=initialise)
                
                self.devices[device_name] = new_device
                self.adc_devices[device_name] = new_device

                container = getattr(self, known_device_container)
                setattr(container, device_name, new_device)
                
                assert id(getattr(container, device_name)) == id(new_device)
                assert id(new_device) == id(self.adc_devices[device_name])
        

    def _create_other_devices(self, device_dict, **kwargs):
        """
        Store non-memory device information in a dictionary

        :param device_dict: raw dictionary of information from tagged
            blocks in Simulink design, keyed on device name
        """
        for device_name, device_info in list(device_dict.items()):
            if device_name == '':
                raise NameError('There\'s a problem somewhere, got a '
                                'blank device name?')
            if device_name in list(self.other_devices.keys()):
                raise NameError('Other device %s already exists.' % device_name)
            if device_info['tag'] in list(CASPER_OTHER_DEVICES.keys()):
                self.devices[device_name] = device_info
                self.other_devices[device_name] = device_info

    def device_names_by_container(self, container_name):
        """
        Return a list of devices in a certain container.
        
        :param container_name: String to search for in containers in memory_devices
        :return: List of strings matching the description
        """
        return [devname for devname, container
                in self.memory_devices.items()
                if container == container_name]

    def devices_by_container(self, container):
        """
        Get devices using container type.
        """
        return getattr(self, container)

    def get_system_information(self, filename=None, fpg_info=None,
                               initialise_objects=False, **kwargs):
        """
        Get information about the design running on the FPGA.
        If filename is given, get it from file, otherwise query the
            host via KATCP.
        :param filename: fpg filename
        :param fpg_info: a tuple containing device_info and coreinfo
                         dictionaries
        :param initialise_objects: Flag included in the event some child objects can be initialised
                                   upon creation/startup of the SKARAB with the new firmware
                                   - e.g. The SKARAB ADC's PLL SYNC
        :return: <nothing> the information is populated in the class
        """
        if (filename is None):
            t_filename, t_fpg_info = \
                self.transport.get_system_information_from_transport()
            filename = t_filename
            fpg_info = t_fpg_info
        if (filename is None) and (fpg_info is None):
            raise RuntimeError('Either filename or parsed fpg data '
                               'must be given.')
        if filename is not None:
            # device_dict is a dictionary of XPS-tagged devices, keyed by
            # block name, with values reflecting yellow block parameters.
            # memorymap_dict is a list of registers, keyed by register name, 
            # with vales {'address': <address>, 'bytes': <size in bytes>
            device_dict, memorymap_dict = parse_fpg(filename)
        else:
            device_dict = fpg_info[0]
            memorymap_dict = fpg_info[1]
        # add system registers
        device_dict.update(self._add_sys_registers())
        # reset current devices and create new ones from the new
        # design information
        self._reset_device_info()

        # populate some system information
        try:
            self.system_info.update(device_dict['77777'])
        except KeyError:
            self.logger.warn('No sys info key in design info!')
        # and RCS information if included
        for device_name in device_dict:
            if device_name.startswith('77777_git'):
                if 'git' not in self.rcs_info:
                    self.rcs_info['git'] = {}
                self.rcs_info['git'].update(device_dict[device_name])

            if device_name.startswith('77777_svn'):
                if 'svn' not in self.rcs_info:
                    self.rcs_info['svn'] = {}
                self.rcs_info['svn'].update(device_dict[device_name])

        try:
            self.rcs_info['git'].pop('tag')
        except:
            pass
        # Create Register Map
        self.logger.info("Creating memory devices")
        self._create_memory_devices(device_dict, memorymap_dict,
                                    initialise=initialise_objects)
        self.logger.info("Creating other devices")
        self._create_other_devices(device_dict, initialise=initialise_objects)
        self.logger.info("Creating ADC devices")
        self._create_casper_adc_devices(device_dict, initialise=initialise_objects)
        self.transport.memory_devices = self.memory_devices
        self.logger.info("Creating devices by register name")
        self._create_casper_device_by_regname(device_dict)
        self.logger.info("Running post_get_system_information")
        self.transport.post_get_system_information()
        # we may not have been able to detect endianness until now
        # that we know the register map
        self.logger.info("Detecting endianness")
        try:
            self._detect_little_endianness()
        except:
            pass

    def estimate_fpga_clock(self):
        """
        Get the estimated clock of the running FPGA, in Mhz.

        :return: Float - FPGA Clock speed in MHz
        """
        firstpass = self.read_uint('sys_clkcounter')
        time.sleep(2.0)
        secondpass = self.read_uint('sys_clkcounter')
        if firstpass > secondpass:
            secondpass += (2**32)
        return (secondpass - firstpass) / 2000000.0

    def check_tx_raw(self, wait_time=0.2, checks=10):
        """
        Check to see whether this host is transmitting packets without
        error on all its GbE interfaces.

        :param wait_time: seconds to wait between checks
        :param checks: times to run check
        :return: Boolean - True/False - Success/Fail
        """
        for gbecore in self.gbes:
            if not gbecore.tx_okay(wait_time=wait_time, checks=checks):
                return False
        return True

    def check_rx_raw(self, wait_time=0.2, checks=10):
        """
        Check to see whether this host is receiving packets without
        error on all its GbE interfaces.

        :param wait_time: seconds to wait between checks
        :param checks: times to run check
        """
        for gbecore in self.gbes:
            if not gbecore.rx_okay(wait_time=wait_time, checks=checks):
                return False
        return True

    @staticmethod
    def _add_sys_registers():
        standard_reg = {'tag': 'xps:sw_reg', 'io_dir': 'To Processor',
                        'io_delay': '1', 'sample_period': '1', 'names': 'reg',
                        'bitwidths': '32', 'bin_pts': '0', 'arith_types': '0',
                        'sim_port': 'off', 'show_format': 'off', }
        sys_registers = {'sys_board_id': standard_reg.copy(),
                         'sys_rev': standard_reg.copy(),
                         'sys_rev_rcs': standard_reg.copy(),
                         'sys_scratchpad': standard_reg.copy(),
                         'sys_clkcounter': standard_reg.copy()}
        return sys_registers

    def get_version_info(self):
        """
        :return: List
        """
        if 'git' not in self.rcs_info:
            return []
        if len(self.rcs_info['git']) == 0:
            return []
        git_info = self.rcs_info['git']
        files = list(git_info.keys())
        old_version = hasattr(git_info[files[0]], 'keys')
        if old_version:
            rv = []
            for filename in files:
                if 'git_info_found' in list(git_info[filename].keys()):
                    if git_info[filename]['git_info_found'] == ['1']:
                        rv.append(
                            (filename, git_info[filename]['commit_hash'][0] +
                             '_' + git_info[filename]['status'][0]))
                    continue
                rv.append((filename, str(git_info[filename])))
            return rv
        else:
            return [(f, git_info[f]) for f in files if f != 'tag']

    def __str__(self):
        """
]
        """
        return self.host

# end
