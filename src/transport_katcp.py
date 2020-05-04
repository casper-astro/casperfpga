import logging
import katcp
import time
import os
import threading
import queue
import random
import socket
import struct
import contextlib

from .transport import Transport
from .utils import create_meta_dictionary, get_hostname, get_kwarg, socket_closer

LOGGER = logging.getLogger(__name__)

# monkey-patch the maximum katcp message size
if hasattr(katcp.CallbackClient, 'MAX_MSG_SIZE'):
    setattr(katcp.CallbackClient, 'MAX_MSG_SIZE',
            katcp.CallbackClient.MAX_MSG_SIZE * 10)

if hasattr(katcp.CallbackClient, 'MAX_WRITE_BUFFER_SIZE'):
    setattr(katcp.CallbackClient, 'MAX_WRITE_BUFFER_SIZE',
            katcp.CallbackClient.MAX_WRITE_BUFFER_SIZE * 10)


class KatcpConnectionError(Exception):
    pass


class KatcpRequestError(RuntimeError):
    """An error occurred processing a KATCP request."""
    pass


class KatcpRequestInvalid(RuntimeError):
    """An invalid KATCP request was made."""
    pass

class KatcpRequestFail(RuntimeError):
    """A valid KATCP request failed."""
    pass

# Moved to inside the KatcpTransport class to make use of the logger passed through from casperfpga.py
# def sendfile(filename, targethost, port, result_queue, timeout=2):
#     """
#     Send a file to a host using sockets. Place the result of the
#     action in a Queue.Queue
#     :param filename: the file to send
#     :param targethost: the host to which it must be sent
#     :param port: the port the host should open
#     :param result_queue: the result of the upload, nothing '' indicates success
#     :param timeout:
#     :return:
#     """
#     with contextlib.closing(socket.socket()) as upload_socket:
#         stime = time.time()
#         connected = False
#         while (not connected) and (time.time() - stime < timeout):
#             try:
#                 upload_socket.connect((targethost, port))
#                 connected = True
#             except socket.error:
#                 time.sleep(0.1)
#         if not connected:
#             result_queue.put('Could not connect to upload port.')
#         try:
#             upload_socket.send(open(filename).read())
#             result_queue.put('')
#         except Exception as e:
#             result_queue.put('Could not send file to upload port(%i): '
#                              '%s' % (port, e.message))
#         finally:
#             LOGGER.info('%s: upload thread complete at %.3f' %
#                         (targethost, time.time()))


