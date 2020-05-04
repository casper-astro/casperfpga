from __future__ import print_function
import threading
import queue
import time
import logging
import sys
import socket

LOGGER = logging.getLogger(__name__)


class CheckCounter(object):

    def __init__(self, name, must_change=True, required=False):
        """

        :param name: the name of the counter
        :param must_change: this counter should be changing
        :param required: is this counter required?
        """
        self.name = name
        self.must_change = must_change
        self.required = required
        self.data = None
        self.changed = False


def create_meta_dictionary(metalist):
    """
    Build a meta information dictionary from a provided raw meta info list.

    :param metalist: a list of all meta information about the system
    :return: a dictionary of device info, keyed by unique device name
    """
    meta_items = {}
    try:
        for name, tag, param, value in metalist:
            if name not in meta_items:
                meta_items[name] = {}
            try:
                if meta_items[name]['tag'] != tag:
                    raise ValueError(
                        'Different tags - %s, %s - for the same item %s' % (
                            meta_items[name]['tag'], tag, name))
            except KeyError:
                meta_items[name]['tag'] = tag
            meta_items[name][param] = value
    except ValueError as e:
        for ctr, contents in enumerate(metalist):
            print(ctr, end='')
            print(contents)
        raise e
    return meta_items


def get_kwarg(field, kwargs, default=None):
    try:
        return kwargs[field]
    except KeyError:
        return default


def get_hostname(**kwargs):
    """

    :param kwargs:
    """
    host = kwargs['host']
    bitstream = get_kwarg('bitstream', kwargs)
    if ',' in host:
        host, bitstream = host.split(',')
    return host, bitstream


def parse_fpg(filename):
    """
    Read the meta information from the FPG file.

    :param filename: the name of the fpg file to parse
    :return: device info dictionary, memory map info (coreinfo.tab) dictionary
    """

    is_p2 = sys.version_info[0] < 3

    LOGGER.debug('Parsing file %s for system information' % filename)
    if filename is not None:
        fptr = open(filename, 'r')
        if is_p2:
            firstline = fptr.readline().decode('latin-1').strip().rstrip('\n')
        else:
            firstline = fptr.buffer.readline().decode('latin-1').strip().rstrip('\n')
        if firstline != '#!/bin/kcpfpg':
            fptr.close()
            raise RuntimeError('%s does not look like an fpg file we can '
                               'parse.' % filename)
    else:
        raise IOError('No such file %s' % filename)
    memorydict = {}
    metalist = []
    while True:
        if is_p2:
            line = fptr.readline().decode('latin-1').strip().rstrip('\n')
        else:
            line = fptr.buffer.readline().decode('latin-1').strip().rstrip('\n')
        #line = fptr.readline().strip().rstrip('\n')
        if line.lstrip().rstrip() == '?quit':
            break
        elif line.startswith('?meta'):
            # some versions of mlib_devel may mistakenly have put spaces
            # as delimiters where tabs should have been used. Rectify that
            # here.
            if line.startswith('?meta '):
                LOGGER.warn('An old version of mlib_devel generated %s. Please '
                            'update. Meta fields are seperated by spaces, '
                            'should be tabs.' % filename)
                line = line.replace(' ', '\t')
            # and carry on as usual.
            line = line.replace('\_', ' ').replace('?meta', '')
            line = line.replace('\n', '').lstrip().rstrip()
            #line_split = line.split('\t')
            # Rather split on any space
            line_split = line.split()
            name = line_split[0]
            tag = line_split[1]
            param = line_split[2]
            if len(line_split[3:]) == 1:
                value = line_split[3:][0]
            else:
                value = ' '.join(line_split[3:])
            # name, tag, param, value = line.split('\t')
            name = name.replace('/', '_')
            metalist.append((name, tag, param, value))
        elif line.startswith('?register'):
            if line.startswith('?register '):
                register = line.replace('\_', ' ').replace('?register ', '')
                register = register.replace('\n', '').lstrip().rstrip()
                name, address, size_bytes = register.split(' ')
            elif line.startswith('?register\t'):
                register = line.replace('\_', ' ').replace('?register\t', '')
                register = register.replace('\n', '').lstrip().rstrip()
                name, address, size_bytes = register.split('\t')
            else:
                raise ValueError('Cannot find ?register entries in '
                                 'correct format.')
            address = int(address, 16)
            size_bytes = int(size_bytes, 16)
            if name in memorydict.keys():
                raise RuntimeError('%s: mem device %s already in '
                                   'dictionary' % (filename, name))
            memorydict[name] = {'address': address, 'bytes': size_bytes}
    fptr.close()
    return create_meta_dictionary(metalist), memorydict

