import logging

from .transport import Transport
from .network import IpAddress

LOGGER = logging.getLogger(__name__)


class NamedFifo(object):
    def __init__(self, maxlen=None):
        self.names = []
        self.values = []
        self.maxlen = maxlen

    def push(self, name, value):
        self.names.append(name)
        self.values.append(value)
        if self.maxlen is not None:
            if len(self) > self.maxlen:
                self.names.pop(0)
                self.values.pop(0)

    def pop(self, name=None):
        pop = self.names.index(name) if name is not None else 0
        self.names.pop(pop)
        rv = self.values.pop(pop)
        return rv

    def __len__(self):
        return len(self.names)



class DummyTransport(Transport):
    """
    A dummy transport for testing
    """
    def __init__(self, **kwargs):
        """
        Make a Dummy Transport

        :param host: IP Address should be 127.0.0.1 for a Dummy
        """
        Transport.__init__(self, **kwargs)
        self._devices = NamedFifo(100)
        self._devices_wishbone = NamedFifo(100)

    def connect(self, timeout=None):
        """

        :param timeout:
        """
        return

    def is_running(self):
        """
        Is the FPGA programmed and running?

        :return: True or False
        """
        return True

    def is_connected(self):
        """

        """
        return True

    def test_connection(self):
        """
        Write to and read from the scratchpad to test the connection to the FPGA
        """
        return self.is_connected()

    def ping(self):
        """
        Use the 'watchdog' request to ping the FPGA host.

        :return: True or False
        """
        return True

    def disconnect(self):
        """

        :return:
        """
        pass

    def read(self, device_name, size, offset=0):
        """

        :param device_name:
        :param size:
        :param offset:
        """
        try:
            return self._devices.pop(device_name)
        except ValueError:
            pass
        return '\x00' * size

    def blindwrite(self, device_name, data, offset=0):
        """

        :param device_name:
        :param data:
        :param offset:
        """
        self._devices.push(device_name, data)
        return

    def listdev(self):
        """
        Get a list of the memory bus items in this design.

        :return: a list of memory devices
        """
        return self.memory_devices.keys()

    def deprogram(self):
        """
        Deprogram the FPGA connected by this transport
        """
        raise NotImplementedError

    def set_igmp_version(self, version):
        """
        :param version:
        """
        pass

    def upload_to_ram_and_program(self, filename, port=-1, timeout=10,
                                  wait_complete=True, skip_verification=False):
        """
        Upload an FPG file to RAM and then program the FPGA.

        :param filename: the file to upload
        :param port: the port to use on the rx end, -1 means a random port
        :param timeout: how long to wait, seconds
        :param wait_complete: wait for the transaction to complete, return
            after upload if False
        :param skip_verification: don't verify the image after uploading it
        """
        self.bitstream = filename
        return True

    def upload_to_flash(self, binary_file, port=-1, force_upload=False,
                        timeout=30, wait_complete=True):
        """
        Upload the provided binary file to the flash filesystem.

        :param binary_file: filename of the binary file to upload
        :param port: host-side port, -1 means a random port will be used
        :param force_upload: upload the binary even if it already exists
            on the host
        :param timeout: upload timeout, in seconds
        :param wait_complete: wait for the upload to complete, or just
            kick it off
        """
        return True

    def get_system_information_from_transport(self):
        """

        """
        return self.bitstream, None

    def post_get_system_information(self):
        """
        Cleanup run after get_system_information
        """
        pass

    def read_wishbone(self, wb_address):
        """
        Used to perform low level wishbone read from a Wishbone slave.

        :param wb_address: address of the wishbone slave to read from
        :return: Read Data or None
        """
        try:
            return self._devices_wishbone.pop(wb_address)
        except ValueError:
            pass
        return 0

    def write_wishbone(self, wb_address, data):
        """
        Used to perform low level wishbone write to a wishbone slave. Gives
        low level direct access to wishbone bus.

        :param wb_address: address of the wishbone slave to write to
        :param data: data to write
        :return: response object
        """
        self._devices_wishbone.push(wb_address, data)
        return None

    @staticmethod
    def multicast_receive(gbename, ip, mask):
        """

        :param gbename:
        :param ip:
        :param mask:
        """
        resp_ip = IpAddress(ip)
        resp_mask = IpAddress(mask)
        LOGGER.debug('%s: multicast configured: addr(%s) mask(%s)' % (
            gbename, resp_ip.ip_str, resp_mask.ip_str))

# end
