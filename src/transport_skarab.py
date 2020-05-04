import socket
import math
import select
import logging
import struct
import time
import os
import random
import contextlib

from threading import Lock

from . import skarab_definitions as sd
from . import skarab_fileops as skfops
from .transport import Transport
from .network import IpAddress
from .utils import socket_closer

__author__ = 'tyronevb'
__date__ = 'April 2016'


# region -- Custom Errors and Return Values for SKARAB --

class SkarabSendPacketError(ValueError):
    pass


class SkarabUploadChecksumMismatch(ValueError):
    pass


class SkarabSdramError(RuntimeError):
    pass


class SkarabInvalidResponse(ValueError):
    pass


class SkarabReadFailed(ValueError):
    pass


class SkarabWriteFailed(ValueError):
    pass


class SkarabSequenceSetError(RuntimeError):
    pass


class SkarabUnknownDeviceError(ValueError):
    pass


class SkarabResponseNotReceivedError(RuntimeError):
    pass


class SkarabReorderWarning(ValueError):
    pass


class SkarabReorderError(ValueError):
    pass


class SkarabSpeadWarning(ValueError):
    pass


class SkarabSpeadError(ValueError):
    pass


class SkarabInvalidHostname(RuntimeError):
    pass

class InvalidDeviceType(ValueError):
    pass

class SkarabFanControllerClearError(ValueError):
    pass

class NonVolatileLogRetrievalError(ValueError):
    pass

class SkarabProcessorVersionError(ValueError):
    pass

# endregion