def get_git_info_from_fpg(fpg_file):
    """
    Method to get git info from an fpg-file's header
    :param fpg_file: filename as string
    :return: Dictionary of git_info
             - key = git-repo
             - value = git-version
    """
    git_tag = '77777_git'
    git_info_dict = None

    # This returns a tuple of dictionaries,
    # - device info dict, memory map info (coreinfo.tab) dict
    # - Git info is meta-data, and should only ever be in the
    #   first dictionary of the tuple
    fpg_header = parse_fpg(fpg_file)
    fpg_metadata = fpg_header[0]

    git_info_dict = fpg_metadata.get(git_tag, None)
    try:
        git_info_dict.pop('tag')
        return git_info_dict
    except KeyError:
        # tag: rcs entry isn't there, no worries
        return git_info_dict

def pull_info_from_fpg(fpg_file, parameter):
    """
    Pull available parameters about x-engine or f-engine from .fpg file.
    Available options for x-engine: 'x_fpga_clock', 'xeng_outbits',
    'xeng_accumulation_len'
    Available options for f-engine: 'n_chans', 'quant_format', 'spead_flavour'

    :param fpg_file: bit file path
    :param parameter: parameter string
    :return: pattern value (string)
    """
    match = []
    fpg_dict = parse_fpg(fpg_file)
    if parameter == 'x_fpga_clock':
        match = str(int(fpg_dict[0]['XSG_core_config']['clk_rate'])*10**6)
    if parameter == 'xeng_outbits':
        match = fpg_dict[0]['sys0_vacc']['n_bits']
    if parameter == 'xeng_accumulation_len':
        match = fpg_dict[0]['sys0_xeng']['acc_len']
    if parameter == 'spead_flavour':
        match1 = fpg_dict[0]['pack_spead_pack0']['spead_msw']
        match2 = fpg_dict[0]['pack_spead_pack0']['spead_lsw']
        s = ','
        match = s.join([match1, match2])
    if parameter == 'quant_format':
        match1 = fpg_dict[0]['snap_quant0']['io_widths']
        match2 = fpg_dict[0]['snap_quant0']['io_bps']
        s = '.'
        match = s.join([match1[1], match2[1]])
    if parameter == 'n_chans':
        pfb_dict = fpg_dict[0]['pfb_fft_wideband_real_fft_biplex_real_4x']
        match1 = int(pfb_dict['fftsize'])
        match2 = int(pfb_dict['n_inputs'])
        match = match2*2**match1
    if match is []:
        errstr = 'Parameter %s does not match any field in fpg ' \
                 'file.' % parameter
        LOGGER.error(errstr)
        raise RuntimeError(errstr)
    return match