class KatcpTransport(Transport, katcp.CallbackClient):
    """
    A katcp transport client for a casperfpga object
    """
    def __init__(self, **kwargs):
        """

        :param host:
        :param port:
        :param timeout:
        :param connect:
        """
        port = get_kwarg('port', kwargs, 7147)
        timeout = get_kwarg('timeout', kwargs, 10)
        Transport.__init__(self, **kwargs)

        # Create instance of self.logger
        # try:
        #     self.logger = kwargs['logger']
        # except KeyError:
        #     self.logger = logging.getLogger(__name__)

        try:
            self.parent = kwargs['parent_fpga']
            self.logger = self.parent.logger
        except KeyError:
            errmsg = 'parent_fpga argument not supplied when creating katcp device'
            raise RuntimeError(errmsg)

        new_connection_msg = '*** NEW CONNECTION MADE TO {} ***'.format(self.host)
        self.logger.info(new_connection_msg)

        katcp.CallbackClient.__init__(
            self, self.host, port, tb_limit=20,
            timeout=timeout, logger=self.logger, auto_reconnect=True)
        self.system_info = {}
        self.unhandled_inform_handler = None
        self._timeout = timeout
        self.connect()
        self.logger.info('%s: port(%s) created and connected.' % (self.host, port))

    @staticmethod
    def test_host_type(host_ip, timeout=5):
        """
        Is this host_ip assigned to a Katcp board?

        :param host_ip: as a String
        :param timeout: as an Integer
        """
        try:
            
            llevel = LOGGER.getEffectiveLevel()
            tlogger = logging.getLogger('tornado')
            tlevel = LOGGER.getEffectiveLevel()
            LOGGER.setLevel(logging.CRITICAL)
            tlogger.setLevel(logging.CRITICAL)
            board = katcp.CallbackClient(host=host_ip, port=7147, timeout=timeout, auto_reconnect=False)
            board.setDaemon(True)
            board.start()
            connected = board.wait_connected(timeout)
            board.stop()
            LOGGER.setLevel(llevel)
            tlogger.setLevel(tlevel)
            if not connected:
                return False
            else:
                return True

        except AttributeError:
                raise RuntimeError("Please ensure that katcp-python >=v0.6.3 is being used")

        except Exception:
            LOGGER.setLevel(llevel)
            LOGGER.debug('KATCP-unable to connect to {}'.format(host_ip))
            return False

    def sendfile(self, filename, targethost, port, result_queue, timeout=2):
        """
        Send a file to a host using sockets. Place the result of the
        action in a Queue.Queue

        :param filename: the file to send
        :param targethost: the host to which it must be sent
        :param port: the port the host should open
        :param result_queue: the result of the upload, nothing '' indicates success
        :param timeout:
        """
        with contextlib.closing(socket.socket()) as upload_socket:
            stime = time.time()
            connected = False
            while (not connected) and (time.time() - stime < timeout):
                try:
                    upload_socket.connect((targethost, port))
                    connected = True
                except socket.error:
                    time.sleep(0.1)
            if not connected:
                result_queue.put('Could not connect to upload port.')
            try:
                upload_socket.send(open(filename).read())
                result_queue.put('')
            except Exception as e:
                result_queue.put('Could not send file to upload port({}): {}'.format(
                                 port, e))
            socket_closer("sendfile to {}:{}".format(targethost, port), upload_socket)
            self.logger.info('%s: upload thread complete at %.3f' %
                                (targethost, time.time()))

    def is_connected(self):
        return katcp.CallbackClient.is_connected(self)

    def connect(self, timeout=None):
        """
        Establish a connection to the KATCP server on the device.

        :param timeout: How many seconds should we wait? Use instance default
                        if None.
        """
        if timeout is None:
            timeout = self._timeout
        if not self.is_connected():
            # Implement backward / forwards compatibility for change in
            # daemonization APIs in upstream katcp package.
            try:
                # new style
                self.setDaemon(True)
                self.start()
            except AttributeError:
                # old style katcp-python
                # self.start(daemon=True)
                raise RuntimeError("Please ensure that katcp-python >=v0.6.3 is being used")
            connected = self.wait_connected(timeout)
            if not connected:
                err_msg = 'Connection to {} not established within {}s'.format(
                    self.host, timeout)
                self.logger.error(err_msg)
                raise RuntimeError(err_msg)

        # check that an actual katcp command gets through
        got_ping = False
        _stime = time.time()
        while time.time() < _stime + timeout:
            if self.ping():
                got_ping = True
                break
        if not got_ping:
            err_msg = 'Could not connect to KATCP server %s' % self.host
            self.logger.error(err_msg)
            raise RuntimeError(err_msg)

        # set a higher write buffer size than standard
        try:
            if self._stream.max_write_buffer_size <= 262144:
                self._stream.max_buffer_size *= 2
                self._stream.max_write_buffer_size *= 2
        except AttributeError:
            self.logger.warn('%s: no ._stream instance found.' % self.host)

        self.logger.info('%s: connection established' % self.host)

    def disconnect(self):
        """
        Disconnect from the device server.
        :return:
        """
        self.join(timeout=self._timeout)
        self.logger.info('%s: disconnected' % self.host)

    def katcprequest(self, name, request_timeout=-1.0, require_ok=True,
                     request_args=()):
        """
        Make a blocking request to the KATCP server and check the result.
        Raise an error if the reply indicates a request failure.

        :param name: request message to send.
        :param request_timeout: number of seconds after which the request
            must time out
        :param require_ok: will we raise an exception on a response != ok
        :param request_args: request arguments.
        :return: tuple of reply and informs
        """
        # TODO raise sensible errors
        if request_timeout == -1:
            request_timeout = self._timeout
        request = katcp.Message.request(name, *request_args)
        reply, informs = self.blocking_request(request, timeout=request_timeout)
        if (reply.arguments[0] != katcp.Message.OK) and require_ok:
            if reply.arguments[0] == katcp.Message.FAIL:
                raise KatcpRequestFail(
                    'Request %s on host %s failed.\n\t'
                    'Request: %s\n\tReply: %s' %
                    (request.name, self.host, request, reply))
            elif reply.arguments[0] == katcp.Message.INVALID:
                raise KatcpRequestInvalid(
                    'Invalid katcp request %s on host %s.\n\t'
                    'Request: %s\n\tReply: %s' %
                    (request.name, self.host, request, reply))
            else:
                raise KatcpRequestError(
                    'Unknown error processing request %s on host '
                    '%s.\n\tRequest: %s\n\tReply: %s' %
                    (request.name, self.host, request, reply))
        return reply, informs

    def listdev(self, getsize=False, getaddress=False):
        """
        Get a list of the memory bus items in this design.

        :return: a list of memory devices
        """
        if getsize:
            _, informs = self.katcprequest(name='listdev',
                                           request_timeout=self._timeout,
                                           request_args=('size',))
            return [(i.arguments[0].decode(), i.arguments[1].decode()) for i in informs]
        elif getaddress:
            _, informs = self.katcprequest(name='listdev',
                                           request_timeout=self._timeout,
                                           request_args=('detail',))
            return [(i.arguments[0].decode(), i.arguments[1].decode()) for i in informs]
        else:
            _, informs = self.katcprequest(name='listdev',
                                           request_timeout=self._timeout)
            return [i.arguments[0].decode() for i in informs]

    def listbof(self):
        """
        :return: a list of binary files stored on the host device.
        """
        _, informs = self.katcprequest(name='listbof',
                                       request_timeout=self._timeout)
        return [i.arguments[0].decode() for i in informs]

    def status(self):
        """
        :return: FPGA status
        """
        reply, _ = self.katcprequest(name='status',
                                     request_timeout=self._timeout)
        return reply.arguments[1].decode()

    def ping(self):
        """
        Use the 'watchdog' request to ping the FPGA host.

        :return: True or False
        """
        reply, _ = self.katcprequest(name='watchdog',
                                     request_timeout=self._timeout)
        if reply.arguments[0] == katcp.Message.OK:
            self.logger.info('%s: katcp ping okay' % self.host)
            return True
        else:
            self.logger.error('%s: katcp ping fail' % self.host)
            return False

    def is_running(self):
        """
        Is the FPGA programmed and running?

        :return: True or False
        """
        reply, _ = self.katcprequest(
            name='fpgastatus', request_timeout=self._timeout, require_ok=False)
        return reply.arguments[0] == katcp.Message.OK

    def test_connection(self):
        """
        Write to and read from the scratchpad to test the connection to the FPGA
        """
        for val in [0xa5a5a5, 0x000000]:
            data = struct.pack('>i' if val < 0 else '>I', val)
            self.blindwrite('sys_scratchpad', data, 0)
            val2 = self.read('sys_scratchpad', 4, 0)
            val2 = struct.unpack('>L', val2[0:4])[0]
            if val != val2:
                raise RuntimeError('%s: cannot write scratchpad? %i != %i' %
                                   (self.host, val, val2))
        return True

    def read(self, device_name, size, offset=0):
        """
        Read size-bytes of binary data with carriage-return escape-sequenced.
       
        :param device_name: name of memory device from which to read
        :param size: how many bytes to read
        :param offset: start at this offset
        :return: binary data string
        """
        reply, _ = self.katcprequest(
            name='read', request_timeout=self._timeout, require_ok=True,
            request_args=(device_name, str(offset), str(size)))
        return reply.arguments[1].decode()

    def wordread(self, device_name, size=1, word_offset=0, bit_offset=0):
        """

        :param device_name: name of memory device from which to read
        :param word_count: how many words to read
        :param word_offset: start at this word offset
        :param bit_offset: start at this bit offset
        :return: value in hexadecimal
        """

        reply, _ = self.katcprequest(
            name='wordread', request_timeout=self._timeout, require_ok=True,
            request_args=(device_name, str(word_offset)+':'+str(bit_offset),
                          str(size))
        )
        return reply.arguments[1].decode()

    def blindwrite(self, device_name, data, offset=0):
        """
        Unchecked data write.
        :param device_name: the memory device to which to write
        :param data: the byte string to write
        :param offset: the offset, in bytes, at which to write
        :return: <nothing>
        """
        assert(type(data) == str), 'You need to supply binary packed ' \
                                   'string data!'
        assert(len(data) % 4) == 0, 'You must write 32-bit-bounded words!'
        assert((offset % 4) == 0), 'You must write 32-bit-bounded words!'
        self.katcprequest(name='write', request_timeout=self._timeout,
                          require_ok=True,
                          request_args=(device_name, str(offset), data))

    def bulkread(self, device_name, size, offset=0):
        """
        Read size-bytes of binary data with carriage-return escape-sequenced.
        Uses much faster bulkread katcp command which returns data in pages
        using informs rather than one read reply, which has significant
        buffering overhead on the ROACH.

        :param device_name: name of the memory device from which to read
        :param size: how many bytes to read
        :param offset: the offset at which to read
        :return: binary data string
        """
        _, informs = self.katcprequest(
            name='bulkread', request_timeout=self._timeout, require_ok=True,
            request_args=(device_name, str(offset), str(size)))
        return ''.join([i.arguments[0] for i in informs])

    def program(self, filename=None):
        """
        Program the FPGA with the specified binary file.

        :param filename: name of file to program, can vary depending on
            the formats supported by the device. e.g. fpg, bof, bin
        """
        # raise DeprecationWarning('This does not seem to be used anymore.'
        #                          'Use upload_to_ram_and_program')
        if 'program_filename' in self.system_info.keys():
            if filename is None:
                filename = self.system_info['program_filename']
            elif filename != self.system_info['program_filename']:
                self.logger.error('%s: programming filename %s, configured '
                             'programming filename %s' %
                             (self.host, filename,
                              self.system_info['program_filename']))
                # This doesn't seem as though it should really be an error...
        if filename is None:
            self.logger.error('%s: cannot program with no filename given. '
                         'Exiting.' % self.host)
            raise RuntimeError('Cannot program with no filename given. '
                               'Exiting.')
        unhandled_informs = []
        # set the unhandled informs callback
        self.unhandled_inform_handler = \
            lambda msg: unhandled_informs.append(msg)
        reply, _ = self.katcprequest(name='progdev', request_timeout=10,
                                     request_args=(filename, ))
        self.unhandled_inform_handler = None
        if reply.arguments[0] == katcp.Message.OK:
            complete_okay = False
            for inf in unhandled_informs:
                if (inf.name == 'fpga') and (inf.arguments[0] == 'ready'):
                    complete_okay = True
            if not complete_okay: # Modify to do an extra check
                reply, _ = self.katcprequest(name='status', request_timeout=1)
                # Not sure whether 1 second is a good timeout here
                if reply.arguments[0] == katcp.Message.OK:
                    complete_okay = True
                else:
                    self.logger.error('%s: programming %s failed.' %
                                 (self.host, filename))
                    for inf in unhandled_informs:
                        self.logger.debug(inf)
                    raise RuntimeError('%s: programming %s failed.' %
                                       (self.host, filename))
            self.system_info['last_programmed'] = filename
        else:
            self.logger.error('%s: progdev request %s failed.' %
                         (self.host, filename))
            raise RuntimeError('%s: progdev request %s failed.' %
                               (self.host, filename))
        if filename[-3:] == 'fpg':
            #TODO: fix this
            # self.get_system_information()
            pass
        else:
            self.logger.info('%s: %s is not an fpg file, could not parse '
                        'system information.' % (self.host, filename))
        self.logger.info('%s: programmed %s okay.' % (self.host, filename))
        self.prog_info['last_programmed'] = filename
        self.prog_info['last_uploaded'] = ''

    def deprogram(self):
        """
        Deprogram the FPGA.

        Unsubscribes all active tap devices from any multicast groups to
        avoid confusing switch IGMP snoop tables.
        """
        try:
            self._unsubscribe_all_taps()
            # Sleep a little to give roach kernel time to send IGMP multicast
            # unsubscibe message(s) before deprogramming which would kill the
            # tap devices
            time.sleep(0.05)
            reply, _ = self.katcprequest(name='progdev', require_ok=True)
            self.logger.info('%s: deprogrammed okay' % self.host)
            self.prog_info['last_programmed'] = ''
        except KatcpRequestError as exc:
            self.logger.exception('{}: could not deprogram FPGA, katcp request '
                             'failed:'.format(self.host))
            raise RuntimeError('{}: could not deprogram '
                               'FPGA - {}'.format(self.host, exc))

    def _unsubscribe_all_taps(self):
        """
        Remove all multicast subscriptions before deprogramming the ROACH
        """
        reply, informs = self.katcprequest(name='tap-info', require_ok=True)
        taps = [inform.arguments[0] for inform in informs]
        for tap in taps:
            reply, _ = self.katcprequest(name='tap-multicast-remove',
                                         request_args=(tap,))
            if not reply.reply_ok():
                self.logger.warn('{}: could not unsubscribe tap {} from multicast '
                            'groups on FPGA'.format(self.host, tap))

    def set_igmp_version(self, version):
        """
        Sets version of IGMP multicast protocol to use

        :param version: IGMP protocol version, 0 for kernel default, 1, 2 or 3

        Note: won't work if config keep file is present since the
        needed ?igmp-version request won't exist on the KATCP interface.
        """
        reply, _ = self.katcprequest(name='igmp-version',
                                     request_args=(version, ),
                                     require_ok=True)

    def upload_to_ram_and_program(self, filename, port=-1, timeout=10,
                                  wait_complete=True,
                                  skip_verification=False):
        """
        Upload an FPG file to RAM and then program the FPGA.

        :param filename: the file to upload
        :param port: the port to use on the rx end, -1 means a random port
        :param timeout: how long to wait, seconds
        :param wait_complete: wait for the transaction to complete, return
            after upload if False
        :param skip_verification: do not verify the uploaded file before reboot
        """
        self.logger.info('%s: uploading %s, programming when done' % (
            self.host, filename))
        # does the file that is to be uploaded exist on the local filesystem?
        os.path.getsize(filename)

        # function to make the request to the KATCP server
        def makerequest(result_queue):
            try:
                result = self.katcprequest(
                    name='progremote', request_timeout=timeout,
                    require_ok=True, request_args=(port, ))
                if result[0].arguments[0] == katcp.Message.OK:
                    result_queue.put('')
                else:
                    result_queue.put('Request to client %s returned, but not '
                                     'Message.OK.' % self.host)
            except:
                result_queue.put('Request to client %s failed.' % self.host)
            finally:
                self.logger.debug('progremote thread done')

        if port == -1:
            port = random.randint(2000, 2500)
        # start the request thread and join
        request_queue = queue.Queue()
        request_thread = threading.Thread(target=makerequest,
                                          args=(request_queue, ))
        old_timeout = self._timeout
        self._timeout = timeout
        request_thread.start()
        request_thread.join()
        request_result = request_queue.get()
        if request_result != '':
            raise RuntimeError('progremote request(%s) on host %s failed' %
                               (request_result, self.host))
        # start the upload thread and join
        upload_queue = queue.Queue()
        unhandled_informs_queue = queue.Queue()
        upload_thread = threading.Thread(target=self.sendfile, args=(
            filename, self.host, port, upload_queue, ))
        self.unhandled_inform_handler = \
            lambda msg: unhandled_informs_queue.put(msg)
        upload_thread.start()
        if not wait_complete:
            self.unhandled_inform_handler = None
            self._timeout = old_timeout
            return True
        upload_thread.join()  # wait for the file upload to complete
        upload_result = upload_queue.get()
        if upload_result != '':
            raise RuntimeError('upload(%s)' % upload_result)
        # wait for the '#fpga ready' inform
        done = False
        while not done:
            try:
                inf = unhandled_informs_queue.get(block=True, timeout=timeout)
            except queue.Empty:
                self.logger.error('%s: no programming informs yet. Odd?' % self.host)
                raise RuntimeError('%s: no programming informs yet. '
                                   'Odd?' % self.host)
            if (inf.name == 'fpga') and (inf.arguments[0] == 'ready'):
                done = True
        self.logger.info('%s: programming done.' % self.host)
        self.unhandled_inform_handler = None
        self._timeout = old_timeout
        self.prog_info['last_programmed'] = filename
        self.prog_info['last_uploaded'] = filename
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
        # does the bof file exist?
        os.path.getsize(binary_file)

        # is it on the FPGA already?
        filename = binary_file.split('/')[-1]
        if not force_upload:
            bofs = self.listbof()
            if bofs.count(filename) == 1:
                return

        # function to make the request to the KATCP server
        def makerequest(result_queue):
            try:
                result = self.katcprequest(name='saveremote',
                                           request_timeout=timeout,
                                           require_ok=True,
                                           request_args=(port, filename, ))
                if result[0].arguments[0] == katcp.Message.OK:
                    result_queue.put('')
                else:
                    result_queue.put('Request to client returned, but not '
                                     'Message.OK.')
            except:
                result_queue.put('Request to client failed.')

        if port == -1:
            port = random.randint(2000, 2500)

        # request thread
        request_queue = queue.Queue()
        request_thread = threading.Thread(target=makerequest,
                                          args=(request_queue, ))
        # upload thread
        upload_queue = queue.Queue()
        upload_thread = threading.Thread(target=self.sendfile, args=(
            binary_file, self.host, port, upload_queue, ))
        # start the threads and join
        old_timeout = self._timeout
        self._timeout = timeout
        request_thread.start()
        upload_thread.start()
        if not wait_complete:
            self._timeout = old_timeout
            return
        request_thread.join()
        self._timeout = old_timeout
        request_result = request_queue.get()
        upload_result = upload_queue.get()
        if (request_result != '') or (upload_result != ''):
            raise Exception('Error: request(%s), upload(%s)' %
                            (request_result, upload_result))
        self.prog_info['last_uploaded'] = filename
        return

    def _delete_bof(self, filename):
        """
        Delete a binary file from the device.

        :param filename: the file to delete
        """
        if filename == 'all':
            bofs = self.listbof()
        else:
            bofs = [filename]
        for bof in bofs:
            result = self.katcprequest(
                name='delbof', request_timeout=self._timeout,
                require_ok=True, request_args=(bof, ))
            if result[0].arguments[0] != katcp.Message.OK:
                raise RuntimeError('Failed to delete bof file %s' % bof)

    def tap_arp_reload(self):
        """
        Have the tap driver reload its ARP table right now.
        """
        reply, _ = self.katcprequest(
            name='tap-arp-reload', request_timeout=-1, require_ok=True)
        if reply.arguments[0] != 'ok':
            raise RuntimeError('%s: failure requesting ARP reload.' % self.host)

    def __str__(self):
        return 'KatcpTransport(%s):%i - %s' % \
               (self.host, self._bindaddr[1],
                'connected' if self.is_connected() else 'disconnected')

    @staticmethod
    def _process_git_info(metalist):
        """
        Git information in the FPG must be processed.

        :param metalist:
        """
        got_git = {}
        time_pref = str(int(time.time())).replace('.', '_') + '_'
        for ctr, parms in enumerate(metalist):
            name, tag, param, value = parms
            if name == '77777_git':
                assert tag == 'rcs'
                newname = name + '_' + time_pref + param
                if newname not in got_git:
                    got_git[newname] = (newname, tag, 'file-name', param)
                param = value[0]
                value = value[1:]
                metalist[ctr] = (newname, tag, param, value)
        metalist.extend(got_git.values())

    def _read_design_info_from_host(self, device=None):
        """
        Katcp request for extra system information embedded in the bitstream.
        
        :param device: can specify a device name if you don't want everything
        :return: a dictionary of metadata
        """
        self.logger.debug('%s: reading designinfo' % self.host)
        if device is None:
            reply, informs = self.katcprequest(
                name='meta', request_timeout=10.0, require_ok=True)
        else:
            reply, informs = self.katcprequest(
                name='meta', request_timeout=10.0, require_ok=True,
                request_args=(device, ))
        if reply.arguments[0] != katcp.Message.OK:
            raise RuntimeError('Could not read meta information '
                               'from %s' % self.host)
        metalist = []
        for inform in informs:
            if len(inform.arguments) < 4:
                if len(inform.arguments) == 3:
                    self.logger.warn('Incorrect number of meta inform '
                                'arguments, missing value '
                                'field: %s' % str(inform.arguments))
                    inform.arguments.append('-1')
                else:
                    self.logger.error('FEWER than THREE meta inform '
                                 'arguments: %s' % str(inform.arguments))
                    continue
            for arg in inform.arguments:
                arg = arg.replace('\_', ' ')
            name = inform.arguments[0].decode()
            tag = inform.arguments[1].decode()
            param = inform.arguments[2].decode()
            value = [x.decode() for x in inform.arguments[3:]]
            if len(value) == 1:
                value = value[0]
            name = name.replace('/', '_')
            metalist.append((name, tag, param, value))
        self._process_git_info(metalist)
        return create_meta_dictionary(metalist)

    def _read_coreinfo_from_host(self):
        """
        Get the equivalent of coreinfo.tab from the host using
        KATCP listdev commands.
        """
        self.logger.debug('%s: reading coreinfo' % self.host)
        memorymap_dict = {}
        listdev_size = self.listdev(getsize=True)
        listdev_address = self.listdev(getaddress=True)
        if len(listdev_address) != len(listdev_size):
            raise RuntimeError('Different length listdev(size) and '
                               'listdev(detail)')
        for byte_dev, byte_size in listdev_size:
            matched = False
            for addrdev, address in listdev_address:
                if addrdev == byte_dev:
                    byte_size = int(byte_size.split(':')[0])
                    address = int(address.split(':')[0], 16)
                    memorymap_dict[byte_dev] = {
                        'address': address, 'bytes': byte_size
                    }
                    matched = True
                    continue
            if not matched:
                raise RuntimeError('No matching listdev address for '
                                   'device %s' % byte_dev)
        return memorymap_dict

    def get_system_information_from_transport(self):
        """

        """
        if not self.is_running():
            return self.bitstream, None
        device_dict = self._read_design_info_from_host()
        memorymap_dict = self._read_coreinfo_from_host()
        return self.bitstream, (device_dict, memorymap_dict)

    def unhandled_inform(self, msg):
        """
        Overloaded from CallbackClient
        
        What do we do with unhandled KATCP inform messages that
        this device receives?

        Pass it onto the registered function, if it's not None
        """
        if self.unhandled_inform_handler is not None:
            self.unhandled_inform_handler(msg)

    def check_phy_counter(self):
        """

        """
        request_args = ((0, 0), (0, 1), (1, 0), (1, 1))
        for arg in request_args:
            result0 = self.katcprequest('phywatch', request_args=arg)
            time.sleep(1)
            result1 = self.katcprequest('phywatch', request_args=arg)
            if (int(result1[0].arguments[1].replace('0x', ''), base=16) -
                int(result0[0].arguments[1].replace('0x', ''), base=16)) != 0:
                self.logger.info('%s: check_phy_counter - TRUE.' % self.host)
                return True
            else:
                self.logger.error('%s: check_phy_counter failed on PHY %s - '
                             'FALSE.' % (self.host, arg))
                return False

# end