class SkarabTransport(Transport):
    """
    The network transport for a SKARAB-type interface.
    """

    def __init__(self, **kwargs):
        """
        Initialized SKARAB FPGA object

        :param host: IP Address of the targeted SKARAB Board
        :param parent_fpga: Instance of parent_fpga
        :param timeout: Send packet timeout in seconds,
                        defaults to CONTROL_RESPONSE_TIMEOUT
                        in skarab_definitions.py
        :param retries: Send packet retries, defaults to
                        CONTROL_RESPONSE_RETRIES in skarab_definitions.py
        :param blocking: True (default)/False. If True a SKARAB comms
                         check will be performed. If False only the
                         instance will be created.
        """
        Transport.__init__(self, **kwargs)

        try:
            # Entry point is always via casperfpga.CasperFpga
            self.parent = kwargs['parent_fpga']
            self.logger = self.parent.logger
        except KeyError:
            errmsg = 'parent_fpga argument not supplied when creating skarab'
            # Pointless trying to log to a logger
            raise RuntimeError(errmsg)

        new_connection_msg = '*** NEW CONNECTION MADE TO {} ***'.format(self.host)
        self.logger.debug(new_connection_msg)

        try:
            self.timeout = kwargs['timeout']
        except KeyError:
            self.timeout = sd.CONTROL_RESPONSE_TIMEOUT
        try:
            self.retries = kwargs['retries']
        except KeyError:
            self.retries = sd.CONTROL_RESPONSE_RETRIES
        try:
            self.blocking = kwargs['blocking']
        except KeyError:
            self.blocking = True

        # sequence number for control packets
        self._seq_num = None
        self.reset_seq_num()

        # create tuple for ethernet control packet address
        self.skarab_eth_ctrl_addr = (
            self.host, sd.ETHERNET_CONTROL_PORT_ADDRESS)

        # create tuple for fabric packet address
        self.skarab_fpga_addr = (self.host, sd.ETHERNET_FABRIC_PORT_ADDRESS)

        # flag for keeping track of SDRAM state
        self._sdram_programmed = False

        # dict for sensor data, empty at initialization
        self.sensor_data = {}

        # create, and connect to, a socket for the skarab object
        self._skarab_control_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._skarab_control_sock.connect(self.skarab_eth_ctrl_addr)
        except socket.gaierror:
            errmsg = 'Hostname invalid, check leases or resource-list'
            self.logger.error(errmsg)
            raise SkarabInvalidHostname(errmsg)

        self._skarab_control_sock.setblocking(0)
        self._lock=Lock()

        # check if connected to host
        if self.blocking:
            if self.is_connected():
                self.logger.info('Port({}) created & connected.'.format(
                    sd.ETHERNET_CONTROL_PORT_ADDRESS))
            else:
                self.logger.error('Error connecting to {}: port {}'.format(self.host,
                    sd.ETHERNET_CONTROL_PORT_ADDRESS))

        # self.image_chunks, self.local_checksum = None, None
        # TODO - add the one_gbe
        # self.gbes = []
        # self.gbes.append(FortyGbe(self, 0))
        # # self.gbes.append(FortyGbe(self, 0, 0x54000 - 0x4000))

    def __del__(self):
        socket_closer("class SkarabTransport __del__", self._skarab_control_sock)

    @staticmethod
    def test_host_type(host_ip):
        """
        Is a given IP assigned to a SKARAB?

        :param host_ip:
        """
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as sctrl_sock:
            sctrl_sock.setblocking(0)
            skarab_eth_ctrl_port = (host_ip, sd.ETHERNET_CONTROL_PORT_ADDRESS)
            request_object = sd.ReadRegReq(sd.BOARD_REG, sd.C_RD_VERSION_ADDR)
            request_payload = request_object.create_payload(0xffff)
            sctrl_sock.sendto(request_payload, skarab_eth_ctrl_port)
            data_ready = select.select([sctrl_sock], [], [], 1)
            socket_closer("test_host_type", sctrl_sock)
            if len(data_ready[0]) > 0:
                # self.logger.debug('%s seems to be a SKARAB' % host_ip)
                return True
        return False

    def is_connected(self,
                     timeout=None,
                     retries=None):
        """
        'ping' the board to see if it is connected and running.
        Tries to read a register

        :return: Boolean - True/False - Succes/Fail
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        try:
            data = self.read_board_reg(sd.C_RD_VERSION_ADDR, retries=retries,
                                       timeout=timeout)
            return True if data else False
        except ValueError as vexc:
            self.logger.debug('Skarab is not connected: {}'.format(vexc))
            return False

    def is_running(self):
        """
        Is the FPGA programmed and running a toolflow image?

        :return: True or False
        """
        [golden_img, multiboot, version] = self.get_virtex7_firmware_version()
        if golden_img == 0 and multiboot == 0:
            return True
        return False

    def loopbacktest(self, iface, timeout=None,
                     retries=None):
        """
        Run the loopback test.

        :param iface:
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        request = sd.DebugLoopbackTestReq(iface, 0x77)
        response = self.send_packet(request, timeout=timeout, retries=retries)
        raise RuntimeError('Not yet tested')

    def _get_device_address(self, device_name):
        # map device name to address, if can't find, bail
        if device_name in self.memory_devices:
            return self.memory_devices[device_name].address
        elif (type(device_name) == int) and (0 <= device_name < 2 ** 32):
            # also support absolute address values
            self.logger.warning('Absolute address given: 0x%06x' % device_name)
            return device_name
        errmsg = 'Could not find device: %s' % device_name
        self.logger.error(errmsg)
        raise SkarabUnknownDeviceError(errmsg)

    def read(self, device_name, size, offset=0, use_bulk=True,
             timeout=None,
             retries=None):
        """
        Read size-bytes of binary data with carriage-return escape-sequenced.

        :param device_name: name of memory device from which to read
        :param size: how many bytes to read
        :param offset: start at this offset, offset in bytes
        :param use_bulk: use the bulk read function
        :param timeout: value in seconds to wait before aborting instruction
                        - Default value is None, uses initialised value
        :param retries: value specifying number of retries should instruction fail
                        - Default value is None, uses initialised value
        :return: binary data string
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        if (size > 4) and use_bulk:
            # use a bulk read if more than 4 bytes are requested
            return self._bulk_read(device_name, size, offset)
        addr = self._get_device_address(device_name)
        # can only read 4 bytes at a time
        # work out how many reads we require, and from where
        offset_bytes = int(offset / 4) * 4
        offset_diff = offset - offset_bytes
        num_bytes_corrected = size + offset_diff
        num_reads = int(math.ceil(num_bytes_corrected / 4.0))
        addr_start = addr + offset - offset_diff
        # self.logger.info('size(%i) offset(%i) addr(0x%06x) => '
        #             'offset_corrected(%i) size_corrected(%i) '
        #             'addr_start(0x%06x) numreads(%i)' % (
        #     size, offset, addr, offset_bytes, num_bytes_corrected,
        #     addr_start, num_reads))
        # address to read is starting address plus offset
        data = ''
        for readctr in range(num_reads):
            addr_high, addr_low = self.data_split_and_pack(addr_start)
            request = sd.ReadWishboneReq(addr_high, addr_low)
            response = self.send_packet(request, timeout=timeout, retries=retries)
            # merge high and low binary data for the current read
            read_low = struct.pack('!H', response.packet['read_data_low'])
            read_high = struct.pack('!H', response.packet['read_data_high'])
            new_read = read_high + read_low
            # append current read to read data
            data += new_read
            # increment addr_start by four
            addr_start += 4
        # return the number of bytes requested
        return data[offset_diff: offset_diff + size]

    def _bulk_read_req(self, address, words_to_read,
                       timeout=None,
                       retries=None):
        """

        :param address: the address at which to read
        :param words_to_read: how many 32-bit words should be read
        :return: binary data string
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        # self.logger.info('reading @ 0x%06x - %i words' % (address, words_to_read))
        if words_to_read > sd.MAX_READ_32WORDS:
            raise RuntimeError('Cannot read more than %i words - '
                               'asked for %i' % (sd.MAX_READ_32WORDS,
                                                 words_to_read))
        start_addr_high, start_addr_low = self.data_split_and_pack(address)
        # the uBlaze will only read as much as you tell it to, but will
        # return the the whole lot, zeros in the rest
        request = sd.BigReadWishboneReq(start_addr_high, start_addr_low,
                                        words_to_read)
        response = self.send_packet(request, timeout=timeout, retries=retries)
        if response is None:
            errmsg = 'Bulk read failed.'
            raise SkarabReadFailed(errmsg)
        # response.read_data is a list of 16-bit words, pack it
        read_data = response.packet['read_data'][0:words_to_read*2]
        return struct.pack('>%iH' % len(read_data), *read_data)

    def _bulk_read(self, device_name, size, offset=0):
        """
        Read size-bytes of binary data with carriage-return escape-sequenced.

        :param device_name: name of memory device from which to read
        :param size: how many bytes to read
        :param offset: start at this offset, offset in bytes
        :return: binary data string
        """
        addr = self._get_device_address(device_name)
        # self.logger.info('addr(0x%06x) size(%i) offset(%i)' % (addr, size,
        # offset))
        bounded_offset = int(math.floor(offset / 4.0) * 4.0)
        offset_diff = offset - bounded_offset
        # self.logger.info('bounded_offset(%i)' % bounded_offset)
        addr += bounded_offset
        size += offset_diff
        # self.logger.info('offset_addr(0x%06x) offset_size(%i)' % (addr, size))
        num_words_to_read = int(math.ceil(size / 4.0))
        maxreadwords = 1.0 * sd.MAX_READ_32WORDS
        num_reads = int(math.ceil(num_words_to_read / maxreadwords))
        # self.logger.info('words_to_read(0x%06x) loops(%i)' % (num_words_to_read,
        #                                                  num_reads))
        data = ''
        data_left = num_words_to_read
        for rdctr in range(num_reads):
            to_read = (sd.MAX_READ_32WORDS if data_left > sd.MAX_READ_32WORDS
                       else data_left)
            data += self._bulk_read_req(addr, to_read)
            data_left -= sd.MAX_READ_32WORDS
            addr += to_read * 4
        # self.logger.info('returning data[%i:%i]' % (offset_diff, size))
        # return the number of bytes requested
        return data[offset_diff: size]

    def _bulk_write_req(self, address, data, words_to_write,
                        timeout=None,
                        retries=None):
        """
        Unchecked data write. Maximum of 1988 bytes per transaction

        :param address: memory device to which to write
        :param data: byte string to write
        :param words_to_write: number of 32-bit words to write
        :return: number of 32-bit writes done
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        if words_to_write > sd.MAX_WRITE_32WORDS:
            raise RuntimeError('Cannot write more than %i words - '
                               'asked to write %i' % (sd.MAX_WRITE_32WORDS,
                                                      words_to_write))
        start_addr_high, start_addr_low = self.data_split_and_pack(address)
        self.logger.debug('\nAddress High: {}\nAddress Low: {}'
                     '\nWords To Write: {}'.format(repr(start_addr_high),
                                                   repr(start_addr_low),
                                                   words_to_write))
        request = sd.BigWriteWishboneReq(start_addr_high,
                                         start_addr_low, data, words_to_write)
        response = self.send_packet(request, timeout=timeout, retries=retries)
        if response is None:
            errmsg = 'Bulk write failed. No response from SKARAB.'
            raise SkarabWriteFailed(errmsg)
        if response.packet['number_of_writes_done'] != words_to_write:
            errmsg = 'Bulk write failed. Not all words written.'
            raise SkarabWriteFailed(errmsg)

        self.logger.debug('Number of writes dones: %d' %
                     response.packet['number_of_writes_done'])

        return response.packet['number_of_writes_done']

    def _bulk_write(self, device_name, data, offset):
        """
        Data write. Supports > 4 bytes written per transaction.

        :param device_name: memory device to which to write
        :param data: byte string to write
        :param offset: the offset, in bytes, at which to write
        """

        # TODO: writing data not bounded to 32-bit words
        # will have to read back the data and then apply a mask with the new
        #  data
        # i.e. have 0X01ABCDEF, want to write 0xFF to the 1st bytes
        # need to read back 0x01ABCDEF, then mask the first byte ONLY
        # and write back 0xFFABCDEF, for now, only support 32-bit boundary

        address = self._get_device_address(device_name)
        size = len(data)  # number of bytes in the write data

        bounded_offset = int(math.floor(offset / 4.0) * 4.0)
        offset_diff = offset - bounded_offset

        address += bounded_offset
        size += offset_diff

        num_words_to_write = int(math.ceil(size / 4.0))
        max_write_words = 1.0 * sd.MAX_WRITE_32WORDS
        num_writes = int(math.ceil(num_words_to_write / max_write_words))
        self.logger.debug('words_to_write(%i) loops(%i)' % (num_words_to_write,
                                                            num_writes))
        write_data_left = num_words_to_write
        data_start = 0
        number_of_writes_done = 0
        for wrctr in range(num_writes):
            self.logger.debug('In write loop %i' % wrctr)
            # determine the number of 32-bit words to write
            to_write = (sd.MAX_WRITE_32WORDS if write_data_left >
                        sd.MAX_WRITE_32WORDS
                        else write_data_left)

            self.logger.debug('words to write ..... %i' % to_write)

            # get the data that is to be written in the next transaction
            write_data = data[data_start: data_start + to_write*4]
            self.logger.debug('Write Data Size: %i' % (len(write_data)/4))

            if to_write < sd.MAX_WRITE_32WORDS:
                # if writing less than the max number of words we need to pad
                # to the request packet size
                padding = (sd.MAX_READ_32WORDS - to_write)
                self.logger.debug('we are padding . . . %i . . . 32-bit words . . '
                             '.' % padding)
                write_data += '\x00\x00\x00\x00' * padding

            number_of_writes_done += self._bulk_write_req(address, write_data,
                                                          to_write)
            write_data_left -= to_write
            # increment address and point to start of next 32-bit word
            address += to_write * 4
            data_start += to_write * 4

        self.logger.debug('Number of writes dones: %d' % number_of_writes_done)
        if number_of_writes_done != num_words_to_write:
            errmsg = 'Bulk write failed. Only %i . . . of %i . . . 32-bit ' \
                     'words written' % (number_of_writes_done,
                                        num_words_to_write)
            raise SkarabWriteFailed(errmsg)

    def read_byte_level(self, device_name, size, offset=0,
                        timeout=None,
                        retries=None):
        """
        Byte-level read. Sorts out reads overlapping registers, and
        reading specific bytes.

        Read size-bytes of binary data with carriage-return escape-sequenced.

        :param device_name: name of memory device from which to read
        :param size: how many bytes to read
        :param offset: start at this offset
        :param timeout: value in seconds to wait before aborting instruction
                        - Default value is None, uses initialised value
        :param retries: value specifying number of retries should instruction fail
                        - Default value is None, uses initialised value
        :return: binary data string
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        # can only read 32-bits (4 bytes) at a time
        # work out how many reads we require, each read req reads a 32-bit reg
        # need to determine how many registers need to be read
        num_reads = int(math.ceil((offset + size) / 4.0))

        # string to store binary data read
        data = ''

        # address to read is starting address plus offset
        addr = device_name + offset
        for readctr in range(num_reads):
            # get correct address and pack into binary format
            # TODO: sort out memory mapping of device_name
            addr_high, addr_low = self.data_split_and_pack(addr)
            request = sd.ReadWishboneReq(addr_high, addr_low)
            response = self.send_packet(request, timeout=timeout, retries=retries)
            # merge high and low binary data for the current read
            read_high = struct.pack('!H', response.packet['read_data_high'])
            read_low = struct.pack('!H', response.packet['read_data_low'])
            new_read = read_high + read_low
            # append current read to read data
            data += new_read
            # increment addr by 4 to read the next 4 bytes (next 32-bit reg)
            addr += 4
        # return the number of bytes requested
        return data[offset:offset + size]

    def blindwrite(self, device_name, data, offset=0, use_bulk=True,
                   timeout=None,
                   retries=None):
        """
        Unchecked data write.

        :param device_name: the memory device to which to write
        :param data: the byte string to write
        :param offset: the offset, in bytes, at which to write
        :param use_bulk: use the bulk write function
        :param timeout: value in seconds to wait before aborting instruction
                        - Default value is None, uses initialised value
        :param retries: value specifying number of retries should instruction fail
                        - Default value is None, uses initialised value
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        assert (type(data) == str), 'Must supply binary packed string data'
        assert (len(data) % 4 == 0), 'Must write 32-bit-bounded words'
        assert (offset % 4 == 0), 'Must write 32-bit-bounded words'

        if (len(data) > 4) and use_bulk:
            # use a bulk write if more than 4 bytes are to be written
            self._bulk_write(device_name, data, offset)
        else:

            # map device name to address, if can't find, bail
            addr = self._get_device_address(device_name)

            # split the data into two 16-bit words
            data_high = data[:2]
            data_low = data[2:]
            addr += offset
            addr_high, addr_low = self.data_split_and_pack(addr)
            request = sd.WriteWishboneReq(addr_high, addr_low,
                                          data_high, data_low)
            self.send_packet(request, timeout=timeout, retries=retries)

    def deprogram(self):
        """
        Deprogram the FPGA.
        This actually reboots & boots from the Golden Image
        """
        # trigger reboot of FPGA
        self.reboot_fpga()
        self.logger.info('Skarab deprogrammed okay')

    def program_from_flash(self):
        """
        Program the FPGA from flash memory.
        This is achieved with a reboot of the board.
        The SKARAB boots from flash on start up.
        """
        self.reboot_fpga()

    def boot_from_sdram(self):
        """
        Triggers a reboot of the Virtex7 FPGA and boot from SDRAM.
        """
        # check if sdram was programmed prior
        if not self._sdram_programmed:
            errmsg = 'SDRAM not programmed.'
            self.logger.error(errmsg)
            raise SkarabSdramError(errmsg)
        # trigger reboot
        self._complete_sdram_configuration()
        # self.logger.info('Booting from SDRAM.')
        # clear sdram programmed flag
        self._sdram_programmed = False
        # still update programming info
        self.prog_info['last_programmed'] = self.prog_info['last_uploaded']
        self.prog_info['last_uploaded'] = ''

    def upload_to_ram(self, filename, verify=True, chunk_size=1988):
        """
        Upload a bitstream to the SKARAB via the wishone --> SDRAM interface

        :param filename: fpga image to upload
        :param verify: calculate the hash of the local file and compare it
        to the stored one.
        :return: Boolean - True/False - Success/Fail
        """
        # Make sure filename isn't empty
        if filename == '' or filename is None:
            # Problem
            errmsg = 'Filename not specified!'
            self.logger.error(errmsg)
            raise ValueError(errmsg)
        # else: Check if the file exists
        abs_path = os.path.abspath(filename)
        if not os.path.exists(abs_path):
            # Problem
            errmsg = '{} does not exist'.format(filename)
            self.logger.error(errmsg)
            raise ValueError(errmsg)
        # else: Continue!

        upload_time = skfops.upload_to_ram_progska(filename, [self.parent], chunk_size)
        self.logger.debug('Uploaded bitstream in %.1f seconds.' % upload_time)
        return upload_time

    def check_running_firmware(self, timeout=None, retries=None):
        """
        Check what image is running on the FPGA and its corresponding
        firmware version.

        :param timeout: value in seconds to wait before aborting instruction
                        - Default value is None, uses initialised value
        :param retries: value specifying number of retries should instruction fail
                        - Default value is None, uses initialised value
        :return: Tuple - (Boolean, String) where:
                       -> Boolean is True if Toolflow Image, False otherwise
                       -> String is the firmware version
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        [golden_image, multiboot, firmware_version] = \
            self.get_virtex7_firmware_version(timeout=timeout, retries=retries)
        if golden_image == 0 and multiboot == 0:
            return True, firmware_version
        elif golden_image == 1 and multiboot == 0:
            self.logger.error(
                'Skarab is back up, but fell back to golden image with '
                'firmware version %s' % firmware_version)
            return False, firmware_version
        elif golden_image == 0 and multiboot == 1:
            self.logger.error(
                'Skarab is back up, but fell back to multiboot image with '
                'firmware version %s' % firmware_version)
            return False, firmware_version
        else:
            self.logger.error(
                'Skarab is back up, but unknown image with firmware '
                'version number %s' % firmware_version)
            return False, firmware_version

    def upload_to_ram_and_program(self, filename, port=-1, timeout=60,
                                  wait_complete=True, skip_verification=False, chunk_size=1988):
        """
        Uploads an FPGA image to the SDRAM, and triggers a reboot to boot
        from the new image.

        *** WARNING: Do NOT attempt to upload a BSP/Flash image to the SDRAM.

        :param filename: fpga image to upload (currently supports bin, bit
                         and hex files)
        :param port: the port to use on the rx end, -1 means a random port
        :param timeout: how long to wait, seconds
        :param wait_complete - wait for the board to boot after programming
        :param skip_verification - do not verify the image after upload
        :return: Boolean - True/False - Succes/Fail
        """
        #print('skarab_transport')
        print(chunk_size)
        try:
            upload_time = self.upload_to_ram(filename, not skip_verification, chunk_size)
            #print("completed fine")
        except:
            #print("failed to program")
            self.logger.error('Failed to program.')
            raise
        if not wait_complete:
            self.logger.debug('Returning immediately after programming.')
            return True
        self.boot_from_sdram()
        # wait for board to come back up
        timeout = timeout + time.time()
        reboot_start_time = time.time()
        while timeout > time.time():
            if self.is_connected(retries=1):
                # # configure the mux back to user_date mode
                # self.config_prog_mux(user_data=1)
                result, firmware_version = self.check_running_firmware()
                if result:
                    reboot_time = time.time() - reboot_start_time
                    self.logger.info(
                        'Skarab is back up, in %.1f seconds (%.1f + %.1f) with FW ver '
                        '%s' % (upload_time + reboot_time, upload_time, reboot_time,
                                firmware_version))
                    return True
                else:
                    return False
            time.sleep(0.1)

        self.logger.error('Skarab has not come back after programming')
        return False

    def clear_sdram(self):
        """
        Clears the last uploaded image from the SDRAM.
        Clears sdram programmed flag.
        """
        # clear sdram and ethernet counters
        self.sdram_reconfigure(clear_sdram=True, clear_eth_stats=True)

        # clear sdram programmed flag
        self._sdram_programmed = False

        # clear prog_info for last uploaded
        self.prog_info['last_uploaded'] = ''
        self.logger.debug('SDRAM for host {host} '
                          'cleared'.format(host=self.host))

    @staticmethod
    def data_split_and_pack(data):
        """
        Splits 32-bit data into 2 16-bit words:

            * dataHigh: most significant 2 bytes of data
            * dataLow: least significant 2 bytes of data

        Also packs the data into a binary string for network transmission

        :param data: 32 bit data to be split
        :return: Tuple - dataHigh, dataLow (packed into binary data string)
        """
        packer = struct.Struct('!I')
        packed_data = packer.pack(data)

        data_high = packed_data[:2]
        data_low = packed_data[-2:]

        return data_high, data_low

    @staticmethod
    def data_unpack_and_merge(data_high, data_low):
        """
        Given 2 16-bit words (dataHigh, dataLow), merges the
        data into a 32-bit word

        :param data_high: most significant 2 bytes of data
        :param data_low: least significant 2 bytes of data
        :return: unpacked 32-bit data (as a native Python type)
        """
        # pack the two words to facilitate easy merging
        packer = struct.Struct('!H')
        data_high = packer.pack(data_high)
        data_low = packer.pack(data_low)

        # merge the data (as a packed string of bytes)
        data = data_high + data_low

        # unpacker for the 32-bit string of bytes
        unpacker = struct.Struct('!I')
        return unpacker.unpack(data)[0]

    def reset_seq_num(self):
        with Lock():
            self._seq_num = random.randint(0, 0xffff)

    def send_packet(self, request_object, timeout=None,
                    retries=None):
        """
        Make send_packet thread safe

        :param request_object:
        :param timeout:
        :param retries:
        """
        if timeout is None:
            timeout = self.timeout
        if retries is None:
            retries = self.retries

        with Lock():
            if self._seq_num >= 0xffff:
                self._seq_num = 0
            else:
                self._seq_num += 1
            return self._send_packet(
                request_object, self._seq_num, addr=self.skarab_eth_ctrl_addr,
                timeout=timeout, retries=retries, hostname=self.host
            )

    def _send_packet(self, request_object, sequence_number, addr,
                     timeout=sd.CONTROL_RESPONSE_TIMEOUT,
                     retries=sd.CONTROL_RESPONSE_RETRIES,
                     hostname='<unknown_host>'):
        """
        Send payload via UDP packet to SKARAB
        Sends request packets then waits for response packet if expected
        Retransmits request packet if response not received

        :param request_object: object containing the data to send to SKARAB
        :param addr: hostname and port of SKARAB
        :param timeout: how long to wait for a response before bailing
        :param retries: how many times to retransmit a request
        :return: response: returns response object or 'None' if no
            response received.
        """
        self._lock.acquire()
        # create the payload and send it
        request_payload = request_object.create_payload(sequence_number)
        retransmit_count = 0
        while retransmit_count < retries:
            self.logger.debug('{}: retransmit attempts: {}'.format(
                hostname, retransmit_count))
            try:
                self.logger.debug('{}: sending pkt({}, {}) to port {} = {}'.format(
                    hostname, request_object.packet['command_type'],
                    request_object.packet['seq_num'], addr, request_payload))
                self._skarab_control_sock.send(request_payload)
                if not request_object.expect_response:
                    self.logger.debug(
                        '{}: no response expected for seq {}, '
                        'returning'.format(hostname, sequence_number))
                    self._lock.release()
                    return None
                # get a required response
                rx_packet = None
                while rx_packet is None:
                    # here we want to receive a packet from the socket
                    # we pass the socket to the receive_packet function
                    rx_packet = self._receive_packet(
                        request_object, sequence_number, timeout, hostname)
                self._lock.release()
                return rx_packet
            except SkarabResponseNotReceivedError:
                # retransmit the packet
                pass
            except (KeyboardInterrupt, select.error):
                self.logger.warning('{}: keyboard interrupt, clearing '
                               'buffer.'.format(hostname))
                # wait to receive incoming responses
                time.sleep(0.5)
                try:
                    _ = self._skarab_control_sock.recvfrom(4096)
                    self.logger.info(
                        '{}: cleared recv buffer.'.format(hostname))
                except socket.error:
                    self.logger.info(
                        '{}: buffer already empty'.format(hostname))
                self._lock.release()
                raise KeyboardInterrupt
            retransmit_count += 1
        self._lock.release()
        errmsg = '{}: retransmit count exceeded. Giving up.'.format(hostname)
        self.logger.debug(errmsg)
        raise SkarabSendPacketError(errmsg)

    def _receive_packet(self, request_object, sequence_number,
                        timeout, hostname):
        """
        Receive a response to a packet.

        :param request_object:
        :param sequence_number:
        :param timeout:
        :param hostname:
        :return: The response object, or None
        """
        self.logger.debug('%s: reading response to sequence id %i.' % (
            hostname, sequence_number))

        try:
            # wait for response until timeout
            data_ready = select.select([self._skarab_control_sock], [], [], timeout)
            # if we have a response, process it
            if data_ready[0]:
                data = self._skarab_control_sock.recvfrom(4096)
                response_payload, address = data

                self.logger.debug('%s: response from %s = %s' % (
                    hostname, str(address), repr(response_payload)))

                # check if response is from the expected SKARAB
                recvd_from_addr = address[0]
                expected_recvd_from_addr = \
                    self._skarab_control_sock.getpeername()[0]
                if recvd_from_addr != expected_recvd_from_addr:
                    self.logger.warning(
                        '%s: received response from  %s, expected response from '
                        '%s. Discarding response.' % (
                            hostname, recvd_from_addr, expected_recvd_from_addr))
                    return None
                # check the opcode of the response i.e. first two bytes
                if response_payload[:2] == '\xff\xff':
                    self.logger.warning('%s: received unsupported opcode: 0xffff. '
                                        'Discarding response.' % hostname)
                    return None
                # check response packet size
                if (len(response_payload)/2) != request_object.num_response_words:
                    self.logger.warning("%s: incorrect response packet size. "
                                        "Discarding response" % hostname)

                    # self.logger.pdebug("Response packet not of correct size. "
                    self.logger.debug("Response packet not of correct size. "
                                      "Expected %i words, got %i words.\n "
                                      "Incorrect Response: %s" % (
                                        request_object.num_response_words,
                                        (len(response_payload)/2),
                                        repr(response_payload)))
                    # self.logger.pdebug("%s: command ID - expected (%i) got (%i)" %
                    self.logger.debug("%s: command ID - expected (%i) got (%i)" %
                                      (hostname, request_object.type + 1,
                                       (struct.unpack('!H', response_payload[:2]))[0]))
                    # self.logger.pdebug("%s: sequence num - expected (%i) got (%i)" %
                    self.logger.debug("%s: sequence num - expected (%i) got (%i)" %
                                      (hostname, sequence_number,
                                       (struct.unpack('!H', response_payload[2:4]))[0]))
                    return None

                # unpack the response before checking it
                response_object = request_object.response.from_raw_data(
                    response_payload, request_object.num_response_words,
                    request_object.pad_words)
                self.logger.debug('%s: response from %s, with seq num %i' % (
                    hostname, str(address),
                    response_object.seq_num))
                expected_response_id = request_object.type + 1
                if response_object.type != expected_response_id:
                    self.logger.warning('%s: incorrect command ID in response. Expected'
                                   '(%i) got(%i). Discarding response.' % (
                                       hostname, expected_response_id,
                                       response_object.type))
                    return None
                elif response_object.seq_num != sequence_number:
                    self.logger.debug('%s: incorrect sequence number in response. '
                                   'Expected(%i,%i), got(%i). Discarding '
                                   'response.' % (
                                       hostname, sequence_number,
                                       request_object.packet['seq_num'],
                                       response_object.seq_num))
                    return None
                return response_object
            else:
                errmsg = '%s: timeout; no packet received for seq %i. Will ' \
                         'retransmit as seq %i.' % (
                             hostname, sequence_number, sequence_number + 1)
                self.logger.debug(errmsg)
                raise SkarabResponseNotReceivedError(errmsg)

        except KeyboardInterrupt:
            self.logger.warning('{}: keyboard interrupt, clearing '
                            'buffer.'.format(hostname))
            # wait to receive incoming responses
            time.sleep(1)
            try:
                _ = self._skarab_control_sock.recvfrom(4096)
                self.logger.info('{}: cleared recv buffer.'.format(hostname))
            except socket.error:
                self.logger.info('{}: buffer already empty'.format(hostname))
            raise KeyboardInterrupt

        except select.error as e:
            self.logger.debug('{}: handling select error {}'.format(
                hostname, e))
            raise select.error

    # low level access functions
    def reboot_fpga(self):
        """
        Reboots the FPGA, booting from either the NOR FLASH or SDRAM
        """
        # trigger a reboot of the FPGA
        self.sdram_reconfigure(do_reboot=True)
        self.reset_seq_num()
        # reset the sdram programmed flag
        self._sdram_programmed = False
        # clear prog_info
        self.prog_info['last_programmed'] = ''
        self.prog_info['last_uploaded'] = ''

    def reset_fpga(self):
        """
        Reset the FPGA firmware. Resets the clks, registers, etc of the design

        :return: 'ok'
        """
        output = self.write_board_reg(sd.C_WR_BRD_CTL_STAT_0_ADDR,
                                      sd.ROACH3_FPGA_RESET, False)
        # # sleep to allow DHCP configuration
        # time.sleep(1)
        return output

    def shutdown_skarab(self):
        """
        Shuts the SKARAB board down

        :return: 'ok'
        """
        # should this function close the sockets and then attempt to reopen
        # once board is powered on? shut down requires two writes
        self.logger.info('Shutting board down.')
        self.write_board_reg(sd.C_WR_BRD_CTL_STAT_0_ADDR,
                             sd.ROACH3_SHUTDOWN, False)
        output = self.write_board_reg(sd.C_WR_BRD_CTL_STAT_1_ADDR,
                                      sd.ROACH3_SHUTDOWN, False)
        self.reset_seq_num()
        return output

    def write_board_reg(self, reg_address, data, expect_response=True,
                        timeout=None,
                        retries=None):
        """
        Write to a board register

        :param reg_address: address of register to write to
        :param data: data to write
        :param expect_response: does this write command require a response?
            (only false for reset and shutdown commands)
        :return: response object - object created from the response payload
            (attributes = payload components)
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        data_packed = self.data_split_and_pack(data)
        request = sd.WriteRegReq(sd.BOARD_REG, reg_address, *data_packed)
        # handle special writes that don't return a response
        request.expect_response = expect_response
        # send payload via UDP pkt and return response object (if no response
        # expected should return ok)
        write_reg_response = self.send_packet(request, timeout=timeout, retries=retries)
        return write_reg_response

    def read_board_reg(self, reg_address,
                       timeout=None,
                       retries=None):
        """
        Read from a specified board register

        :param reg_address: address of register to read
        :param retries:
        :return: data read from register
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        request = sd.ReadRegReq(sd.BOARD_REG, reg_address)
        read_reg_resp = self.send_packet(request, timeout=timeout, retries=retries)
        if read_reg_resp is None:
            raise ValueError('Got None reading board register '
                             '0x%010x' % reg_address)
        return self.data_unpack_and_merge(
            read_reg_resp.packet['reg_data_high'],
            read_reg_resp.packet['reg_data_low'])

    def write_dsp_reg(self, reg_address, data,
                      timeout=None,
                      retries=None):
        """
        Write to a dsp register

        :param reg_address: address of register to write to
        :param data: data to write
        :return: response object - object created from the response payload
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        data_packed = self.data_split_and_pack(data)
        request = sd.WriteRegReq(sd.DSP_REG, reg_address, *data_packed)
        # send payload via UDP pkt and return response object
        # (if no response expected should return ok)
        write_reg_response = self.send_packet(request, timeout=timeout, retries=retries)
        return write_reg_response

    def read_dsp_reg(self, reg_address,
                     timeout=None,
                     retries=None):
        """
        Read from a specified dsp register

        :param reg_address: address of register to read
        :return: data read from register
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        request = sd.ReadRegReq(sd.DSP_REG, reg_address)
        response = self.send_packet(request, timeout=timeout, retries=retries)
        if response is not None:
            return self.data_unpack_and_merge(
                response.packet['reg_data_high'],
                response.packet['reg_data_low'])
        return 0

    def get_embedded_software_version(self,
                                      timeout=None,
                                      retries=None):
        """
        Read the version of the microcontroller embedded software

        :return: String - Embedded Software Version - Major.Minor.RevisionNumber
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        request = sd.GetEmbeddedSoftwareVersionReq()
        response = self.send_packet(request, timeout=timeout, retries=retries)
        if response is not None:
            major = response.packet['version_major']
            minor = response.packet['version_minor']
            patch = response.packet['version_patch']
            return '{}.{}.{}'.format(major, minor, patch)

    def write_wishbone(self, wb_address, data,
                       timeout=None,
                       retries=None):
        """
        Used to perform low level wishbone write to a wishbone slave. Gives
        low level direct access to wishbone bus.

        :param wb_address: address of the wishbone slave to write to
        :param data: data to write
        :return: response object
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        # split data into two 16-bit words (also packs for network transmission)
        data_split = list(self.data_split_and_pack(data))
        # split address into two 16-bit segments: high, low
        # (also packs for network transmission)
        address_split = list(self.data_split_and_pack(wb_address))
        # create one tuple containing data and address
        address_and_data = address_split
        address_and_data.extend(data_split)
        request = sd.WriteWishboneReq(*address_and_data)
        response = self.send_packet(request, timeout=timeout, retries=retries)
        return response

    def read_wishbone(self, wb_address,
                      timeout=None,
                      retries=None):
        """
        Used to perform low level wishbone read from a Wishbone slave.

        :param wb_address: address of the wishbone slave to read from
        :return: Read Data or None
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        request = sd.ReadWishboneReq(*self.data_split_and_pack(wb_address))
        response = self.send_packet(request, timeout=timeout, retries=retries)
        if response is not None:
            return self.data_unpack_and_merge(
                response.packet['read_data_high'],
                response.packet['read_data_low'])

    def write_i2c(self, interface, slave_address, *bytes_to_write):
        """
        Perform i2c write on a selected i2c interface.
        Up to 33 bytes can be written in a single i2c transaction
        (33 bytes of data allowed since the fan controller i2c
         writes require a command byte plus 32 bytes of data)
        :param interface: identifier for i2c interface:
                          0 - SKARAB Motherboard i2c
                          1 - Mezzanine 0 i2c
                          2 - Mezzanine 1 i2c
                          3 - Mezzanine 2 i2c
                          4 - Mezzanine 3 i2c
        :param slave_address: i2c address of slave to write to
        :param bytes_to_write: 33 bytes of data to write (to be packed as
                               16-bit word each), list of bytes
        :return: response object
        """
        MAX_I2C_WRITE_BYTES = 33
        num_bytes = len(bytes_to_write)
        if num_bytes > MAX_I2C_WRITE_BYTES:
            self.logger.error(
                'Maximum of %s bytes can be written in a single transaction', str(MAX_I2C_WRITE_BYTES))
            return False

        # each byte to be written must be packaged as a 16 bit value
        packed_bytes = ''  # store all the packed bytes here

        packer = struct.Struct('!H')
        pack = packer.pack

        for byte in bytes_to_write:
            packed_bytes += pack(byte)

        # pad the number of bytes to write to 32 bytes
        if num_bytes < MAX_I2C_WRITE_BYTES:
            packed_bytes += (MAX_I2C_WRITE_BYTES - num_bytes) * '\x00\x00'

        # create payload packet structure
        request = sd.WriteI2CReq(interface, slave_address,
                                 num_bytes, packed_bytes)
        response = self.send_packet(request)
        # check if the write was successful
        if response is not None:
            if response.packet['write_success']:
                return True
            else:
                self.logger.error('I2C write failed!')
                return False
        else:
            self.logger.error('Bad response received')
            return False

    def read_i2c(self, interface, slave_address, num_bytes,
                 timeout=None,
                 retries=None):
        """
        Perform i2c read on a selected i2c interface.
        Up to 32 bytes can be read in a single i2c transaction.

        :param interface: identifier for i2c interface:
                          0 - SKARAB Motherboard i2c
                          1 - Mezzanine 0 i2c
                          2 - Mezzanine 1 i2c
                          3 - Mezzanine 2 i2c
                          4 - Mezzanine 3 i2c
        :param slave_address: i2c address of slave to read from
        :param num_bytes: number of bytes to read
        :return: an array of the read bytes if successful, else none
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        if num_bytes > 32:
            self.logger.error(
                'Maximum of 32 bytes can be read in a single transaction')
            return False

        # create payload packet structure
        request = sd.ReadI2CReq(interface, slave_address, num_bytes)
        response = self.send_packet(request, timeout=timeout, retries=retries)
        if response is not None:
            if response.packet['read_success']:
                return response.packet['read_bytes'][:num_bytes]
            else:
                self.logger.error('I2C read failed!')
                return 0
        else:
            self.logger.error('Bad response received.')
            return

    def pmbus_read_i2c(self, bus, slave_address, command_code, num_bytes,
                       timeout=None,
                       retries=None):
        """
        Perform a PMBus read of the I2C bus.

        :param bus: I2C bus to perform PMBus Read of
                          0 - SKARAB Motherboard i2c
                          1 - Mezzanine 0 i2c
                          2 - Mezzanine 0 i2c
                          3 - Mezzanine 0 i2c
                          4 - Mezzanine 0 i2c
        :param slave_address: address of the slave PMBus device to read
        :param command_code: PMBus command for the I2C read
        :param num_bytes: Number of bytes to read
        :return: array of read bytes if successful, else none
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        if num_bytes > 32:
            self.logger.error('Maximum of 32 bytes can be read in a '
                         'single transaction')
            return
        # dummy read data
        read_bytes = struct.pack('!32H', *(32 * [0]))
        # create payload packet structure
        request = sd.PMBusReadI2CBytesReq(bus, slave_address, command_code,
                                          read_bytes, num_bytes)
        # send payload and return response object
        response = self.send_packet(request, timeout=timeout, retries=retries)
        if response is not None:
            if response.packet['read_success']:
                return response.packet['read_bytes'][:num_bytes]
            else:
                self.logger.error('PMBus I2C read failed!')
                return 0
        else:
            self.logger.error('Bad response received.')
            return

    def sdram_program(self, first_packet, last_packet, write_words,
                      timeout=None,
                      retries=None):
        """
        Used to program a block of 4096 words to the boot SDRAM.
        These 4096 words are a chunk of the FPGA image to program to
        SDRAM and boot from.

        This data is sent over UDP packets to the fabric UDP port, not the
        control port- uC does not handle these packets.
        No response is generated.

        :param first_packet: flag to indicate this pkt is the first pkt
            of the image
        :param last_packet: flag to indicate this pkt is the last pkt of
            the image
        :param write_words: chunk of 4096 words from FPGA Image
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        request = sd.SdramProgramReq(first_packet, last_packet, write_words)
        self.send_packet(request, timeout=timeout, retries=retries)

    def sdram_reconfigure(self,
                          output_mode=sd.SDRAM_PROGRAM_MODE,
                          clear_sdram=False,
                          finished_writing=False,
                          about_to_boot=False,
                          do_reboot=False,
                          reset_sdram_read_addr=False,
                          clear_eth_stats=False,
                          enable_debug=False,
                          do_sdram_async_read=False,
                          do_continuity_test=False,
                          continuity_test_out_low=0x00,
                          continuity_test_out_high=0x00,
                          timeout=None,
                          retries=None):

        """
        Used to perform various tasks realting to programming of the boot
        SDRAM and config of Virtex7 FPGA from boot SDRAM

        :param output_mode: specifies the mode of the flash SDRAM interface
        :param clear_sdram: clear any existing FPGA image from the SDRAM
        :param finished_writing: indicate writing FPGA image to SDRAM
            is complete
        :param about_to_boot: enable booting from the newly programmed image
            in SDRAM
        :param do_reboot: trigger reboot of the Virtex7 FPGA and boot from
            image in SDRAM
        :param reset_sdram_read_addr: reset the SDRAM read address so that
            reading SDRAM can start at 0x0
        :param clear_eth_stats: clear ethernet packet statistics with regards
            to FPGA image containing packets
        :param enable_debug: enable debug mode for reading data currently
            stored in SDRAM
        :param do_sdram_async_read: used in debug mode to read the 32-bits
            of the SDRAM and advance read pointer by one
        :param do_continuity_test: test continuity of the flash bus between
            the Virtex7 FPGA and the Spartan 3AN FPGA
        :param continuity_test_out_low: Used in continuity debug mode,
            specify value to set lower 16 bits of the bus
        :param continuity_test_out_high: Used in continuity debug mode,
            specify value to set upper 16 bits of the bus
        :return: data read, if there was any
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        # create request object
        request = sd.SdramReconfigureReq(
            output_mode, clear_sdram, finished_writing,
            about_to_boot, do_reboot, reset_sdram_read_addr, clear_eth_stats,
            enable_debug, do_sdram_async_read, do_continuity_test,
            continuity_test_out_low, continuity_test_out_high)
        response = self.send_packet(request, timeout=timeout, retries=retries)
        resp_pkt = response.packet
        if response is None:
            args = locals()
            args.pop('req')
            args.pop('response')
            args.pop('self')
            raise SkarabSdramError('sdram_reconfigure failed, '
                                   'no response. %s' % args)
        if do_sdram_async_read:
            # process data read here
            low = struct.pack('!H', resp_pkt['sdram_async_read_data_low'])
            high = struct.pack('!H', resp_pkt['sdram_async_read_data_high'])
            sdram_data = low + high
            return sdram_data
        if response is not None:
            return True
        else:
            self.logger.error('Problem configuring SDRAM')
            return False

    # region --- Virtex Flash Reconfiguration-related methods ---

    # region === Read/Verify Command ===

    def read_flash_words(self, flash_address, num_words=256,
                         timeout=None,
                         retries=None):
        """
        Used to read a block of up to 384 16-bit words from the NOR flash
        on the SKARAB motherboard.

        :param flash_address: 32-bit Address in the NOR flash to read
        :param num_words: Number of 16-bit words to be read - Default
        value of 256 words
        :return: String - Words read by the function call
                        - Hex-encoded string
        """
        """
        ReadFlashWordsReq consists of the following:
        - Command Type: skarab_definitions.READ_FLASH_WORDS = 0x0000F
        - Sequence Number: self.sequenceNumber
        - Upper 16 bits of NOR flash address
        - Lower 16 bits of NOR flash address
        - NumWords: Number of 16-bit words to be read
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        if num_words > 384:
            errmsg = 'Failed to ReadFlashWords - Maximum of 384 16-bit words ' \
                     'can be read from the NOR flash'
            self.logger.error(errmsg)
            raise SkarabReadFailed(errmsg)
        address_high, address_low = self.data_split_and_pack(flash_address)
        request = sd.ReadFlashWordsReq(address_high, address_low, num_words)
        # Make actual function call and (hopefully) return data
        # - Number of Words to be expected in the
        # Response: 1+1+(1+1)+1+384+(3-1) = 391
        response = self.send_packet(request, timeout=timeout, retries=retries)
        if response is not None:
            # Then send back ReadWords[:NumWords]
            return response.packet['read_words'][:num_words]
        else:
            errmsg = 'Bad response received from SKARAB'
            self.logger.error(errmsg)
            raise SkarabInvalidResponse(errmsg)

    def verify_words(self, bitstream, flash_address=sd.DEFAULT_START_ADDRESS):
        """
        This method reads back the programmed words from the flash device
        and checks it
        against the data in the input .bin file uploaded to the Flash Memory.

        :param bitstream: Of the input .bin file that was programmed to Flash
            Memory
        :param flash_address: 32-bit Address in the NOR flash from which to
            START reading
        :return: Boolean success/fail
        """
        bitstream_chunks = [
            bitstream[i:i + 512] for i in range(0, len(bitstream), 512)

        ]
        # Now we have 512-byte chunks
        # Using 512-byte chunks = 256-word chunks because we are reading
        # 256 words at a time from the Flash Memory

        # But again, make sure SDRAM is in FLASH Mode
        # - as per Line 1827, in prepare_sdram_for_programming
        if not self.sdram_reconfigure(output_mode=sd.FLASH_MODE):
            errmsg = 'Unable to put SDRAM into FLASH Mode'
            self.logger.error(errmsg)
            raise sd.SkarabProgrammingError(errmsg)
        # else: Continue
        # Compare against the bitstream extracted (and converted)
        # from the .bin file
        # - This will only iterate as long as there are words in the bitstream
        # - Which could (and should) be without padding to the 512-word boundary
        chunk_counter = 0
        for chunk in bitstream_chunks:
            self.logger.debug('Comparing image_chunk: %d', chunk_counter)

            # Check for padding BEFORE we convert to integer words
            if len(chunk) % 512 != 0:
                self.logger.debug('Padding chunk')
                chunk += '\xff' * (512 - (len(chunk) % 512))
            # else: Continue

            # Convert the 512 (string) bytes to 256 (integer) words
            chunk_int = [
                struct.unpack('!H', chunk[i:i + 2])[0] for i in range(0, 512, 2)
            ]

            words_read = self.read_flash_words(flash_address, 256)
            for index in range(256):
                if words_read[index] != chunk_int[index]:
                    errmsg = 'Flash_Word mismatch at index %d in ' \
                             'bitstream_chunk %d' % (index, chunk_counter)
                    self.logger.error(errmsg)
                    raise SkarabReadFailed(errmsg)
            flash_address += 256
            chunk_counter += 1
        return True     # Words have been verified successfully

    # endregion

    # region === Program Command ===

    def program_flash_words(self, flash_address, total_num_words, num_words,
                            do_buffered_prog, start_prog, finish_prog,
                            write_words,
                            timeout=None,
                            retries=None):
        """
        This is the low-level function, as per the FUM, to write to
        the Virtex Flash.

        :param flash_address: 32-bit flash address to program to
        :param total_num_words: Total number of 16-bit words to program over
            one or more Ethernet packets
        :param num_words: Number of words in this (specific) Ethernet packet
            to program
        :param do_buffered_prog: 0/1 = Perform Buffered Programming
        :param start_prog: 0/1 - First packet in flash programming,
            start programming operation in flash
        :param finish_prog: 0/1 - Last packet in flash programming,
            complete programming operation in flash
        :param write_words: Words to program, max = 256 Words
        :return: Boolean - Success/Fail - 1/0
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        # First thing to check:
        if num_words > 256 or len(write_words) > 512:
            errmsg = 'Maximum of 256 words can be programmed to the Flash ' \
                     'at once'
            self.logger.error(errmsg)
            raise sd.SkarabProgrammingError(errmsg)
        # else: Continue as per normal

        """
        ProgramFlashWordsReq consists of the following:
        - Sequence Number: self.seq_num
        - Upper 16 bits of flash_address to start programming to
        - Lower 16 bits of flash_address to start programming to
        - TotalNumWords: Total number of 16-bit words to program over one or
            more Ethernet packets
        - NumWords: Number of words in this Ethernet packet to program
        - doBufferedProgramming: 0/1 - Perform Buffered Programming
        - StartProgram: 0/1 - First packet in flash programming, start
            programming operation in flash
        - FinishProgram: 0/1 - Last packet in flash programming, complete
            programming operation in flash
        - WriteWords[256] (WordsToWrite): Words to program, max = 256 words
        """

        # split 32-bit Flash Address into 16-bit high and low values
        flash_addr_high, flash_addr_low = self.data_split_and_pack(
            flash_address)

        # create instance of ProgramFlashWordsRequest
        request = sd.ProgramFlashWordsReq(
            flash_addr_high, flash_addr_low, total_num_words, num_words,
            do_buffered_prog, start_prog, finish_prog, write_words)

        """
        ProgramFlashWordsResp consists of the following:
        - Command Type
        - Sequence Number
        - Upper 16 bits of Flash Address
        - Lower 16 bits of Flash Address
        - Total Number of Words being Programmed
        - Number of Words being written/that were written in the
            request (at the moment)
        - DoBufferedProgramming
        - First Packet in Flash Programming
        - Last Packet in Flash Programming
        - ProgramSuccess: 0/1
        - Padding: [2]

        - Therefore Total Number of Words to be expected in the Response:
          - 1+1+1+1+1+1+1+1+1+1+(2-1) = 11 16-bit words (?)
        """
        response = self.send_packet(request, timeout=timeout, retries=retries)
        if response is not None:
            # We have some data back
            if response.packet['program_success']:
                # Job done
                return True
            else:
                # ProgramFlashWordsRequest was made, but unsuccessful
                errmsg = 'Failed to Program Flash Words'
                self.logger.error(errmsg)
                raise sd.SkarabProgrammingError(errmsg)
        else:
            errmsg = 'Bad response received from SKARAB'
            self.logger.error(errmsg)
            raise SkarabInvalidResponse(errmsg)

    def program_words(self, bitstream, flash_address=sd.DEFAULT_START_ADDRESS):
        """
        Higher level function call to Program n-many words from an
        input .hex (eventually .bin) file.

        This method scrolls through the words in the bitstream, and packs
        them into 256+256 words.

        :param bitstream: Of the input .bin file to write to Flash Memory
        :param flash_address: Address in Flash Memory from where to
        start programming
        :return: Boolean Success/Fail - 1/0
        """

        # bitstream = open(filename, 'rb').read()
        # As per upload_to_ram() except now we're programming in chunks
        # of 512 words
        size = len(bitstream)
        # Split image into chunks of 512 words = 1024 bytes
        image_chunks = [bitstream[i:i + 1024] for i in range(0, size, 1024)]

        # padding_word = 0xffff
        padding_byte = '\xff'

        # Needs to be calculated on each 512 word chunk
        for chunk in image_chunks:
            if len(chunk) % 1024 != 0:
                # Needs to be padded to a 512 word boundary (and NOT 4096!)
                chunk += (1024 - (len(chunk) % 1024)) * padding_byte
            # else: Continue

            # Need to program 256 words at a time, more specifically
            # - Program first half: If passed, continue; else: return
            # - Program second half: If passed, continue; else: return
            if not self.program_flash_words(flash_address, 512, 256, True, True,
                                            False, chunk[:512]):
                # Did not successfully program the first 256 words
                errmsg = 'Failed to program first 256 words of 512 word ' \
                         'image block'
                self.logger.error(errmsg)
                raise sd.SkarabProgrammingError(errmsg)
            elif not self.program_flash_words(flash_address + 256, 512, 256,
                                              True, False, True, chunk[512:]):
                # Did not successfully program the first 256 words
                errmsg = 'Failed to program second 256 words of 512 word ' \
                         'image block'
                self.logger.error(errmsg)
                raise sd.SkarabProgrammingError(errmsg)

            # Shift the address we are writing to by 512 places
            flash_address += 512
            # Loop back to the next image chunk, repeat the process

        # Now done programming the input bitstream, need to return and
        # move on to VerifyWords()
        return True

    # endregion

    # region === Erase Command ===

    def erase_flash_block(self, flash_address=sd.DEFAULT_START_ADDRESS,
                          timeout=None,
                          retries=None):
        """
        Used to erase a block in the NOR flash on the SKARAB motherboard

        :param flash_address: 32-bit address in the NOR flash to erase
        :return: erase_success - 0/1
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        address_high, address_low = self.data_split_and_pack(flash_address)
        request = sd.EraseFlashBlockReq(address_high, address_low)
        # Make the actual function call and (hopefully) return data
        # - Number of Words to be expected in the
        # Response: 1+1+(1+1)+1+(7-1) = 11
        response = self.send_packet(request, timeout=timeout, retries=retries)
        if response is not None:
            if response.packet['erase_success']:
                return True
            else:
                # Erase request was made, but unsuccessful
                errmsg = 'Failed to Erase Flash Block'
                self.logger.error(errmsg)
                raise sd.SkarabProgrammingError(errmsg)
        else:
            errmsg = 'Bad response received from SKARAB'
            self.logger.error(errmsg)
            raise SkarabInvalidResponse(errmsg)

    def erase_blocks(self, num_flash_blocks,
                     flash_address=sd.DEFAULT_START_ADDRESS):
        """
        Higher level function call to Erase n-many Flash Blocks in preparation
        for program_flash_words
        This method erases the required number of blocks in the flash

        * Only the required number of flash blocks are erased

        :param num_flash_blocks: Number of Flash Memory Blocks to be erased,
            to make space for the new image
        :param flash_address: Start address from where to begin erasing
            Flash Memory
        """
        erase_address = flash_address
        # First, need to SdramReconfigure into 'Flash Mode'
        # - as per Line 1827, in prepare_for_sdram_for_programming
        if not self.sdram_reconfigure(output_mode=sd.FLASH_MODE):
            errmsg = 'Unable to put SDRAM into FLASH Mode'
            self.logger.error(errmsg)
            raise sd.SkarabProgrammingError(errmsg)
        # Now, to do the actual erasing of Flash Memory Blocks
        self.logger.info('Erasing Flash Blocks from flash_address = {}'.format(
            erase_address))
        block_counter = 0
        while block_counter < num_flash_blocks:
            # Erasing Flash Blocks this way because a request may timeout and
            # result in an out-of-sequence response
            if not self.erase_flash_block(erase_address):
                # Problem Erasing the Flash Block
                self.logger.error('Failed to Erase Flash Memory Block at: 0x{:02X}. '
                             'Retrying now...'.format(erase_address))
                # Reset the block_counter and erase_address to
                # their initial values
                block_counter = 0
                erase_address = flash_address
            else:
                # All good
                block_counter += 1
                erase_address += int(sd.DEFAULT_BLOCK_SIZE)
        return True

    # endregion

    # region === VirtexFlashReconfig ===
    def process_flash_bin(self, filename):
        """
        Sends the file to skarab_fileops for processing and returns
        number of words, number of memory blocks and image to program
        :param filename: The actual .bin file that is to be written to
        the Virtex FPGA
        :returns image_to_program, num_words, num_memory_blocks
        """
        # For completeness, make sure the input file is of a .bin disposition
        file_extension = os.path.splitext(filename)[1]
        image_to_program = ''

        # Need to change file-handler to use skarab_fileops.choose_processor(filename)
        #binname = '/tmp/fpgstream_' + str(self.parent) + '.bin'
        processor = skfops.choose_processor(filename)
        processor = processor(filename, extract_to_disk=False)
        #image_to_program, binname = processor.make_bin()
        image_to_program, _none = processor.make_bin()

        self.logger.debug('VIRTEX FLASH RECONFIG: Analysing Words')
        # Can still analyse the filename, as the file size should still
        # be the same, regardless of swapping the endianness
        num_words, num_memory_blocks = skfops.analyse_file_virtex_flash(bitstream=image_to_program)

        if (num_words == 0) or (num_memory_blocks == 0):
            # Problem
            errmsg = 'Failed to Analyse File successfully'
            self.logger.error(errmsg)
            # Remove temp bin-file wherever possible
            #os.remove(binname)
            raise sd.SkarabInvalidBitstream(errmsg)
        # else: Continue

        #os.remove(binname)
        return image_to_program, num_words, num_memory_blocks

    def virtex_flash_reconfig(self, filename=None,
                              image_to_program=None,
                              num_words=None,
                              num_memory_blocks=None,
                              flash_address=sd.DEFAULT_START_ADDRESS,
                              blind_reconfig=False):
        """
        This is the entire function that makes the necessary calls to
        reconfigure the Virtex7's Flash Memory. Either specify a filename
        to program or process the file separately using
        transport_skarab.process_flash_bin and send image_to_program, num_words
        and num_memory_blocks. Note when using this function as a thread it
        is preferable to send image_to_program as processing a file will use
        memory for each instance.
        :param filename: The actual .bin file that is to be written to
        the Virtex FPGA
        :param image_to_program: Image processed by skarab_fileops.py
        to program.
        :param num_words: Number of words as processed by skarab_fileops.py
        :param num_memory_blocks: Number of blocks to program as processed
        by skarab_fileops.py
        :param flash_address: 32-bit Address in the NOR flash to
        start programming from
            - flash_address = DEFUALT_START_ADDRESS is for programming the Multiboot Image
            - flash_address = 0x0 is for programming the Golden Image
        :param blind_reconfig: Reconfigure the board and don't wait to
        verify what has been written
        :return: Success/Fail - 0/1
        """
        if filename:
            image_to_program, num_words, num_memory_blocks = self.process_flash_bin(filename)
        elif (image_to_program is None or num_words is None or
             num_memory_blocks is None):
            errmsg = ('Specify either a filename or image_to_program when '
                      'calling virtex_flash_reconfig')
            raise sd.SkarabProgrammingError(errmsg)

        if (num_words == 0) or (num_memory_blocks == 0):
            # Problem
            errmsg = 'num_words or num_memory_blocks incorrect'
            self.logger.error(errmsg)
            raise sd.SkarabInvalidBitstream(errmsg)

        self.logger.debug('VIRTEX FLASH RECONFIG: Erasing Flash Memory Blocks')
        if not self.erase_blocks(num_memory_blocks, flash_address):
            # Problem
            errmsg = 'Failed to Erase Flash Memory Blocks'
            self.logger.error(errmsg)
            raise sd.SkarabProgrammingError(errmsg)
        # else: Continue

        self.logger.debug('VIRTEX FLASH RECONFIG: Programming Words to Flash Memory')
        if not self.program_words(image_to_program, flash_address):
            # Problem
            errmsg = 'Failed to Program Flash Memory Blocks'
            self.logger.error(errmsg)
            raise sd.SkarabProgrammingError(errmsg)
        # else: Continue

        if not blind_reconfig:
            self.logger.debug('VIRTEX FLASH RECONFIG: Verifying words that '
                         'were written to Flash Memory')
            if not self.verify_words(image_to_program, flash_address):
                # Problem
                errmsg = 'Failed to Program Flash Memory Blocks'
                self.logger.error(errmsg)
                raise sd.SkarabProgrammingError(errmsg)
            # else: Continue
        return True

    # endregion

    # endregion

    # region --- SPARTAN Flash Reconfiguration-related methods ---

    # region === Read/Verify Command ===
    def read_spi_page(self, spi_address, num_bytes,
                      timeout=None,
                      retries=None):
        """
        Used to read a page from the SPI flash in the Spartan 3AN FPGA on the
        SKARAB Motherboard. Up to a full page (264 bytes) can be read.

        :param spi_address: address of the page wanting to be read
        :param num_bytes: number of bytes in page to be read (max 264 bytes)
        :return: list of read data
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        if num_bytes > 264:
            self.logger.error('Maximum of 264 bytes (One full page) '
                         'can be read from a single SPI Register')
            return False
        # split 32-bit page address into 16-bit high and low
        spi_address_high, spi_address_low = \
            self.data_split_and_pack(spi_address)
        # create payload packet structure for read request
        request = sd.ReadSpiPageReq(spi_address_high,
                                    spi_address_low, num_bytes)
        response = self.send_packet(request, timeout=timeout, retries=retries)
        if response is not None:
            if response.packet['read_spi_page_success']:
                # Then, send back ReadBytes[:NumBytes]
                return response.packet['read_bytes'][:num_bytes]
            else:
                # Read was made, but unsuccessful
                self.logger.error('SPI Read FAILED')
                raise SkarabReadFailed('Attempt to perform SPI Read Failed')
        else:
            self.logger.error('Bad Response Received')
            raise SkarabInvalidResponse('Bad response received from SKARAB')

    def verify_bytes(self, bitstream):
        """
        This is the high-level function that implements read_spi_page to
        verify the data from the .ufp file that was written to the Spartan FPGA flash memory.

        :param bitstream: of the input .ufp file that was used to reconfigure
            the Spartan 3AN FPGA
        :return: Boolean - True/False - Success/Fail - 1/0
        """

        # We need to read as many sectors as there are 264-byte pages
        # in the bitstream
        # - Easier to manipulate the bitstream here on the fly

        pages = [bitstream[i:i + 528] for i in range(0, len(bitstream), 528)]

        page_counter = 0
        # It's a list this time because read_spi_page returns an integer list
        raw_data = []
        for page in pages:

            if len(page) % 528 != 0:
                # Needs to be padded to a 264-byte boundary
                page += (528 - (len(page) % 528)) * 'f'
            # else: Continue

            for byte_counter in range(0, len(page), 2):
                one_byte = int(page[byte_counter: byte_counter+2], 16)
                raw_data.append(self.reverse_byte(one_byte))

            # Define the sector address from where we will be reading data
            page_address = (page_counter << 9)
            debugmsg = 'Now reading from SPI Address 0x{:02X}'.format(
                page_address)
            self.logger.debug(debugmsg)

            # Reading one full page at a time
            # - Returns an list of integers
            read_bytes = self.read_spi_page(page_address, 264)

            for byte_counter in range(len(read_bytes)):
                # Compare byte by byte
                # debugmsg = 'Comparing Raw_Data: 0x{:02X} - Read_Data: ' \
                #            '0x{:02X}'.format(raw_data[byte_counter],
                #                              read_bytes[byte_counter])
                # self.logger.debug(debugmsg)

                if raw_data[byte_counter] != read_bytes[byte_counter]:
                    # Problem
                    debugmsg = 'Comparing Raw_Data: 0x{:02X} - Read_Data: ' \
                               '0x{:02X}'.format(raw_data[byte_counter],
                                                 read_bytes[byte_counter])
                    self.logger.debug(debugmsg)

                    errmsg = 'Byte mismatch at index: {}. Failed to ' \
                             'reconfigure Spartan Flash successfully.'.format(
                                byte_counter)
                    self.logger.error(errmsg)
                    raise sd.SkarabProgrammingError(errmsg)
                # else: Continue

            # Increment the page-count
            page_counter += 1
            # clear the raw_data buffer for the next page conversion
            raw_data = []

        return True

    # endregion

    # region === Program Command ===

    def program_spi_page(self, spi_address, num_bytes, write_bytes,
                         timeout=None,
                         retries=None):
        """
        Low-level function call to program a page to the SPI Flash in the
        Spartan 3AN FPGA on the SKARAB.
        Up to a full page (264 bytes) can be programmed.

        :param spi_address: 32-bit address to program bytes to
        :param num_bytes: Number of bytes to program to Spartan flash
        :param write_bytes: Data to program - max 264 bytes -->
            HEX-ENCODED STRING DATA
        :return: Boolean - Success/Fail - 1/0
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries


        # First thing to check:
        if num_bytes > 264 or (len(write_bytes)/2) > 264:
            errmsg = 'Maximum of 264 bytes can be programmed to an SPI Sector' \
                     ' at once.\nNum_bytes = {}, and len(write_bytes) = {}' \
                     ''.format(num_bytes, len(write_bytes))
            self.logger.error(errmsg)
            raise sd.SkarabProgrammingError(errmsg)
        self.logger.debug('Data is ok continue programming to SPI Address 0x{:02X}'
                     ''.format(spi_address))

        """
        ProgramSpiPageReq consists of the following:
        - Sequence Number: self.seq_num
        - Upper 16 bits of spi_address to start programming to
        - Lower 16 bits of spi_address to start programming to
        - NumBytes: Number of bytes in page to program
        - WriteBytes[264] (BytesToWrite): Bytes to program,
            max = 264 bytes (1 page)
        """
        # Split 32-bit Flash Address into 16-bit high and low values
        spi_addr_high, spi_addr_low = self.data_split_and_pack(spi_address)

        # Create instance of ProgramFlashWordsRequest
        request = sd.ProgramSpiPageReq(spi_addr_high, spi_addr_low,
                                       num_bytes=num_bytes,
                                       write_bytes=write_bytes)

        if '\x00\xbe\x00\xaf' in write_bytes[:10]:
            # Flash Magic Byte case - DON'T HANDLE RESPONSE
            debugmsg = 'Making Magic Bytes request...'
            self.logger.debug(debugmsg)

            # It seems the response was still being handled after
            # the first EraseSpiSectorRequest
            # request.expect_response = False
        # else: Continue

        """
        ProgramSpiPageResp consists of the following:
        - Command Type
        - Sequence Number
        - Upper 16 bits of Flash Address
        - Lower 16 bits of Flash Address
        - Number of Bytes being written/that were written in the
            request (at the moment)
        - VerifyBytes[264]: Verification bytes read from the same page after
            programming completes
        - ProgramSpiPageSuccess: 0/1
        - Padding: [2]
        - Therefore Total Number of Words to be expected in the Response:
        - 1+1+(1+1)+1+264+1+(2-1) = 271 16-bit words
        """
        response = self.send_packet(request, timeout=timeout, retries=retries)
        if not request.expect_response:
            # Just return True (?)
            debugmsg = 'No response to handle...'
            self.logger.debug(debugmsg)
            return True

        if response is not None:
            # We have some data back
            if response.packet['program_spi_page_success']:
                # Job done
                # TODO: Implement 'Verify_on_the_fly' to verify written data
                debugmsg = 'ProgramSpiPage returned successfully.\n' \
                           'len(VerifyBytes) = {}'.format(
                            len(response.packet['verify_bytes']))
                self.logger.debug(debugmsg)
                return True
            else:
                # ProgramSpiPageRequest was made, but unsuccessful
                errmsg = 'Failed to Program Page'
                self.logger.error(errmsg)
                raise sd.SkarabProgrammingError(errmsg)
        else:
            errmsg = 'Bad response received from SKARAB'
            self.logger.error(errmsg)
            raise SkarabInvalidResponse(errmsg)

    def program_pages(self, bitstream, num_pages):
        """
        Higher level function call to Program n-many words from an
        input .ufp file.
        This method breaks the bitstream up into chunks of up to 264 bytes.
        - Removed 'num_sectors' parameter; doesn't seem to be needed

        :param bitstream: Of the input .ufp file to write to SPI Sectors,
            without \r and \n
        :param num_pages: Total Number of Pages to be written to the SPI Sectors
        :return: Boolean - Success/Fail - 1/0
        """

        # Need to break up the bitstream in (up to) 264-byte chunks
        pages = [bitstream[i:i+528] for i in range(0, len(bitstream), 528)]

        # Sanity check
        if len(pages) != num_pages:
            # Problem breaking up the bitstream into chunks
            # - No real idea of how to handle this (?)
            errmsg = 'Error in breaking down bitstream to program...\n' \
                     'Pages_calculated = {}, Number of 264-byte pages = {}'\
                        .format(num_pages, len(pages))
            self.logger.error(errmsg)
            raise sd.SkarabProgrammingError(errmsg)
        # else: Continue

        page_counter = 0
        raw_page_data = ''
        for page in pages:
            if len(page) % 528 != 0:
                # Needs to be padded to a 264-byte boundary
                page += (528 - (len(page) % 528)) * 'f'
            # else: Continue

            # Before program_spi_page, need to swap the bits in each byte
            # so that the UFP file format matches the raw data format
            for char_counter in range(0, len(page), 2):
                one_byte = int(page[char_counter: char_counter+2], 16)
                reversed_byte = self.reverse_byte(one_byte)
                raw_page_data += struct.pack('!H', reversed_byte)

            # Need to program_spi_page with a maximum of 264 bytes
            # - First, define the address where the page will be written to
            page_address = (page_counter << 9)

            if not self.program_spi_page(spi_address=page_address,
                                         num_bytes=len(raw_page_data)/2,
                                         write_bytes=raw_page_data):
                # Problem
                errmsg = 'Failed to program page-{} to address: 0x{:02X}'\
                            .format(page_counter, page_address)
                self.logger.error(errmsg)
            # else: Continue

            # Increment page_counter
            page_counter += 1
            # Clear raw_page_data buffer
            raw_page_data = ''

        return True

    # endregion

    # region === Erase Command ===
    def erase_spi_sector(self, spi_address,
                         timeout=None,
                         retries=None):
        """
        Used to erase a sector in the SPI Flash in the Spartan 3AN FPGA
        on the SKARAB.

        :param spi_address: 32-bit address to erase in the Flash
        :return: Boolean - Success/Fail - 1/0
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        address_high, address_low = self.data_split_and_pack(spi_address)
        # Create instance of EraseSpiSectorRequest
        request = sd.EraseSpiSectorReq(address_high, address_low)
        # Make the actual function call and (hopefully) return data
        # Number of Words to be expected in the response: 1+1+(1+1)+1+(7-1) = 11
        response = self.send_packet(request, timeout=timeout, retries=retries)
        if response is not None:
            if response.packet['erase_success']:
                return True
            else:
                # Erase request was made, but unsuccessful
                errmsg = 'Failed to Erase Flash Block'
                self.logger.error(errmsg)
                raise sd.SkarabProgrammingError(errmsg)
        else:
            errmsg = 'Bad response received from SKARAB'
            self.logger.error(errmsg)
            raise SkarabInvalidResponse(errmsg)

    def erase_sectors(self, num_sectors):
        """
        Erase required number of sectors for input .ufp file

        :param num_sectors: Required number of sectors to be erased
        :return: Boolean - Success/Fail - 1/0
        """
        for sector_counter in range(0, num_sectors+1):
            # Get associated 32-bit pre-defined Sector Address
            sector_address = sd.SECTOR_ADDRESS[sector_counter]
            debugmsg = 'Erasing SectorAddress: 0x{:02X}'.format(sector_address)
            self.logger.debug(debugmsg)

            if not self.erase_spi_sector(sector_address):
                # Problem erasing SPI Sector
                errmsg = 'Problem Erasing SPI Sector Address: 0x{:02X}'\
                    .format(sd.SECTOR_ADDRESS[sector_counter])
                self.logger.error(errmsg)
                # No custom 'EraseError' to raise; raise error in main
                # function call
                return False
            self.logger.debug('Successfully erased SPI Sector Address: 0x{:02X} - '
                         '({} of {})'.format(sd.SECTOR_ADDRESS[sector_counter],
                                             sector_counter, num_sectors))
        return True

    # endregion

    # region === Enabling/Disabling Access to SPI Flash ===

    def enable_isp_flash(self):
        """
        This method Enables access to ISP Flash by writing two Magic Bytes
        to a certain address space
        """
        # This will return the SpartanFirmwareVersion as an
        # integer_tuple (major, minor)
        (major_version, minor_version) = \
            self.get_spartan_firmware_version_tuple()

        # This check is done to see if the SPARTAN requires 'unlocking' to
        # access the in-system flash
        if ((major_version == sd.CURRENT_SPARTAN_MAJOR_VER) and
            (minor_version >= sd.CURRENT_SPARTAN_MINOR_VER)) \
                or (major_version > sd.CURRENT_SPARTAN_MAJOR_VER):
            # Write Flash Magic Byte to enable access to ISP Flash

            flash_magic_bytes = [sd.SPARTAN_FLASH_MAGIC_BYTE_0,
                                 sd.SPARTAN_FLASH_MAGIC_BYTE_1]

            # Need to pack these magic_bytes in the format expected
            raw_magic_bytes = ''
            for value in flash_magic_bytes:
                raw_magic_bytes += struct.pack('!H', value)

            # padding just to test whether the packet needs to be 264-bytes long
            # - 266 because testing lead to this result
            raw_magic_bytes += (266 - (len(raw_magic_bytes) % 264)) * '\x00\xff'

            debugmsg = 'Writing Magic Bytes %s to Flash...' % raw_magic_bytes
            self.logger.debug(debugmsg)

            # Ignore result
            self.program_spi_page(sd.SPARTAN_SPI_REG_ADDR, 2, raw_magic_bytes)

    def disable_isp_flash(self):
        """
        This method Disables access to ISP Flash by reading from a
        certain address space
        (And subsequently clearing the Magic Flash Byte)
        """
        # This will return the SpartanFirmwareVersion as an
        # integer_tuple (major, minor)
        (major_version, minor_version) = \
            self.get_spartan_firmware_version_tuple()
        if ((major_version == sd.CURRENT_SPARTAN_MAJOR_VER) and
            (minor_version >= sd.CURRENT_SPARTAN_MINOR_VER)) \
                or (major_version > sd.CURRENT_SPARTAN_MAJOR_VER):
            # Seems as though we just need to read a certain address
            # to clear it
            debugmsg = 'Reading address to clear Magic Bytes...'
            self.logger.debug(debugmsg)
            rd_addr = sd.SPARTAN_SPI_REG_ADDR + \
                sd.SPARTAN_SPI_CLEAR_FLASH_MAGIC_BYTE_OFFSET
            result = self.read_spi_page(rd_addr, 1)
            if len(result) < 1:
                # Failed to read
                errmsg = 'Failed to disable access to ISP Flash'
                self.logger.error(errmsg)
                raise sd.SkarabProgrammingError(errmsg)

    # endregion

    @staticmethod
    def reverse_byte(input_byte):
        """
        Method created to replicate 'SwappedByte' method in
        SpartanFlashReconfigApp.cpp;

        'This is done so that the .ufp bitstream matches raw data format' (?)
        Mirrors 8-bit integer (byte) about its center-point
        e.g. 0b01010110 -> 0b01101010

        :param input_byte: to be byte-swapped/mirrored
        :return: Reversed-byte
        """
        # TODO: clean this function up, better way to do this
        mirrored_byte = 0x0
        if (input_byte & 0x01) == 0x01:
            mirrored_byte = mirrored_byte | 0x80
        if (input_byte & 0x02) == 0x02:
            mirrored_byte = mirrored_byte | 0x40
        if (input_byte & 0x04) == 0x04:
            mirrored_byte = mirrored_byte | 0x20
        if (input_byte & 0x08) == 0x08:
            mirrored_byte = mirrored_byte | 0x10
        if (input_byte & 0x10) == 0x10:
            mirrored_byte = mirrored_byte | 0x08
        if (input_byte & 0x20) == 0x20:
            mirrored_byte = mirrored_byte | 0x04
        if (input_byte & 0x40) == 0x40:
            mirrored_byte = mirrored_byte | 0x02
        if (input_byte & 0x80) == 0x80:
            mirrored_byte = mirrored_byte | 0x01
        return mirrored_byte

    def verify_bytes_now(self, written_bytes, returned_bytes):
        """
        Used to 'Verify on the fly' the data programmed to SPARTAN Flash
        via program_spi_page.

        :param written_bytes:
        :param returned_bytes:
        """
        if len(written_bytes) != len(returned_bytes):
            # Problem
            errmsg = 'Num Written Bytes ({}) != Num Returned Bytes ({})'\
                .format(len(written_bytes), len(returned_bytes))
            self.logger.error(errmsg)
            raise sd.SkarabProgrammingError(errmsg)
        # else: Continue
        for byte_counter in range(len(written_bytes)):
            if written_bytes[byte_counter] != returned_bytes[byte_counter]:
                # Problem - Data mismatch
                errmsg = 'Data mismatch at index {}'.format(byte_counter)
                self.logger.error(errmsg)
                raise sd.SkarabProgrammingError(errmsg)
            # else: Continue

        # All data matched
        return True

    # region === SpartanFlashReconfig ===

    def spartan_flash_reconfig(self, filename, blind_reconfig=False):
        """
        This is the entire function that makes the necessary function calls
        to reconfigure the Spartan's Flash

        :param filename: The actual .ufp file that is to be written to
            the Spartan FPGA
        :param blind_reconfig: Reconfigure the board and don't wait to verify
            what has been written
        :return: Boolean - Success/Fail - 1/0
        """
        # TODO: Figure out how we can use get_spartan_firmware_version in
        # checking versions

        # For completeness, make sure the input file is of a .bin disposition
        if os.path.splitext(filename)[1] != '.ufp':
            # File extension was not .ufp
            errmsg = 'Please use .ufp file to reconfigure Spartan FPGA'
            self.logger.error(errmsg)
            raise sd.SkarabInvalidBitstream(errmsg)

        # Before we breakdown the bitstream, check the SpartanFirmwareVersion
        self.enable_isp_flash()

        # Currently there is no real method of confirming the integrity
        # of the data in the input .ufp file
        self.logger.debug('Checking input .ufp bitstream...')
        (result, image_to_program) = skfops.check_ufp_bitstream(filename)
        if not result:
            errmsg = 'Incompatible .ufp file detected.'
            self.logger.error(errmsg)
            raise sd.SkarabInvalidBitstream(errmsg)

        self.logger.debug('SPARTAN FLASH RECONFIG: Analysing Words')
        num_pages, num_sectors = skfops.analyse_ufp_bitstream(image_to_program)

        if (num_pages == 0) or (num_sectors == 0):
            # Problem
            errmsg = 'Failed to Analyse File successfully'
            self.logger.error(errmsg)
            raise sd.SkarabInvalidBitstream(errmsg)
        # else: Continue

        self.logger.debug('SPARTAN FLASH RECONFIG: Erasing SPI Sectors')
        if not self.erase_sectors(num_sectors):
            # Problem
            errmsg = 'Failed to Erase SPI Sectors'
            self.logger.error(errmsg)
            raise sd.SkarabProgrammingError(errmsg)
        # else: Continue

        # Second part of the Magic Flash Bytes
        self.disable_isp_flash()

        self.logger.debug('SPARTAN FLASH RECONFIG: Programming Words to SPI Sectors')
        self.enable_isp_flash()

        # For Debug purposes
        # (result, returned_data) = self.program_pages(
        #     image_to_program, num_pages)
        if not self.program_pages(image_to_program, num_pages):
            # Problem
            errmsg = 'Failed to Program SPI Sectors'
            self.logger.error(errmsg)
            raise sd.SkarabProgrammingError(errmsg)
        # else: Continue

        self.disable_isp_flash()

        if not blind_reconfig:
            self.logger.debug('VIRTEX FLASH RECONFIG: Verifying words that '
                         'were written to Flash Memory')
            self.enable_isp_flash()
            if not self.verify_bytes(image_to_program):
                # Problem
                errmsg = 'Failed to Verify data programmed SPI Sectors'
                self.logger.error(errmsg)
                raise sd.SkarabProgrammingError(errmsg)
                # else: Continue

            self.disable_isp_flash()

        # Print new SpartanFirmwareVersion
        # new_firmware_version = self.get_spartan_firmware_version()
        # --> Can't do that anymore! Spartan Firmware Version only
        # updates after full power cycle!
        debugmsg = 'Please do a full power cycle of the SKARAB in order to ' \
                   'complete SpartanFlashReconfig Process'
        self.logger.debug(debugmsg)
        return True

    # endregion

    # endregion

    # region --- SKARAB ADC-related methods ---

    # region === Direct SPI Write ===
    # - Perform an SPI write on the SKARAB ADC mezzanine
    def direct_spi_write(self, mezzanine_site, spi_destination, address, data):
        i2c_interface = mezzanine_site + 1

        # Write ADDRESS
        write_byte = (address >> 8) & 0xFF
        self.write_i2c(i2c_interface, sd.STM_I2C_DEVICE_ADDRESS, sd.DIRECT_SPI_ADDRESS_MSB_REG, write_byte)

        write_byte = (address & 0xFF)
        self.write_i2c(i2c_interface, sd.STM_I2C_DEVICE_ADDRESS, sd.DIRECT_SPI_ADDRESS_LSB_REG, write_byte)

        # Write DATA
        write_byte = (data >> 8) & 0xFF
        self.write_i2c(i2c_interface, sd.STM_I2C_DEVICE_ADDRESS, sd.DIRECT_SPI_DATA_MSB_REG, write_byte)

        write_byte = (data & 0xFF)
        self.write_i2c(i2c_interface, sd.STM_I2C_DEVICE_ADDRESS, sd.DIRECT_SPI_DATA_LSB_REG, write_byte)

        write_byte = spi_destination
        # Write, so read/not write bit is 0
        write_byte = write_byte | sd.START_DIRECT_SPI_ACCESS
        self.write_i2c(i2c_interface, sd.STM_I2C_DEVICE_ADDRESS, sd.DIRECT_SPI_CONTROL_REG, write_byte)

        # Wait for the update to complete
        self.write_i2c(i2c_interface, sd.STM_I2C_DEVICE_ADDRESS, sd.DIRECT_SPI_CONTROL_REG)
        read_byte = self.read_i2c(i2c_interface, sd.STM_I2C_DEVICE_ADDRESS, 1)

        timeout = 0
        while ((read_byte[0] & sd.START_DIRECT_SPI_ACCESS) != 0) and (timeout < 1000):
            self.write_i2c(i2c_interface, sd.STM_I2C_DEVICE_ADDRESS, sd.DIRECT_SPI_CONTROL_REG)
            read_byte = self.read_i2c(i2c_interface, sd.STM_I2C_DEVICE_ADDRESS, 1)

            timeout += 1

        if timeout == 1000:
            print("direct_spi_write ERROR: Timeout waiting for the SPI transaction to complete.")


    # region === Direct SPI Read ===
    # - Perform an SPI read on the SKARAB ADC mezzanine
    def direct_spi_read(self, mezzanine_site, spi_destination, address):
        """
		Low-level SPI read function used within other functions of this class.
        :param spi_destination:
        :param address:
        :return:
		"""
        i2c_interface = mezzanine_site + 1
        # Write ADDRESS
        write_byte = (address >> 8) & 0xFF
        self.write_i2c(i2c_interface, sd.STM_I2C_DEVICE_ADDRESS,
                                    sd.DIRECT_SPI_ADDRESS_MSB_REG, write_byte)

        write_byte = (address & 0xFF)
        self.write_i2c(i2c_interface, sd.STM_I2C_DEVICE_ADDRESS,
                                    sd.DIRECT_SPI_ADDRESS_LSB_REG, write_byte)


        write_byte = spi_destination | START_DIRECT_SPI_ACCESS | DIRECT_SPI_READ_NOT_WRITE
        self.write_i2c(i2c_interface, sd.STM_I2C_DEVICE_ADDRESS,
                                    sd.DIRECT_SPI_CONTROL_REG, write_byte)

        # Wait for the update to complete
        self.write_i2c(i2c_interface, sd.STM_I2C_DEVICE_ADDRESS, sd.DIRECT_SPI_CONTROL_REG)
        read_byte = self.read_i2c(i2c_interface, sd.STM_I2C_DEVICE_ADDRESS, 1)

        timeout = 0
        while (((read_byte[0] & sd.START_DIRECT_SPI_ACCESS) != 0) and (timeout < 1000)):
            self.write_i2c(i2c_interface, sd.STM_I2C_DEVICE_ADDRESS, sd.DIRECT_SPI_CONTROL_REG)
            read_byte = self.read_i2c(i2c_interface, sd.STM_I2C_DEVICE_ADDRESS, 1)

            timeout = timeout + 1

        if timeout == 1000:
            print("DirectSpiWrite ERROR: Timeout waiting for the SPI transaction to complete.")

        # Now get the read data
        self.write_i2c(i2c_interface, sd.STM_I2C_DEVICE_ADDRESS, sd.DIRECT_SPI_DATA_MSB_REG)
        read_byte = self.read_i2c(i2c_interface, sd.STM_I2C_DEVICE_ADDRESS, 1)

        read_word = read_byte[0] << 8

        self.write_i2c(i2c_interface, sd.STM_I2C_DEVICE_ADDRESS, sd.DIRECT_SPI_DATA_LSB_REG)
        read_byte = self.read_i2c(i2c_interface, sd.STM_I2C_DEVICE_ADDRESS, 1)

        read_word = read_word | read_byte[0]

        return read_word

    # endregion

    # endregion


    # region --- board level functions ---

    def check_programming_packet_count(self,
                                       timeout=None,
                                       retries=None):
        """
        Checks the number of packets programmed into the SDRAM of SKARAB

        :return: {num_ethernet_frames, num_ethernet_bad_frames,
            num_ethernet_overload_frames}
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        sdram_reconfigure_req = sd.SdramReconfigureReq(
            output_mode=sd.SDRAM_PROGRAM_MODE,
            clear_sdram=False,
            finished_writing=False,
            about_to_boot=False,
            do_reboot=False,
            reset_sdram_read_address=False,
            clear_ethernet_stats=False,
            enable_debug_sdram_read_mode=False,
            do_sdram_async_read=False,
            do_continuity_test=False,
            continuity_test_output_high=0x0,
            continuity_test_output_low=0x0)
        response = self.send_packet(sdram_reconfigure_req, timeout=timeout,
                                    retries=retries)
        packet_count = {
            'Ethernet Frames': response.packet['num_ethernet_frames'],
            'Bad Ethernet Frames': response.packet['num_ethernet_bad_frames'],
            'Overload Ethernet Frames':
                response.packet['num_ethernet_overload_frames']
        }
        return packet_count

    def get_virtex7_firmware_version(self, timeout=None, retries=None):
        """
        Read the version of the Virtex 7 firmware

        :return: golden_image, multiboot, firmware_major_version,
            firmware_minor_version
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        reg_data = self.read_board_reg(sd.C_RD_VERSION_ADDR, timeout=timeout,
                                       retries=retries)
        if reg_data:
            firmware_major_version = (reg_data >> 16) & 0x3fff
            firmware_minor_version = reg_data & 0xffff
            return reg_data >> 31, reg_data >> 30 & 0x1, '{}.{}'.format(
                firmware_major_version, firmware_minor_version)
        return None, None, None

    def get_microblaze_hardware_version(self):
        """
        Read the version of the microblaze hardware (SoC) implementation

        :return: soc_version (string)
        """
        reg_data = self.read_board_reg(sd.C_RD_SOC_VERSION_ADDR)
        if reg_data:
            soc_major_version = (reg_data >> 16) & 0x3fff
            soc_minor_version = reg_data & 0xffff
            return '{}.{}'.format(soc_major_version, soc_minor_version)

    def front_panel_status_leds(self, led_0_on, led_1_on, led_2_on, led_3_on,
                                led_4_on, led_5_on, led_6_on, led_7_on):
        """
        Control front panel status LEDs

        :param led_0_on: True: Turn LED 0 on, False: off
        :param led_1_on: True: Turn LED 1 on, False: off
        :param led_2_on: True: Turn LED 2 on, False: off
        :param led_3_on: True: Turn LED 3 on, False: off
        :param led_4_on: True: Turn LED 4 on, False: off
        :param led_5_on: True: Turn LED 5 on, False: off
        :param led_6_on: True: Turn LED 6 on, False: off
        :param led_7_on: True: Turn LED 7 on, False: off
        """
        led_mask = 0
        if led_0_on:
            led_mask = led_mask | sd.FRONT_PANEL_STATUS_LED0
        if led_1_on:
            led_mask = led_mask | sd.FRONT_PANEL_STATUS_LED1
        if led_2_on:
            led_mask = led_mask | sd.FRONT_PANEL_STATUS_LED2
        if led_3_on:
            led_mask = led_mask | sd.FRONT_PANEL_STATUS_LED3
        if led_4_on:
            led_mask = led_mask | sd.FRONT_PANEL_STATUS_LED4
        if led_5_on:
            led_mask = led_mask | sd.FRONT_PANEL_STATUS_LED5
        if led_6_on:
            led_mask = led_mask | sd.FRONT_PANEL_STATUS_LED6
        if led_7_on:
            led_mask = led_mask | sd.FRONT_PANEL_STATUS_LED7
        self.write_board_reg(sd.C_WR_FRONT_PANEL_STAT_LED_ADDR, led_mask)

    def control_front_panel_leds_write(self, dsp_override=True):
        """
        Neatly packaged command that switches control of FrontPanelStatus LEDs
        between DSP and BSP control
        - Controlled via BSP by default
        :param dsp_override: Boolean - 1/0 - True/False
        :return: Boolean - 1/0 - True/False
        """

        # Easiest to just write the value, then check it
        result = self.write_board_reg(sd.C_WR_DSP_OVERRIDE_ADDR, dsp_override)

        if result.packet['reg_data_low'] != dsp_override:
            # Problem
            errmsg = 'Failed to switch control of FrontPanel LEDs...'
            self.logger.error(errmsg)
            raise SkarabWriteFailed(errmsg)

        # else: Success
        led_controller = 'DSP Design' if dsp_override else 'Board Support Package'
        debugmsg = 'Successfully changed control of FrontPanel LEDs to {}...'.format(led_controller)
        self.logger.debug(debugmsg)
        print(debugmsg)
        return True

    def control_front_panel_leds_read(self):
        """
        Neatly packaged command that checks who is controlling FrontPanelStatus LEDs
        - Controlled via BSP by default
        :return:
        """
        result = self.read_board_reg(sd.C_RD_DSP_OVERRIDE_ADDR)

        if result:
            debugmsg = 'DSP Design is controlling FrontPanel LEDs...'
        else:
            debugmsg = 'Board Support Package is controlling FrontPanel LEDs...'

        self.logger.debug(debugmsg)
        print(debugmsg)

    def control_front_panel_leds_write(self, dsp_override=True):
        """
        Neatly packaged command that switches control of FrontPanelStatus LEDs
        between DSP and BSP control
        - Controlled via BSP by default

        :param dsp_override: Boolean - 1/0 - True/False
        :return: Boolean - 1/0 - True/False
        """

        # Easiest to just write the value, then check it
        result = self.write_board_reg(sd.C_WR_DSP_OVERRIDE_ADDR, dsp_override)

        if result.packet['reg_data_low'] != dsp_override:
            # Problem
            errmsg = 'Failed to switch control of FrontPanel LEDs...'
            self.logger.error(errmsg)
            raise SkarabWriteFailed(errmsg)

        # else: Success
        led_controller = 'DSP Design' if dsp_override else 'Board Support Package'
        debugmsg = 'Successfully changed control of FrontPanel LEDs to {}...'.format(led_controller)
        self.logger.debug(debugmsg)
        print(debugmsg)
        return True

    def control_front_panel_leds_read(self):
        """
        Neatly packaged command that checks who is controlling FrontPanelStatus LEDs
        - Controlled via BSP by default
        """
        result = self.read_board_reg(sd.C_RD_DSP_OVERRIDE_ADDR)

        if result:
            debugmsg = 'DSP Design is controlling FrontPanel LEDs...'
        else:
            debugmsg = 'Board Support Package is controlling FrontPanel LEDs...'

        self.logger.debug(debugmsg)
        print(debugmsg)

    def _prepare_sdram_ram_for_programming(self):
        """
        Prepares the sdram for programming with FPGA image
        """
        # put sdram in flash mode to enable FPGA outputs
        try:
            self.sdram_reconfigure(output_mode=sd.FLASH_MODE)
        except SkarabSdramError:
            errmsg = 'Error putting SDRAM in flash mode.'
            self.logger.error(errmsg)
            raise SkarabSdramError(errmsg)
        # clear sdram and clear ethernet counters
        try:
            self.sdram_reconfigure(clear_sdram=True, clear_eth_stats=True)
        except SkarabSdramError:
            errmsg = 'Error clearing SDRAM.'
            self.logger.error(errmsg)
            raise SkarabSdramError(errmsg)
        # put in sdram programming mode
        try:
            self.sdram_reconfigure()
        except SkarabSdramError:
            errmsg = 'Error putting SDRAM in programming mode.'
            self.logger.error(errmsg)
            raise SkarabSdramError(errmsg)
        self.logger.info('SDRAM successfully prepared.')

    def _complete_sdram_configuration(self):
        """
        Completes sdram programming and configuration. Sets to boot from sdram
        and triggers reboot

        :return: True if success
        """
        try:
            self.sdram_reconfigure(about_to_boot=True)
        except SkarabSdramError:
            errmsg = 'Error enabling boot from SDRAM.'
            self.logger.error(errmsg)
            raise SkarabSdramError(errmsg)
        try:
            self.sdram_reconfigure(do_reboot=True)
        except SkarabSdramError:
            errmsg = 'Error triggering reboot.'
            self.logger.error(errmsg)
            raise SkarabSdramError(errmsg)
        self.logger.info('Skarab is rebooting from SDRAM.')

    def write_hmc_i2c(self, interface, slave_address, write_address, write_data,
                     timeout=None,
                     retries=None):
        """
        Write a register on the HMC device via the I2C interface
        Also returns the data
        :param interface: identifier for i2c interface:
                          0 - SKARAB Motherboard i2c
                          1 - Mezzanine 0 i2c
                          2 - Mezzanine 1 i2c
                          3 - Mezzanine 2 i2c
                          4 - Mezzanine 3 i2c
        :param slave_address: I2C slave address of device to write
        :param write_address: register address on device to write
        :param write_data: data to write to HMC device
        :return: operation status: 0 - Fail, 1 - Success
        """

        if timeout is None: timeout = self.timeout
        if retries is None: retries = self.retries

        # hmc addresses are 24/32bit, pack them as 4 Bytes (32 bits)
        unpacked_addr = struct.unpack('!4B', struct.pack('!I', write_address))
        write_address = ''.join([struct.pack('!H', x) for x in unpacked_addr])

        unpacked_data = struct.unpack('!4B', struct.pack('!I', write_data))
        write_data = ''.join([struct.pack('!H', x) for x in unpacked_data])

        request = sd.WriteHMCI2CReq(interface, slave_address, write_address, write_data)
        response = self.send_packet(request, timeout=timeout, retries=retries)
        if response is None:
            errmsg = 'Invalid response to HMC I2C write request.'
            raise SkarabInvalidResponse(errmsg)
        if not response.packet['write_success']:
            errmsg = 'HMC I2C write failed!'
            raise SkarabWriteFailed(errmsg)
        return response.packet['write_success']

    def read_hmc_i2c(self, interface, slave_address, read_address,
                     format_print=False,
                     timeout=None,
                     retries=None):
        """
        Read a register on the HMC device via the I2C interface
        Prints the data in binary (32-bit) and hexadecimal formats
        Also returns the data

        :param interface: identifier for i2c interface:
                          0 - SKARAB Motherboard i2c
                          1 - Mezzanine 0 i2c
                          2 - Mezzanine 1 i2c
                          3 - Mezzanine 2 i2c
                          4 - Mezzanine 3 i2c
        :param slave_address: I2C slave address of device to read
        :param read_address: register address on device to read
        :param format_print: print the read data in more readable form
        :return: read data / None if fails
        """
        if timeout is None: timeout = self.timeout
        if retries is None: retries = self.retries

        # handle read address (pack it as 4 16-bit words)
        # TODO: handle this in the createPayload method
        unpacked = struct.unpack('!4B', struct.pack('!I', read_address))
        read_address = ''.join([struct.pack('!H', x) for x in unpacked])
        request = sd.ReadHMCI2CReq(interface, slave_address, read_address)
        response = self.send_packet(request, timeout=timeout, retries=retries)
        if response is None:
            errmsg = 'Invalid response to HMC I2C read request.'
            raise SkarabInvalidResponse(errmsg)
        if not response.packet['read_success']:
            errmsg = 'HMC I2C read failed!'
            raise SkarabReadFailed(errmsg)
        hmc_read_bytes = response.packet['read_bytes']  # this is the 4 bytes
        # read
        # from the register
        # want to create a 32 bit value
        hmc_read_word = struct.unpack(
            '!I', struct.pack('!4B', *hmc_read_bytes))[0]
        if format_print:
            self.logger.info('Binary: \t {:#032b}'.format(hmc_read_word))
            self.logger.info('Hex:    \t ' + '0x' + '{:08x}'.format(hmc_read_word))
        return hmc_read_word

    def get_skarab_version_info(self):
        """
        Get version info of all SKARAB components

        :return: dictionary containing all SKARAB version numbers:
        {
         virtex7_firmware_version:,
         embedded_software_version:,
         spartan_firmware_version:,
         soc_version:,
         multiboot_image:,
         golden_image:,
         toolflow_image:
         }
        """
        golden_image, multiboot_image, firmware_version = \
            self.get_virtex7_firmware_version()
        return {
            'golden_image': bool(golden_image),
            'multiboot_image': bool(multiboot_image),
            'virtex7_firmware_version': firmware_version,
            'toolflow_image': not (golden_image or multiboot_image),
            'spartan_firmware_version': self.get_spartan_firmware_version(),
            'microblaze_hardware_version':
                self.get_microblaze_hardware_version(),
            'microblaze_software_version': self.get_embedded_software_version(),
        }

    def get_sensor_data(self,
                        timeout=None,
                        retries=None):
        """
        Get sensor data.

        Units:

        * Fan Speed - RPM
        * Fan Speed PWM - PWM %
        * Temperature Sensors - degrees Celsius
        * Voltage - Volts (V)
        * Currents - Amps (A)

        :return: all sensor data rolled up into a dictionary
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        def sign_extend(value, bits):
            """
            Performs 2's compliment sign extension

            :param value: value to sign extend
            :param bits: number of bits making up the value
            :return: sign extended value
            """
            sign_bit = 1 << (bits - 1)
            return (value & (sign_bit - 1)) - (value & sign_bit)

        def temperature_value_check(value):
            """
            Checks the value returned from the temperature sensor and handles
            it accordingly.

            :param value: value returned from temperature sensor
            :return: correct temperature value
            """
            if value == 0x7FFF:
                return 'Error'
            if value & 0x8000 != 0:
                value = sign_extend(value,
                                    16)  # 16 bits represent the temperature
            value = int(value)
            value = float(value)
            return round(value / 100.0, 2)

        def mezzanine_temperature_check_qsfp(value):
            """
            Checks the returned QSFP mezzanine temperatures and handles them
            accordingly.

            NB: This sensor doesn't reliably report the temperature and is omitted.

            :param value: value returned from the temperature sensor
            :return: correct mezzanine temperature value
            """
            # scale the measured voltage
            temp = ((value / 1000.0) + 2.34) / 4.0
            temp = ((temp - 0.76) / 0.0025) + 25.0

            # convert to temperature
            return round(temp, 2)

        def mezzanine_temperature_check_hmc(value):
            """
            Checks the returned HMC mezzanine temperatures and handles them
            accordingly.
            :param value: value returned from the temperature sensor
            :return: correct mezzanine temperature value
            """
            # scale the measured voltage
            temp = value
            temp = -1.0 * ((5.506 - math.sqrt(30.316036 + 0.00704 * (870.6
                                                                     -
                                                                     temp)))
                           / 0.00352) + 30.0

            # convert to temperature
            return round(temp, 2)

        def voltage_current_monitor_temperature_check(value):
            """
            Checks the value returned for the voltage monitor temperature
            and handles it appropriately to extract the actual temperature
            value from the received data
            :param value: value returned by voltage monitor temperature sensor
            :return: correct temperature value
            """
            # get lower 11 bits
            mantissa = value & 0x07FF
            if (mantissa & 0x400) != 0:
                # lower 11 bits are for mantissa
                mantissa = sign_extend(mantissa, 11)
            mantissa = int(mantissa)
            # get upper 5 bits
            exponent = (value >> 11) & 0x1F
            if (exponent & 0x10) != 0:
                # upper 5 bits are for exponent
                exponent = sign_extend(exponent, 5)
            exponent = int(exponent)
            return round(float(mantissa) * pow(2.0, float(exponent)), 2)

        def voltage_handler(raw_sensor_data, index):
            """
            Handles the data returned by the voltage monitor for the various
            board voltages. Returns actual voltages extracted from this data.

            :param raw_sensor_data: array containing raw sensor data
            :param index: index at which next voltage sensor data begins
            :return: extracted voltage
            """
            voltage = raw_sensor_data[index]
            voltage_scale_factor = raw_sensor_data[index + 1]
            if (voltage_scale_factor & 0x10) != 0:
                voltage_scale_factor = sign_extend(voltage_scale_factor, 5)
            voltage_scale_factor = int(voltage_scale_factor)
            val = float(voltage) * float(pow(2.0, float(voltage_scale_factor)))
            return round(
                val * sd.voltage_scaling[str(raw_sensor_data[index + 2])],
                2)

        def current_handler(raw_sensor_data, index):
            """
            Handles the data returned by the current monitor for the various
            board currents. Returns actual current extracted from this data.

            :param raw_sensor_data: array containing raw sensor data
            :param index: index at which next current sensor data begins
            :return: extracted current
            """

            current = raw_sensor_data[index]
            scale_factor = raw_sensor_data[index + 1]

            if (scale_factor & 0x10) != 0:
                scale_factor = sign_extend(scale_factor, 5)

                scale_factor = int(scale_factor)

            val = float(current) * float(pow(2.0, float(scale_factor)))

            return round(
                val * sd.current_scaling[str(raw_sensor_data[index + 2])],
                2)

        def check_fan_speed(fan_name, value):
            """
            Checks if a given fan is running within acceptable limits

            :param fan_name: fan to be checked
            :param value: fan speed value
            :return: OK, WARNING or ERROR
            """
            if value > sd.fan_speed_ranges[fan_name][0] or value < \
                        sd.fan_speed_ranges[fan_name][1]:
                return 'warning'
            else:
                return 'nominal'

        def check_temperature(sensor_name, value, inlet_ref):
            """
            Checks if a given temperature is within acceptable range

            :param sensor_name: temperature to check
            :param value: temperature value
            :param inlet_ref: inlet temperature; used as reference for other
                temperature thresholds
            :return: OK, WARNING or ERROR
            """

            if sensor_name == 'inlet_temperature_degC':
                if value > sd.temperature_ranges[sensor_name][0] or value < \
                        sd.temperature_ranges[sensor_name][1]:
                    return 'error'
                else:
                    return 'nominal'
            else:
                # handle hmc die temperatures of unintialised HMC cores
                if 'hmc' in sensor_name:
                    if value == 0xffeeddcc:
                        # umc unitialised
                        return 'unknown'
                if value > inlet_ref + sd.temperature_ranges[sensor_name][
                    0] or value < inlet_ref + \
                        sd.temperature_ranges[sensor_name][1]:
                    return 'error'
                else:
                    return 'nominal'

        def check_current(current_name, value):
            """
            Checks if a given PSU current reading is within acceptable range

            :param current_name: current to check
            :param value: value of the sensor
            :return: OK, WARNING or ERROR
            """

            if value > sd.current_ranges[current_name][0] or value < \
                    sd.current_ranges[current_name][1]:
                # return '\033[0;31m{}\033[00m'.format('error')
                return 'error'

            else:
                # return '\033[0;31m{}\033[00m'.format('nominal')
                return 'nominal'

        def check_voltage(voltage_name, value):
            """
            Checks if a given PSU voltage reading is within acceptable range

            :param voltage_name: voltage to check
            :param value: value of the sensor
            :return: OK, WARNING or ERROR
            """

            if value > sd.voltage_ranges[voltage_name][0] or value < \
                    sd.voltage_ranges[voltage_name][1]:
                # return '\033[0;31m{}\033[00m'.format('error')
                return 'error'
            else:
                # return '\033[0;31m{}\033[00m'.format('nominal')
                return 'nominal'

        def parse_fan_speeds_rpm(raw_sensor_data):
            for key, value in sd.sensor_list.items():
                if 'fan_rpm' in key:
                    fan_speed = raw_sensor_data[value]
                    self.sensor_data[key] = (
                            fan_speed, 'rpm', check_fan_speed(key, fan_speed))

        def parse_fan_speeds_generic(raw_sensor_data):
            for key, value in sd.sensor_list.items():
                if 'fan_pwm' in key:
                    self.sensor_data[key] = round(
                        raw_sensor_data[value] / 100.0, 2)

        def parse_fan_speeds_pwm(raw_sensor_data):
            for key, value in sd.sensor_list.items():
                if 'fan_pwm' in key:
                    pwm_value = round(raw_sensor_data[value] / 100.0, 2);
                    if(pwm_value > 100 or pwm_value < 0):
                        message = 'error'
                    else:
                        message = 'nominal'
                    self.sensor_data[key] = (
                            pwm_value, '%',message)

        def parse_temperatures(raw_sensor_data):
            # inlet temp (reference)
            inlet_ref = temperature_value_check(
                raw_sensor_data[sd.sensor_list['inlet_temperature_degC']])
            for key, value in sd.sensor_list.items():
                if 'temperature' in key:
                    if 'voltage' in key or 'current' in key:
                        temperature = voltage_current_monitor_temperature_check(
                            raw_sensor_data[value])
                        self.sensor_data[
                            key] = (temperature, 'degC',
                                    check_temperature(key, temperature,
                                                      inlet_ref=0))
                    elif 'hmc' in key:
                        temperature = struct.unpack(
                            '!I', struct.pack('!4B',
                                              *raw_sensor_data[value:value+4]))[0]
                        self.sensor_data[key] = (-1 if temperature==0xffeeddcc else temperature,
                                                 'degC',
                                                 check_temperature(key, temperature, inlet_ref=0))

                    # ignore the mezzanine temperatures as these are unreliable
                    elif 'mezzanine' in key:
                        continue

                    else:
                        temperature = temperature_value_check(
                            raw_sensor_data[value])
                        self.sensor_data[key] = (temperature, 'degC',
                                                 check_temperature(key,
                                                                   temperature,
                                                                   inlet_ref=0))

        def parse_mezzanine_temperatures(raw_sensor_data):
            for key, value in sd.sensor_list.items():

                if 'mezzanine' in key:
                    temperature = mezzanine_temperature_check_hmc(raw_sensor_data[value])
                    self.sensor_data[key] = (temperature, 'degC', check_temperature(key, temperature, inlet_ref=0))

        def parse_voltages(raw_sensor_data):
            for key, value in sd.sensor_list.items():
                if '_voltage' in key:
                    voltage = voltage_handler(raw_sensor_data, value)
                    self.sensor_data[key] = (voltage, 'volts',
                                             check_voltage(key, voltage))

        def parse_currents(raw_sensor_data):
            for key, value in sd.sensor_list.items():
                if '_current' in key:
                    current = current_handler(raw_sensor_data, value)
                    self.sensor_data[key] = (current, 'amperes',
                                             check_current(key, current))

        request = sd.GetSensorDataReq()
        response = self.send_packet(request, timeout=timeout, retries=retries)
        if response is not None:
            # raw sensor data received from SKARAB
            recvd_sensor_data_values = response.packet['sensor_data']
            # parse the raw data to extract actual sensor info
            parse_fan_speeds_generic(recvd_sensor_data_values)
            parse_fan_speeds_rpm(recvd_sensor_data_values)
            parse_fan_speeds_pwm(recvd_sensor_data_values)
            parse_currents(recvd_sensor_data_values)
            parse_voltages(recvd_sensor_data_values)
            parse_temperatures(recvd_sensor_data_values)

            # disable mezzanine temperatures as these values are not reliable
            # parse_mezzanine_temperatures(recvd_sensor_data_values)

            return self.sensor_data

        else:
            raise SkarabInvalidResponse('Error reading board temperatures')

    def set_fan_speed(self, fan_page, pwm_setting,
                      timeout=None,
                      retries=None):
        """
        Sets the speed of a selected fan on the SKARAB motherboard. Desired
        speed is given as a PWM setting: range: 0.0 - 100.0

        :param fan_page: desired fan
        :param pwm_setting: desired PWM speed (as a value from 0.0 to 100.0)
        :return: (new_fan_speed_pwm, new_fan_speed_rpm)
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        # check desired fan speed
        if pwm_setting > 100.0 or pwm_setting < 0.0:
            self.logger.error('Given speed out of expected range.')
            return
        request = sd.SetFanSpeedReq(fan_page, pwm_setting)
        response = self.send_packet(request, timeout=timeout, retries=retries)
        if response is not None:
            return (response.packet['fan_speed_pwm'] / 100.0,
                    response.packet['fan_speed_rpm'])
        return

    def post_get_system_information(self):
        """
        Cleanup run after get_system_information
        """
        # Fix the memory mapping for SKARAB registers by masking the most
        # significant bit of the register address parsed from the fpg file.
        for key in self.memory_devices.keys():
            self.memory_devices[key].address &= 0x7fffffff

    # endregion

    # sensor functions

    def configure_i2c_switch(self, switch_select):
        """
        Configures the PCA9546AD I2C switch.

        :param switch_select: the desired switch configuration:
               Fan Controller = 1
               Voltage/Current Monitor = 2
               1GbE = 4

        :return: True or False
        """

        if not self.write_i2c(sd.MB_I2C_BUS_ID, sd.PCA9546_I2C_DEVICE_ADDRESS,
                              switch_select):
            self.logger.error('Failed to configure I2C switch.')
            return False
        else:
            self.logger.debug('I2C Switch successfully configured')
            return True

    def get_spartan_checksum(self):
        """
        Method for easier access to the Spartan Checksum

        :return: spartan_flash_write_checksum
        """
        rd_addr = sd.SPARTAN_SPI_REG_ADDR + sd.SPARTAN_CHECKSUM_UPPER_OFFSET
        upper_byte = self.read_spi_page(rd_addr, 1)[0]
        rd_addr = sd.SPARTAN_SPI_REG_ADDR + sd.SPARTAN_CHECKSUM_LOWER_OFFSET
        lower_byte = self.read_spi_page(rd_addr, 1)[0]
        spartan_flash_write_checksum = (upper_byte << 8) | lower_byte
        return spartan_flash_write_checksum

    def get_spartan_firmware_version(self):
        """
        Get a string representation of the firmare_version
        """
        (major, minor) = self.get_spartan_firmware_version_tuple()
        return str(major) + '.' + str(minor)

    def get_spartan_firmware_version_tuple(self):
        """
        Using read_spi_page() function to read two SPI Addresses which give
        the major and minor version numbers of the SPARTAN Firmware Version

        :return: Integer Tuple (Major, Minor)
        """
        # Just a heads-up, read_spi_page(address, num_bytes)
        # returns a list of bytes of length = num_bytes
        rd_major = sd.SPARTAN_SPI_REG_ADDR + sd.SPARTAN_MAJOR_VER_OFFSET
        rd_minor = sd.SPARTAN_SPI_REG_ADDR + sd.SPARTAN_MINOR_VER_OFFSET
        major = self.read_spi_page(rd_major, 1)[0]
        minor = self.read_spi_page(rd_minor, 1)[0]
        return major, minor

    def multicast_receive(self, gbename, ip, mask,
                          timeout=None,
                          retries=None):
        """

        :param gbename:
        :param ip:
        :param mask:
        """
        if timeout is None: timeout=self.timeout
        if retries is None: retries=self.retries

        ip_high = ip.ip_int >> 16
        ip_low = ip.ip_int & (2 ** 16 - 1)
        mask_high = mask.ip_int >> 16
        mask_low = mask.ip_int & (2 ** 16 - 1)
        request = sd.ConfigureMulticastReq(1, ip_high, ip_low,
                                           mask_high, mask_low)
        response = self.send_packet(request, timeout=timeout, retries=retries)
        resp_pkt = response.packet
        resp_ip = IpAddress(resp_pkt['fabric_multicast_ip_address_high'] << 16 |
                            resp_pkt['fabric_multicast_ip_address_low'])
        resp_mask = IpAddress(
            resp_pkt['fabric_multicast_ip_address_mask_high'] << 16 |
            resp_pkt['fabric_multicast_ip_address_mask_low'])
        self.logger.debug('%s: multicast configured: addr(%s) mask(%s)' % (
            gbename, resp_ip.ip_str, resp_mask.ip_str))

    def one_wire_read_rom(self, one_wire_port, timeout=None, retries=None):
        """
        Reads the 64-bit ROM address of a DS24N33 EEPROM on the specified
        1-wire interface
        :param one_wire_port: 1-wire interface to access
        0 - skarab motherboard
        1 - 4 mezzanine 0 - 3
        :return: 64-bit ROM address
        """

        if timeout is None: timeout = self.timeout
        if retries is None: retries = self.retries

        request = sd.OneWireReadROMReq(one_wire_port)

        response = self.send_packet(request, timeout=timeout, retries=retries)
        if response is None:
            errmsg = 'Invalid response to One Wire Read Rom request.'
            raise SkarabInvalidResponse(errmsg)
        if not response.packet['read_success']:
            errmsg = 'One Wire Rom Read failed!'
            raise SkarabWriteFailed(errmsg)

        raw_address = ''.join([struct.pack('!B', x) for x in response.packet['rom']])
        real_address = struct.unpack('!Q', raw_address)[0]
        return real_address

    def one_wire_ds2433_write_mem(self, write_bytes, page, one_wire_port,
                                  device_rom=None, skip_rom_address=1,
                                  offset=0, timeout=None, retries=None,
                                  force_page_zero_write=False):
        """
        Write to the EEPROM of a connected device connected on the one-wire bus
        :param device_rom: 64-bit ROM address of device
        :param skip_rom_address: if enable, skip using the rom to address device,
        assumes only one device connected
        :param write_bytes: bytes to write, maximum of 32 bytes
        :param page: page to write [0 to 15]
        :param offset: offset at which to start writing in the page (max 31)
        :param one_wire_port: 1-wire interface to access
        0 - skarab motherboard
        1 - 4 mezzanine 0 - 3
        :param timeout:
        :param retries:
        :param force_page_zero_write: set to true to force writing to page zero
        :return: 1 if success
        """

        if timeout is None: timeout = self.timeout
        if retries is None: retries = self.retries

        if page not in range(16):
            raise ValueError('Selected page does not exist. Select page in range 0 - 15')

        if page == 0 and force_page_zero_write is not True:
            raise UserWarning('WARNING: trying to write to Page 0. If this is'
                              'what you want to do, set the '
                              'force_page_zero_write flag to true')

        # if a device rom is given, disable skip-rom
        if device_rom is not None:
            skip_rom_address=0
        else:
            # then set a dummy address
            device_rom = 0

        if offset > 31:
            raise ValueError('Maximum offset is 31 bytes')

        # need to pack device rom (pack each byte
        unpacked_device_rom = struct.unpack('!8B', struct.pack('!Q', device_rom))
        device_rom = ''.join([struct.pack('!H', x) for x in unpacked_device_rom])

        if type(write_bytes) != list:
            tmp = write_bytes
            write_bytes = [tmp]

        num_bytes = len(write_bytes)

        if num_bytes > 32:
            raise ValueError('Maximum number of bytes that can be written is 32')

        # pack the write bytes: pack each byte as a 16-bit word
        packed_write_bytes = [struct.pack('!H', x) for x in write_bytes]
        write_bytes = ''.join(packed_write_bytes) + ('\x00\x00' * (32-num_bytes))

        # determine target address 1 and target address 2
        target_address_1 = ((page*0x20) & 0xFF) + offset
        target_address_2 = (page*0x20) >> 8
        full_address = (target_address_2 << 8) + target_address_1

        # check if writing over a page boundary
        number_of_writable_bytes = 0x20 - (full_address % 0x20)

        if num_bytes> number_of_writable_bytes:
            raise ValueError('Cannot write across a page boundary!')

        request = sd.OneWireDS2433WriteMemReq(device_rom, skip_rom_address,
                                              write_bytes, num_bytes,
                                              target_address_1,
                                              target_address_2, one_wire_port)

        response = self.send_packet(request, timeout=timeout, retries=retries)
        if response is None:
            errmsg = 'Invalid response to One Wire Write request.'
            raise SkarabInvalidResponse(errmsg)
        if not response.packet['write_success']:
            errmsg = 'One Wire DS2433 Write failed!'
            raise SkarabWriteFailed(errmsg)
        return response.packet['write_success']

    def one_wire_ds2433_read_mem(self, one_wire_port, num_bytes, page, offset=0,
                                 device_rom=None, skip_rom_address=1,
                                 timeout=None, retries=None):
        """
        Read from the EEPROM of a connected device connected on the one-wire bus
        :param one_wire_port: 1-wire interface to access
        0 - skarab motherboard
        1 - 4 mezzanine 0 - 3
        :param num_bytes: number of bytes to read
        :param page: page to write [0 to 15]
        :param offset: offset at which to start reading in the page (max 31)
        :param device_rom: 64-bit ROM address of device
        :param skip_rom_address: if enable, skip using the rom to address device,
        assumes only one device connected
        :param timeout:
        :param retries:
        :return: read data
        """

        if timeout is None: timeout = self.timeout
        if retries is None: retries = self.retries

        if page not in range(16):
            raise ValueError('Selected page does not exist. Select page in range 0 - 15')

        # if a device rom is given, disable skip-rom
        if device_rom is not None:
            skip_rom_address = 0
        else:
            # then set a dummy address
            device_rom = 0

        if num_bytes > 32:
            raise ValueError('Maximum number of bytes that can be read is 32')

        if offset > 31:
            raise ValueError('Maximum offset is 31 bytes')

        # need to pack device rom (pack each byte
        unpacked_device_rom = struct.unpack('!8B',
                                            struct.pack('!Q', device_rom))
        device_rom = ''.join(
            [struct.pack('!H', x) for x in unpacked_device_rom])

        # determine target address 1 and target address 2
        target_address_1 = ((page * 0x20) & 0xFF) + offset
        target_address_2 = (page * 0x20) >> 8

        request = sd.OneWireDS2433ReadMemReq(device_rom, skip_rom_address,
                                             num_bytes, target_address_1,
                                             target_address_2, one_wire_port)

        response = self.send_packet(request, timeout=timeout, retries=retries)
        if response is None:
            errmsg = 'Invalid response to One Wire Read request.'
            raise SkarabInvalidResponse(errmsg)
        if not response.packet['read_success']:
            errmsg = 'One Wire DS2433 Read failed!'
            raise SkarabWriteFailed(errmsg)

        # do something clever here to return the number of bytes requested
        return response.packet['read_bytes'][0:num_bytes]

    # high level Mezzanine Flash Reads/Writes

    # for adjusting tunable configuration parameters stored in flash

    def set_dhcp_init_time(self, dhcp_init_time):
        """
        Set the init time for DHCP - time before first DHCP message is sent
        :param dhcp_init_time: the desired dhcp init time, in seconds
        :return: True if success, False if failed
        """

        assert(0 <= dhcp_init_time <= 120), 'DHCP init time range: 0 - 120 sec'

        # the uBlaze requires a count of 100ms increments

        dhcp_init_time = int((dhcp_init_time * 1000.0)/100.0)

        # need to pack data in little endian
        msb = (dhcp_init_time >> 8) & 0xff
        lsb = dhcp_init_time & 0xff
        write_bytes = [lsb, msb]

        #dhcp init time occupies bytes 0, 1
        offset = 0

        rv = self.one_wire_ds2433_write_mem(write_bytes=write_bytes,
                                            page=sd.TUNABLE_PARAMETERS_PAGE,
                                            one_wire_port=sd.MB_ONE_WIRE_PORT,
                                            device_rom=None, skip_rom_address=1,
                                            offset=offset, timeout=None, retries=None,
                                            force_page_zero_write=False)

        return rv

    def set_dhcp_retry_rate(self, dhcp_retry_rate):
        """
        Set the rate at which the SKARAB re-attempts to get DHCP configured
        :param dhcp_retry_rate: the desired dhcp retry rate, in seconds
        :return: True if success, False if failed
        """

        assert(0.5 <= dhcp_retry_rate <= 30), 'DHCP retry rate range: 0.5 - 30 sec'

        # the uBlaze requires a count of 100ms increments

        dhcp_retry_rate = int((dhcp_retry_rate * 1000.0) / 100.0)

        # need to pack data in little endian
        msb = (dhcp_retry_rate >> 8) & 0xff
        lsb = dhcp_retry_rate & 0xff
        write_bytes = [lsb, msb]

        #dhcp retry rate occupies bytes 2, 3
        offset = 2

        rv = self.one_wire_ds2433_write_mem(write_bytes=write_bytes,
                                       page=sd.TUNABLE_PARAMETERS_PAGE,
                                       one_wire_port=sd.MB_ONE_WIRE_PORT,
                                        device_rom=None, skip_rom_address=1,
                                        offset=offset, timeout=None, retries=None,
                                        force_page_zero_write=False)

        return rv

    def set_hmc_reconfig_timeout(self, hmc_reconfig_timeout=10):
        """
        Set the HMC reconfiguration state machine timeout,
        before triggering a motherboard reset to attempt to initialise all HMCs
        :param hmc_reconfig_timeout: the desired HMC reconfiguration timeout (default is 10 seconds)
        :return: True if success, False if failed
        """

        assert(1 <= hmc_reconfig_timeout <= 60), 'HMC Reconfig timeout range: 1 - 60 sec'

        # the uBlaze requires a count of 100ms increments

        hmc_reconfig_timeout = int((hmc_reconfig_timeout * 1000.0) / 100.0)

        # need to pack data in little endian
        msb = (hmc_reconfig_timeout >> 8) & 0xff
        lsb = hmc_reconfig_timeout & 0xff
        write_bytes = [lsb, msb]

        # hmc reconfig timeout occupies bytes 4, 5
        offset = 4

        rv = self.one_wire_ds2433_write_mem(write_bytes=write_bytes,
                                            page=sd.TUNABLE_PARAMETERS_PAGE,
                                            one_wire_port=sd.MB_ONE_WIRE_PORT,
                                            device_rom=None,
                                            skip_rom_address=1,
                                            offset=offset, timeout=None,
                                            retries=None,
                                            force_page_zero_write=False)

        return rv

    def set_hmc_reconfig_max_retries(self, max_retries):
        """
        Set the maximum number of retires for initialisation of all HMCs.
        Each retry is a motherboard reset.
        :param max_retries: desired number of max retries
        :return: True if success, False if failed
        """

        # this is an 8-bit number
        assert(3 <= max_retries <= 255), 'Maximum number of retries must be in the range 3 - 255'

        write_bytes = max_retries
        # hmc reconfig max retries occupies byte 6
        offset = 6

        rv = self.one_wire_ds2433_write_mem(write_bytes=write_bytes,
                                            page=sd.TUNABLE_PARAMETERS_PAGE,
                                            one_wire_port=sd.MB_ONE_WIRE_PORT,
                                            device_rom=None,
                                            skip_rom_address=1,
                                            offset=offset, timeout=None,
                                            retries=None,
                                            force_page_zero_write=False)

        return rv

    def set_link_mon_timeout(self, link_mon_timeout):
        """
        Set the link monitor timeout - how long to wait for the single 40GbE link
        to show activity. After this timeout, the motherboard resets in attempt to
        re-initialise the link. Only monitors the RX side of the link.
        :param link_mon_timeout: desired link mon timeout, minimum time is 30 sec
        :return: True if success, False if failed
        """

        assert(link_mon_timeout >= 30), 'Minimum link monitor timeout is 30 seconds'

        # the uBlaze requires a count of 100ms increments

        link_mon_timeout = int((link_mon_timeout * 1000.0) / 100.0)

        # need to pack data in little endian
        msb = (link_mon_timeout >> 8) & 0xff
        lsb = link_mon_timeout & 0xff
        write_bytes = [lsb, msb]

        # hmc reconfig timeout occupies bytes 7, 8
        offset = 7

        rv = self.one_wire_ds2433_write_mem(write_bytes=write_bytes,
                                            page=sd.TUNABLE_PARAMETERS_PAGE,
                                            one_wire_port=sd.MB_ONE_WIRE_PORT,
                                            device_rom=None,
                                            skip_rom_address=1,
                                            offset=offset, timeout=None,
                                            retries=None,
                                            force_page_zero_write=False)

        return rv

    def get_tunable_parameters(self):
        """
        Read back the current values of the tuneable parameters (dhcp init time,
        dhcp rety rate, hmc reconfig timeout, hmc reconfig max retries, link mon
        timeout)
        :return: dict
        """

        # get the data, 9 bytes to be read
        data = self.one_wire_ds2433_read_mem(sd.MB_ONE_WIRE_PORT, 9,
                                             sd.TUNABLE_PARAMETERS_PAGE)

        tunable_params = {'dhcp_init_time': (data[0] + (data[1] << 8))/10.0,
                          'dhcp_retry_rate': (data[2] + (data[3] << 8))/10.0,
                          'hmc_reconfig_timeout': (data[4] + (data[5] << 8))/10.0,
                          'hmc_reconfig_max_retries': data[6],
                          'link_mon_timeout': (data[7] + (data[8] << 8))/10.0}

        return tunable_params

    # for retrieving the HMC reconfiguration statistics

    def get_hmc_reconfigure_stats(self, *hmcs):
        """
        Get the reconfiguration statistics for a specific HMC card or cards
        :param hmc: the hmc card index (or indices) - 0, 1 or 2 - to read the stats for
        :return: dictionary: {hmcN : {hmc_retires: # ; hmc_total_retries: #}}
        or None if failed
        """

        hmc_reconfigure_stats = {}

        for hmc in hmcs:
            stats = {}
            prefix = 'hmc_{}'.format(hmc)

            raw_data = self.one_wire_ds2433_read_mem(one_wire_port=sd.HMC_CARD_I2C_PORT_MAP[hmc],
                                                 num_bytes=8, page=sd.HMC_STATISTICS_PAGE, offset=0,
                                                 device_rom=None, skip_rom_address=1,
                                                 timeout=None, retries=None)

            hmc_retries_raw = raw_data[:4]
            hmc_total_retries_raw = raw_data[4:]

            # data is stored in little endian, hence use '<' to unpack
            hmc_retries = struct.unpack('<I', struct.pack('!4B', *hmc_retries_raw))[0]
            hmc_total_retries = struct.unpack('<I', struct.pack('!4B', *hmc_total_retries_raw))[0]

            stats['hmc_init_failures_recorded'] = hmc_retries
            stats['hmc_total_init_retries_recorded'] = hmc_total_retries

            hmc_reconfigure_stats[prefix] = stats

        return hmc_reconfigure_stats

    # for reading the mezzanine signatures stored in flash

    def get_mezzanine_signature(self, mezzanine_card_one_wire_port):
        """
        Get the signature of a a specific mezzanine card
        :param mezzanine_card_one_wire_port: one wire port of the mezzanine
        care to read
        :return: success: card signature (as an integer), fail: None
        """

        raw_data = self.one_wire_ds2433_read_mem(one_wire_port=mezzanine_card_one_wire_port,
                                                 num_bytes=7, page=sd.MEZZANINE_SIGNATURES_PAGE,
                                                 offset=0,
                                                 device_rom=None, skip_rom_address=1,
                                                 timeout=None, retries=None)

        # select the bytes of interest
        selected_data = [raw_data[i] for i in [0, 4, 5, 6]]

        signature = struct.unpack('!I', struct.pack('!4B', *selected_data))[0]

        return signature

    def reset_dhcp_state_machine(self, link_id=1, timeout=None, retries=None):
        """
        Resets the DHCP State Machine for the given link
        :param link_id: id of the interface on which DHCP is to be reset
        1 - 40GbE
        0 - 1GbE
        :return: True if reset issued successfully
        """

        error_dictionary = {1: 'No such link id',
                            2: 'Specified link is down'}

        request = sd.ResetDHCPStateMachineReq(link_id)

        response = self.send_packet(request, timeout=timeout, retries=retries)

        # check if the request was successful
        if not response.packet['reset_error']:
            self.logger.info(
                'DHCP state machine reset issued on link_id: {}'.format(
                    response.packet['link_id']))
            return True
        else:
            err = 'DHCP state machine reset on link_id: {} failed! {}.'.format(
                response.packet['link_id'],
                error_dictionary[response.packet['reset_error']])
            raise SkarabUnknownDeviceError(err)

    # TODO: only declare this function once! Will have to be global
    @staticmethod
    def _sign_extend(value, bits):
        """
        Performs 2's compliment sign extension
        :param value: value to sign extend
        :param bits: number of bits making up the value
        :return: sign extended value
        """
        sign_bit = 1 << (bits - 1)
        return (value & (sign_bit - 1)) - (value & sign_bit)

    def _voltage_handler_logging(self, voltage, scale_factor, device_page):
        """
        Handles the data returned by the voltage monitor for the various
        board voltages. Returns actual voltages extracted from this data.
        :param raw_sensor_data: array containing raw sensor data
        :param index: index at which next voltage sensor data begins
        :return: extracted voltage
        """

        if (scale_factor & 0x10) != 0:
            scale_factor = self._sign_extend(scale_factor, 5)
        scale_factor = int(scale_factor)
        val = float(voltage) * float(pow(2.0, float(scale_factor)))
        return round(val * sd.voltage_scaling[str(device_page)], 2)

    def _current_handler_logging(self, current, scale_factor, device_page):
        """
        Handles the data returned by the current monitor for the various
        board currents. Returns actual current extracted from this data.
        :param raw_sensor_data: array containing raw sensor data
        :param index: index at which next current sensor data begins
        :return: extracted current
        """

        if (scale_factor & 0x10) != 0:
            scale_factor = self._sign_extend(scale_factor, 5)
        scale_factor = int(scale_factor)
        val = float(current) * float(pow(2.0, float(scale_factor)))

        return round(
            val * sd.current_scaling[str(device_page)], 2)

    def get_max31785_hw_logs(self, timeout=None, retries=None):
        """
        Retrieve the non-volatile logs from the MAX31785 fan controller on the
        SKARAB.
        :param timeout:
        :param retries:
        :return: log data in the form [FAULT_LOG_INDEX, FAULT_LOG_COUNT,
        STATUS_WORD, STATUS_VOUT Pages 17/18,
        STATUS_VOUT Pages 19/20, STATUS_VOUT Pages 21/22,
        STATUS_MFR_SPECIFIC Pages 6/7, STATUS_MFR_SPECIFIC Pages 8/9,
        STATUS_MFR_SPECIFIC Pages 10/11, STATUS_MFR_SPECIFIC Pages 12/13,
        STATUS_MFR_SPECIFIC Pages 14/15, STATUS_MFR_SPECIFIC Pages 16/00h,
        STATUS_FANS_1_2 Pages 0/1, STATUS_FANS_1_2 Pages 2/3,
        STATUS_FANS_1_2 Pages 4/5]
        """

        request = sd.GetFanControllerLogsReq()
        response = self.send_packet(request, timeout=timeout, retries=retries)

        # check if logs were read successfully
        if not response.packet['log_entry_success']:
            err = 'fan controller non-volatile log retrieval failed'
            raise NonVolatileLogRetrievalError(err)

        """
        log entry structure:
        log[0] - fault log index
        log[1] - fault log count
        log[2] - status word
        log[3] - status_vout pages 18/17
        log[4] - status_vout pages 20/19
        log[5] - status_vout pages 22/21
        log[6] - status_mfr_specific pages 7/6
        log[7] - status_mfr_specific pages 9/8
        log[8] - status_mfr_specific pages 11/10
        log[9] - status_mfr_specific pages 13/12
        log[10] - status_mfr_specific pages 15/14
        log[11] - status_mfr_specific pages 00h/16
        log[12] - status_fans_1_2 pages 1/0
        log[13] - status_fans_1_2 pages 3/2
        log[14] - status_fans_1_2 pages 5/4
        """

        # list to store the parsed log data
        log_data = []

        log_entries = [(entry, data) for entry, data in enumerate(response.packet['fan_cont_mon_logs'])]

        # parse each of the 15 log entries
        # each log is of form [entry number, [log data]]
        for log in log_entries:
            # check if a log entry is empty
            if log[1].count(0xFFFF) == len(log[1]):
                # log entry is empty
                fault_summary = (log[0], None)
            else:
                idx_of_faulty_page = next(
                    (i + 3 for i, val in enumerate(log[1][3:]) if val), None)

                # determine the fault register
                fault_log_field = sd.fault_log_fields[idx_of_faulty_page]
                if 'status_mfr_specific' in fault_log_field:
                    fault_reg = sd.status_mfr_specific
                elif 'status_fans_1_2' in fault_log_field:
                    fault_reg = sd.status_fans_1_2
                elif 'status_vout' in fault_log_field:
                    fault_reg = sd.status_vout
                else:
                    err = 'unknown fault registered detected. cannot interpret' \
                          'failure'
                    raise NonVolatileLogRetrievalError(err)

                # parse page data
                upper_page = log[1][idx_of_faulty_page] >> 8
                lower_page = log[1][idx_of_faulty_page] & 0xFF

                if upper_page:
                    fault_page = int(
                        fault_log_field.split('_')[-1].split('/')[0])
                    fault_value = upper_page
                elif lower_page:
                    fault_page = int(
                        fault_log_field.split('_')[-1].split('/')[1])
                    fault_value = lower_page
                else:
                    err = 'retrieved log data is invalid'
                    raise NonVolatileLogRetrievalError(err)

                device_page = sd.fan_controller_pages[fault_page]

                # find the type of fault
                # convert to 8-bit binary number and reverse order to make python indexing match msb indexing
                fault_data_bits = '{0:08b}'.format(fault_value)[::-1]

                # look for the bit that is set
                for bit, val in enumerate(fault_data_bits):
                    if int(val):
                        fault_event = fault_reg[bit]

                # need one of these per log entry, then parse in a separate function
                fault_summary = (log[0], device_page, fault_event)

            log_data.append(fault_summary)

        return log_data

    def clear_max31785_hw_logs(self, timeout=None, retries=None):
        """
        Clear the error logs of the MAX31785 fan controller.
        The controller only stores 15 logs. Once this is full, the logs
        must be cleared to catch new faults.
        :param timeout:
        :param retries:
        :return: True if completed successfuly, otherwise, raises error
        """

        request = sd.ClearFanControllerLogsReq()
        response = self.send_packet(request, timeout=timeout, retries=retries)

        # check the status
        if response.packet['status']:
            self.logger.info('MAX31785 fan controller logs cleared successfully')
            return True
        else:
            err = 'MAX31785 fan controller logs not cleared!'
            raise SkarabFanControllerClearError(err)

    def get_ucd90120a_hw_logs(self, device, timeout=None, retries=None):
        """
        Retrieve the non-volatile logs from the UCD90120A hardware monitoring devices
        on the SKARAB. There are two devices: a voltage monitor and a current monitor.
        These must be specified using the device parameter.
        :param device: 'current' or 'voltage'
        :param timeout:
        :param retries:
        :return: log data in the form [page specific, fault type, device page,
        fault value, scaling, runtime seconds since fault]
        """

        #TODO: fix up docstrings

        # initialisation depending on device type
        if device == 'current':
            request = sd.GetCurrentLogsReq()
            packet_field = 'current_mon_logs'
            handler = self._current_handler_logging
            page_dict = sd.current_monitor_pages
        elif device == 'voltage':
            request = sd.GetVoltageLogsReq()
            packet_field = 'voltage_mon_logs'
            handler = self._voltage_handler_logging
            page_dict = sd.voltage_monitor_pages
        else:
            err = "Invalid device type specified. device must either be 'current' or 'voltage'"
            raise InvalidDeviceType(err)

        response = self.send_packet(request, timeout=timeout, retries=retries)
        """
        log entry structure:
        log[0] - Page Specific
        log[1] - Fault Type
        log[2] - Device Page
        log[3] - Fault Value (unscaled)
        log[4] - Scaling Factor
        log[5] - Runtime Seconds Since Fault - MSW
        log[6] - Runtime Seconds Since Fault - LSW
        """

        status_bits = list(
            '{:016b}'.format(response.packet['log_entry_success']))

        log_entries = list(range(16, 0, -1))

        log_data = [[log_entries.pop(), page_dict[log[2]],
                     self._check_fault_type(log[0], log[1], sensor=device),
                     handler(log[3], log[4], log[2]),
                     sd.log_entry_success_codes[status_bits.pop()],
                     self.get_fault_timestamp(log[5], log[6])]
                    if log[0] != 0xFFFF
                    else [log_entries.pop(), None,
                          sd.log_entry_success_codes[status_bits.pop()]]
                    for log in response.packet[packet_field]]

        log_data.reverse()

        return log_data

    @staticmethod
    def _check_fault_type(page_specifc, fault_type, sensor='voltage'):
        """
        Check the fault on the UCD90120A device
        :param page_specifc: page specific flag
        :param fault_type: fault code
        :return: fault_type (str)
        """

        if not page_specifc:
            return sd.non_page_specific_faults[fault_type]
        else:
            if sensor == 'current':
                if fault_type == 0 or fault_type == 1:
                    fault_type += 3

            return sd.page_specific_faults[fault_type]

    @staticmethod
    def get_fault_timestamp(seconds_msw, seconds_lsw):
        """
        Get the timestamp of the fault
        :param seconds_msw: most significant word of the seconds since fault
        :param seconds_lsw: least significant word of the seconds since fault
        :return: seconds
        """
        return struct.unpack('!I', struct.pack('!2H', seconds_msw, seconds_lsw))[0]

    def display_skarab_hw_logs(self, log_data, device_logged, units):
        """
        Display the skarab hardware logs in an easily readable format
        :param log_data: the log data retrieved from the skarab
        :param device_logged: the name of the device logged
        :param units: the unit of the measured value, where applicable
        :return:
        """

        title = 'Hardware Log Data for {skarab} - Device: {device_logged}'.format(
            skarab=self.host, device_logged=device_logged)

        print('\n{title:^94}\n'.format(title=title))

        if device_logged == 'MAX31785 Fan Controller':
            print('WARNING: Clear the MAX31785 Fan Controller logs manually after debugging to prevent losing new fault logs.\nUse the clear_max31785_hw_logs command\n')
            print(
                '{entry:^10} {device:^25} {event:^25}'.format(
                    entry='Log Entry', device='Device Page',
                    event='Fault Event'))

            for log in log_data:
                if log[1] is None:
                    print(
                        '{entry:^10} {data:-^51}'.format(
                            entry=log[0], data='No Log Data'))
                else:

                    print(
                        '{entry:^10} {device:^25} {event:^25}'.format(
                            entry=log[0], device=log[1], event=log[2]))
        else:

            print(
            '{entry:^10} {timestamp:^25} {device:^25} {event:^25} {fault_value:^15} {entry_success:^15}'.format(
                entry='Log Entry', device='Device Page', event='Fault Event',
                fault_value='Fault Value ({})'.format(units),
                entry_success='Log Entry Success?',
                timestamp='Runtime Since Fault'))

            for log in log_data:
                if log[1] is None:
                    print(
                        '{entry:^10} {data:-^93} {entry_success:^15}'.format(
                            entry=log[0], data='No Log Data',
                            entry_success=log[2]))
                else:

                    # make timestamp human readable
                    min, sec = divmod(log[5], 60)
                    hours, min = divmod(min, 60)
                    timestamp = '{hours:02d}h{minutes:02d}m{seconds:02d}s'.format(
                        hours=hours, minutes=min, seconds=sec)

                    print(
                        '{entry:^10} {timestamp:^25} {device:^25} {event:^25} {fault_value:^15} {entry_success:^15}'.format(
                            entry=log[0], device=log[1], event=log[2],
                            fault_value=log[3], entry_success=log[4],timestamp=timestamp))

    def display_voltage_monitor_logs(self):
        """
        Retrieve and display the NV logs of the voltage monitor
        :param voltage_mon_logs:
        :return: None
        """

        # retrieve log data
        log_data = self.get_ucd90120a_hw_logs('voltage')

        # display log data
        self.display_skarab_hw_logs(log_data=log_data, device_logged='UCD90120A Voltage Monitor', units='V')

    def display_current_monitor_logs(self):
        """
        Retrieve and display the NV logs of the current monitor
        :return: None
        """

        # retrieve log data
        log_data = self.get_ucd90120a_hw_logs('current')

        # display log data
        self.display_skarab_hw_logs(log_data=log_data, device_logged='UCD90120A Current Monitor', units='A')

    def display_fan_controller_logs(self):
        """
        Retrieve and display the NV logs from the MAX31785 fan controller
        :return:
        """

        # retrieve log data
        try:
            log_data = self.get_max31785_hw_logs()

            # display log data
            self.display_skarab_hw_logs(log_data=log_data,
                                        device_logged='MAX31785 Fan Controller',
                                        units=None)

        except NonVolatileLogRetrievalError:
            self.logger.error('Failed to retrieve fan controller log data')

    def _set_to_default_configuration(self, threshold_1v0_current=35,
                                      tuneable_parameters_dict=sd.default_tunable_parameters):
        """
        Configure the SKARAB board to the SARAO default configuration parameters
        :param: threshold_1v0_current
        :param: tuneable_parameters_dict
        :return: True if success, False otherwise
        """

        # check MircoBlaze version
        major, minor, patch = self.get_embedded_software_version().split('.')
        if (int(major) < 3) or (int(major) == 3 and int(minor) < 11):
            raise SkarabProcessorVersionError('MicroBlaze version is too '
                                              'old to support configuration '
                                              'of tunable parameters')

        # configure 1V0 Trip Current Threshold
        current_threshold_set = self._set_1v0_trip_current_threshold(
            trip_threshold=threshold_1v0_current)

        if not current_threshold_set:
            self.logger.warning('Setting trip threshold for '
                                '1V0 current to {} failed!'.format(threshold_1v0_current))

        # configure tunable parameters
        error_dict = {}
        self.set_hmc_reconfig_max_retries(tuneable_parameters_dict['hmc_reconfig_max_retries'])
        self.set_hmc_reconfig_timeout(tuneable_parameters_dict['hmc_reconfig_timeout'])
        self.set_link_mon_timeout(tuneable_parameters_dict['link_mon_timeout'])
        self.set_dhcp_init_time(tuneable_parameters_dict['dhcp_init_time'])
        self.set_dhcp_retry_rate(tuneable_parameters_dict['dhcp_retry_rate'])

        # check if the tunable parameters were set correctly
        set_parameters = self.get_tunable_parameters()
        for parameter, value in set_parameters.items():
            if value != tuneable_parameters_dict[parameter]:
                error_dict[parameter] = value

        if error_dict:
            #  some parameters were not set correctly
            self.logger.warning("Some parameters were not set correctly. "
                  "These are the errorneous parameters and the "
                  "values they are set to:{}".format(error_dict))
        else:
            self.logger.debug("All tunable parameters successfully configured!")

        # enable non-volatile fan controller logging
        # TODO: implement function

        # configure fan control logic
        # TODO: add when available

        if error_dict or not current_threshold_set:
            return False
        else:
            return True

    def _set_1v0_trip_current_threshold(self, trip_threshold=35):
        """
        Reconfigure the 1V0 current trip threshold
        :param trip_threshold: The desired 1V0 current trip threshold (Amps)
        :return: True if success, False otherwise
        """

        # check that the desired threshold is within the acceptable range

        if trip_threshold < 20:
            self.logger.warning("Desired current limit too low! "
                                "Threshold must be >= 20A")
            return False

        # work out the [LSB, MSB] to set the threshold

        # calculation of the LSB and MSB for trip value
        # current measured as voltage divided by sense resistor

        # voltage over the 1V0 Rsense is on page 7 of i2c device 0x47 (UCD90120A)
        # this voltage can be read with READ_VOUT cmd (0x8b) and is in the LINEAR16 format.
        # from datasheet, UCD90xxx Sequencer and System Health Controller PMBus Command Reference:
        #   pg. 13 par. 2.1 =>  Voltage = V x 2^x
        #                       V is 16bit unsigned int (from READ_VOUT)
        #                       x is signed 5-bit two's complement exponent (from VOUT_MODE)

        # therefore :
        # current = value x 2^(x) x 1/R(sense)

        # for page 7:
        # VOUT_MODE = 17 => 17 -32 = -15 two's compl 5-bit signed integer
        # R(sense) = 100*0.00025 = 0.025

        # => current (A) = value x 2^(-15) * 1/0.025
        # => value = current / (2^(-15) * 1/0.025)
        # e.g. 32A
        # => value = 26,214.4 ~ 26,214 = 0x6666
        # e.g. 35A
        # => value = 28,672 = 0x7000

        value = int(trip_threshold / (2**(-15) * 1.0/0.025))

        limit_msb = value >> 8
        limit_lsb = value & 0xff

        assert limit_msb >= 0x40, "Desired current limit too low! " \
                                  "Threshold must be >= 20A"

        # set i2c switch to select current mon
        self.write_i2c(sd.MB_I2C_BUS_ID, sd.PCA9546_I2C_DEVICE_ADDRESS,
                       sd.MONITOR_SWITCH_SELECT)
        time.sleep(0.5)

        # set current monitor page to 7, which corresponds to 1V0 current
        self.write_i2c(sd.MB_I2C_BUS_ID, sd.UCD90120A_CMON_I2C_DEVICE_ADDRESS,
                       sd.PAGE_CMD, sd.P1V0_CURRENT_MON_PAGE)
        time.sleep(1)

        # now set the current fault limit with the VOUT_OV_FAULT_LIMIT cmd
        self.write_i2c(sd.MB_I2C_BUS_ID, sd.UCD90120A_CMON_I2C_DEVICE_ADDRESS,
                       sd.VOUT_OV_FAULT_LIMIT_CMD, limit_lsb, limit_msb)
        time.sleep(1)

        # check the new stored value

        stored_threshold = self.pmbus_read_i2c(sd.MB_I2C_BUS_ID,
                                               sd.UCD90120A_CMON_I2C_DEVICE_ADDRESS,
                                               sd.VOUT_OV_FAULT_LIMIT_CMD, 2)

        time.sleep(1)

        # check the set value
        scale_factor = self.pmbus_read_i2c(sd.MB_I2C_BUS_ID,
                                               sd.UCD90120A_CMON_I2C_DEVICE_ADDRESS,
                                               sd.VOUT_MODE_CMD, 1)

        set_value = (stored_threshold[1] << 8) + stored_threshold[0]
        set_current = set_value * pow(2, scale_factor[0]-32) * 40

        if set_current == trip_threshold:
            # new current trip threshold set successfully
            self.logger.debug("New 1v0 current threshold "
                              "successfully set to {}".format(set_current))

            # now store the new limit as the default value
            # store as default value into NV data memory / FLASH
            self.write_i2c(sd.MB_I2C_BUS_ID, sd.UCD90120A_CMON_I2C_DEVICE_ADDRESS,
                           sd.STORE_DEFAULT_ALL_CMD)
            time.sleep(2)

            stored = False
            count = 0
            while not stored and count < 10:
                status = self.pmbus_read_i2c(sd.MB_I2C_BUS_ID,
                                             sd.UCD90120A_CMON_I2C_DEVICE_ADDRESS,
                                             sd.MFR_STATUS,
                                             5)  # read mfr_status
                if (status[3] & 0x6) == 2:  # status is 32-bit word
                    stored = True
                time.sleep(1)
                count = count + 1

            if not stored:
                # new default trip threshold not stored
                self.logger.warning("New default trip threshold not stored!")
                return False

            self.logger.debug("New default trip threshold successfully stored!")
            return True

        else:
            # new current trip threshold not set correctly
            self.logger.error("New 1v0 current threshold not set correctly. "
                              "Desired threshold = {} but set "
                              "threshold = {}".format(trip_threshold,
                                                      set_current))
            return False

    def _check_1v0_current_limit(self):
        """
        Check the set 1V0 current trip limit
        :return: the set current limit for the 1V0 rail
        """
        # set i2c switch to select current mon
        self.write_i2c(sd.MB_I2C_BUS_ID, sd.PCA9546_I2C_DEVICE_ADDRESS,
                       sd.MONITOR_SWITCH_SELECT)
        time.sleep(0.5)

        # set current monitor page to 7, which corresponds to 1V0 current
        self.write_i2c(sd.MB_I2C_BUS_ID, sd.UCD90120A_CMON_I2C_DEVICE_ADDRESS,
                       sd.PAGE_CMD, sd.P1V0_CURRENT_MON_PAGE)
        time.sleep(1)

        stored_threshold = self.pmbus_read_i2c(sd.MB_I2C_BUS_ID,
                                               sd.UCD90120A_CMON_I2C_DEVICE_ADDRESS,
                                               sd.VOUT_OV_FAULT_LIMIT_CMD, 2)

        time.sleep(1)

        # check the set value
        scale_factor = self.pmbus_read_i2c(sd.MB_I2C_BUS_ID,
                                           sd.UCD90120A_CMON_I2C_DEVICE_ADDRESS,
                                           sd.VOUT_MODE_CMD, 1)

        set_value = (stored_threshold[1] << 8) + stored_threshold[0]
        set_current = set_value * pow(2, scale_factor[0] - 32) * 40

        self.logger.info('Configured 1v0 current for '
                         'host {} is {}'.format(self.host, set_current))
        return set_current

    def _enable_fan_controller_nv_logs(self):
        """
        Enable logging of fan controller faults to non-volatile memory
        :return: True if success, False otherwise
        """

        # set i2c switch to select the fan controller
        self.write_i2c(sd.MB_I2C_BUS_ID, sd.PCA9546_I2C_DEVICE_ADDRESS,
                       sd.FAN_CONT_SWITCH_SELECT)

        # clear the fault log
        # read the control register value
        # tmp[0] = LSB, tmp[1] = MSB
        tmp = self.pmbus_read_i2c(sd.MB_I2C_BUS_ID,
                                  sd.MAX31785_I2C_DEVICE_ADDRESS,
                                  sd.MFR_MODE_CMD, 2)

        # toggle bit to clear fault log
        tmp[1] = tmp[1] | 0x40

        # write new value to the control register
        self.write_i2c(sd.MB_I2C_BUS_ID, sd.MAX31785_I2C_DEVICE_ADDRESS,
                       sd.MFR_MODE_CMD, tmp[0], tmp[1])

        time.sleep(0.5)
        # restore the default fan controller configuration
        self.write_i2c(sd.MB_I2C_BUS_ID, sd.MAX31785_I2C_DEVICE_ADDRESS,
                       sd.RESTORE_DEFAULT_ALL_CMD)

        # enable non-volatile logging for all relevant pages (page 0 - page 22)
        for page in range(0, 23):
            time.sleep(0.5)
            # select page of the fan controller
            self.write_i2c(sd.MB_I2C_BUS_ID, sd.MAX31785_I2C_DEVICE_ADDRESS,
                           sd.PAGE_CMD, page)

            time.sleep(0.5)

            # get the current page configuration
            tmp = self.pmbus_read_i2c(sd.MB_I2C_BUS_ID,
                                      sd.MAX31785_I2C_DEVICE_ADDRESS,
                                      sd.MFR_FAULT_RESPONSE_CMD, 1)

            time.sleep(0.5)

            # toggle the bit to enable logging to non-volatile memory
            tmp[0] = tmp[0] | 0x80

            # write new value to the page control register
            self.write_i2c(sd.MB_I2C_BUS_ID, sd.MAX31785_I2C_DEVICE_ADDRESS,
                           sd.MFR_FAULT_RESPONSE_CMD, tmp[0])

            time.sleep(0.5)

        self.logger.info('Enabled Fan Controller Fault logging for'
                         'host {}'.format(self.host))

        # store the new default configuration incl. the non-volatile fault logging
        self.write_i2c(sd.MB_I2C_BUS_ID, sd.MAX31785_I2C_DEVICE_ADDRESS,
                       sd.STORE_DEFAULT_ALL_CMD)

        # TODO: implement error checking and error handling
        # check here:
            # read the value back
            # check the msb
            # if it's set, return true, else return false

        return True
# end