def check_changing_status(counters, data_function, wait_time, num_checks):
    """
    Check a changing set of status fields.

    :param counters: a list of CheckCounters
    :param data_function: a function that will return a single value for the
        fields from field_dict
    :param wait_time: seconds to wait between calls to data_function
    :param num_checks: times to run data_function
    """
    # fields = [
    #     CheckCounter('test_required_diff', False, True),
    #     CheckCounter('test_required_same', True, True),
    #     CheckCounter('test_diff', False, True),
    #     CheckCounter('test_same', True, True),
    # ]
    #
    # def get_data():
    #     res = {}
    #     res['test_required_diff'] = time.time()
    #     res['test_required_same'] = 123456
    #     res['test_diff'] = time.time()
    #     res['test_same'] = 654321
    #     return res
    if num_checks < 2:
        raise ValueError('num_checks of less than two makes no sense')
    # check that required data fields are returned by the data function
    change_required = {}
    d = data_function()
    to_remove = []
    for checkctr in counters:
        if checkctr.name not in d:
            if checkctr.required:
                return False, 'required field %s not found' % checkctr.name
            to_remove.append(checkctr.name)
        else:
            checkctr.data = d[checkctr.name]
    for name in to_remove:
        for idx, checkctr in enumerate(counters):
            if checkctr.name == name:
                counters.pop(idx)
                break
    time.sleep(wait_time)
    for loop in range(num_checks - 1):
        dnew = data_function()
        for checkctr in counters:
            ctrnew = dnew[checkctr.name]
            if checkctr.must_change:
                # must change
                if ctrnew != checkctr.data:
                    checkctr.changed = True
            else:
                # must stay the same
                if ctrnew != checkctr.data:
                    return False, '%s changing: %.3f > %.3f' % (
                        checkctr.name, checkctr.data, ctrnew)
        time.sleep(wait_time)
    for checkctr in counters:
        if checkctr.must_change and (not checkctr.changed):
            return False, '%s is not changing: %.3f' % (
                checkctr.name, checkctr.data)
    return True, ''


def program_fpgas(fpga_list, progfile, timeout=10):
    """
    Program more than one FPGA at the same time.

    :param fpga_list: a list of objects for the FPGAs to be programmed
    :param progfile: string, the file used to program the FPGAs
    :param timeout: how long to wait for a response, in seconds
    """
    stime = time.time()
    if progfile is None:
        for fpga in fpga_list:
            try:
                tuplen = len(fpga)
                fpga[0].bitstream = fpga[1]
            except TypeError:
                pass
    else:
        for fpga in fpga_list:
            fpga.bitstream = progfile
    threaded_fpga_function(fpga_list, 60, 'upload_to_ram_and_program')
    LOGGER.info('Programming %d FPGAs took %.3f seconds.' % (
        len(fpga_list), time.time() - stime))


def threaded_create_fpgas_from_hosts(host_list, fpga_class=None,
                                     port=7147, timeout=10,
                                     best_effort=False, **kwargs):
    """
    Create KatcpClientFpga objects in many threads, Moar FASTAAA!

    :param fpga_class: the class to insantiate, usually CasperFpga
    :param host_list: a comma-seperated list of hosts
    :param port: the port on which to do network comms
    :param timeout: how long to wait, in seconds
    :param best_effort: return as many hosts as it was possible to make
    """
    if fpga_class is None:
        from .casperfpga import CasperFpga
        fpga_class = CasperFpga

    num_hosts = len(host_list)
    result_queue = queue.Queue(maxsize=num_hosts)
    thread_list = []

    def makehost(hostname):
        result_queue.put_nowait(fpga_class(hostname, port, **kwargs))

    for host_ in host_list:
        thread = threading.Thread(target=makehost, args=(host_,))
        thread.daemon = True
        thread.start()
        thread_list.append(thread)
    for thread_ in thread_list:
        thread_.join(timeout)
    fpgas = [None] * num_hosts
    hosts_missing = host_list[:]
    while True:
        try:
            result = result_queue.get_nowait()
            host_pos = host_list.index(result.host)
            fpgas[host_pos] = result
            hosts_missing.pop(hosts_missing.index(result.host))
        except queue.Empty:
            break
    if hosts_missing:
        for host in hosts_missing:
            LOGGER.error('Could not create host %s.' % host)
        errstr = 'Given %d hosts, only made %d CasperFpgas.' % (
            num_hosts, num_hosts-len(hosts_missing))
        LOGGER.error(errstr)
        if best_effort:
            rv = []
            for fpga in fpgas:
                if fpga is not None:
                    rv.append(fpga)
            return rv
        raise RuntimeError(errstr)
    return fpgas


def _check_target_func(target_function):
    """

    :param target_function:
    """
    if isinstance(target_function, str):
        return target_function, (), {}
    try:
        len(target_function)
    except TypeError:
        return target_function, (), {}
    if len(target_function) == 3:
        return target_function
    elif len(target_function) == 1:
        target_function = (target_function[0], (), {})
    elif len(target_function) == 2:
        target_function = (target_function[0], target_function[1], {})
    else:
        raise RuntimeError('target_function tuple too long? - (name, (), {})')
    return target_function


def threaded_fpga_function(fpga_list, timeout, target_function):
    """
    Thread the running of any CasperFpga function on a list of
    CasperFpga objects. Much faster.

    :param fpga_list: list of KatcpClientFpga objects
    :param timeout: how long to wait before timing out
    :param target_function: a tuple with three parts:

                            1. string, the KatcpClientFpga function to
                               run e.g. 'disconnect' for fpgaobj.disconnect()
                            2. tuple, the arguments to the function
                            3. dict, the keyword arguments to the function e.g. (func_name, (1,2,), {'another_arg': 3})
    :return: a dictionary of the results, keyed on hostname
    """
    target_function = _check_target_func(target_function)

    def dofunc(fpga, *args, **kwargs):
        try:
            rv = getattr(fpga, target_function[0])(*args, **kwargs)
            return rv
        except AttributeError:
            LOGGER.error('FPGA %s has no such function: %s' % (
                fpga.host, target_function[0]))
            raise
    return threaded_fpga_operation(
        fpga_list, timeout, (dofunc, target_function[1], target_function[2]))


def threaded_fpga_operation(fpga_list, timeout, target_function):
    """
    Thread any operation against many FPGA objects

    :param fpga_list: list of KatcpClientFpga objects
    :param timeout: how long to wait before timing out
    :param target_function: a tuple with three parts:

                            1. reference, the function object that must be
                               run - MUST take FPGA object as first argument
                            2. tuple, the arguments to the function
                            3. dict, the keyword arguments to the function e.g. (func_name, (1,2,), {'another_arg': 3})
    :return: a dictionary of the results, keyed on hostname
    """
    target_function = _check_target_func(target_function)

    def jobfunc(resultq, fpga):
        rv = target_function[0](fpga, *target_function[1], **target_function[2])
        resultq.put_nowait((fpga.host, rv))

    num_fpgas = len(fpga_list)
    result_queue = queue.Queue(maxsize=num_fpgas)
    thread_list = []
    for fpga_ in fpga_list:
        thread = threading.Thread(target=jobfunc, args=(result_queue, fpga_))
        thread.setDaemon(True)
        thread.start()
        thread_list.append(thread)
    for thread_ in thread_list:
        thread_.join(timeout)
        if thread_.isAlive():
            break
    returnval = {}
    hosts_missing = [fpga.host for fpga in fpga_list]
    while True:
        try:
            result = result_queue.get_nowait()
            returnval[result[0]] = result[1]
            hosts_missing.pop(hosts_missing.index(result[0]))
        except queue.Empty:
            break
    if hosts_missing:
        errmsg = 'Ran \'%s\' on hosts. Did not get a response ' \
                 'from %s.' % (target_function[0].__name__, hosts_missing)
        LOGGER.error(errmsg)
    return returnval


def threaded_non_blocking_request(fpga_list, timeout, request, request_args):
    """
    Make a non-blocking KatCP request to a list of KatcpClientFpgas, using
    the Asynchronous client.

    :param fpga_list: list of KatcpClientFpga objects
    :param timeout: the request timeout
    :param request: the request string
    :param request_args: the arguments to the request, as a list
    :return: a dictionary, keyed by hostname, of result dictionaries containing
        reply and informs
    """

    raise DeprecationWarning

    num_fpgas = len(fpga_list)
    reply_queue = queue.Queue(maxsize=num_fpgas)
    requests = {}
    replies = {}

    # reply callback
    def reply_cb(host, req_id):
        LOGGER.debug('Reply(%s) from host(%s)' % (req_id, host))
        reply_queue.put_nowait([host, req_id])

    # start the requests
    LOGGER.debug('Send request(%s) to %i hosts.' % (request, num_fpgas))
    lock = threading.Lock()
    for fpga_ in fpga_list:
        lock.acquire()
        req = fpga_.nb_request(request, None, reply_cb, *request_args)
        requests[req['host']] = [req['request'], req['id']]
        lock.release()
        LOGGER.debug('Request \'%s\' id(%s) to host(%s)' % (
            req['request'], req['id'], req['host']))

    # wait for replies from the requests
    timedout = False
    done = False
    while (not timedout) and (not done):
        try:
            it = reply_queue.get(block=True, timeout=timeout)
        except:
            timedout = True
            break
        replies[it[0]] = it[1]
        if len(replies) == num_fpgas:
            done = True
    if timedout:
        LOGGER.error('non_blocking_request timeout after %is.' % timeout)
        LOGGER.error(replies)
        raise RuntimeError('non_blocking_request timeout after %is.' % timeout)

    # process the replies
    returnval = {}
    for fpga_ in fpga_list:
        try:
            request_id = replies[fpga_.host]
        except KeyError:
            LOGGER.error(replies)
            raise KeyError(
                'Didn\'t get a reply for FPGA \'%s\' so the request \'%s\' '
                'probably didn\'t complete.' % (fpga_.host, request))
        reply, informs = fpga_.nb_get_request_result(request_id)
        frv = {'request': requests[fpga_.host][0],
               'reply': reply.arguments[0],
               'reply_args': reply.arguments}
        informlist = []
        for inf in informs:
            informlist.append(inf.arguments)
        frv['informs'] = informlist
        returnval[fpga_.host] = frv
        fpga_.nb_pop_request_by_id(request_id)
    return returnval


def hosts_from_dhcp_leases(host_pref=None,
                           leases_file='/var/lib/misc/dnsmasq.leases'):
    """
    Get a list of hosts from a leases file.

    :param host_pref: the prefix of the hosts in which we're interested
    :param leases_file: the file to read
    """
    hosts = []
    if host_pref is None:
        host_pref = ['roach', 'skarab']
    if not isinstance(host_pref, list):
        host_pref = [host_pref]
    with open(leases_file) as masqfile:
        masqlines = masqfile.readlines()
    for line in masqlines:
        (leasetime, mac, ip, host, mac2) = line.replace('\n', '').split(' ')
        for host_prefix in host_pref:
            if host.startswith(host_prefix):
                hosts.append(host if host != '*' else ip)
                break
    return hosts, leases_file


def deprogram_hosts(host_list):
    """

    :param host_list:
    """
    if len(host_list) == 0:
        raise RuntimeError('No good carrying on without hosts.')
    fpgas = threaded_create_fpgas_from_hosts(host_list)
    running = threaded_fpga_function(fpgas, 10, 'is_running')
    deprogrammed = []
    to_deprogram = []
    already_deprogrammed = []
    for fpga in fpgas:
        if running[fpga.host]:
            deprogrammed.append(fpga.host)
            to_deprogram.append(fpga)
        else:
            already_deprogrammed.append(fpga.host)
    running = threaded_fpga_function(to_deprogram, 10, 'deprogram')
    if len(deprogrammed) != 0:
        print('%s: deprogrammed okay.' % deprogrammed)
    if len(already_deprogrammed) != 0:
        print('%s: already deprogrammed.' % already_deprogrammed)
    threaded_fpga_function(fpgas, 10, 'disconnect')

def socket_closer(arg_caller, arg_socket):
    """
    Ref: https://docs.python.org/3/library/socket.html
    See warnings about close() and notes on shutdown().

    Shutdown and close the specified socket.
    Ignore all exceptions.

    :param arg_caller: Identity and/or context of caller.
    :param arg_socket: socket to be shutdown & closed.
    :return: nothing
    """
    LOGGER.debug("socket_closer: called from {}".format(arg_caller))
    try:
        arg_socket.shutdown(socket.SHUT_RDWR)
    except:
        pass
    try:
        arg_socket.close()
    except:
        pass

# end
