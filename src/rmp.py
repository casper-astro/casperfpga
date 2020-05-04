"""!@package rmp UDP socket management and RMP packet encoding/decoding

This package provides functions for network initializing and basic 32 bit read/write
operations on the network attached device using RMP protocol. This is rough and minimal code
not exploiting all the RMP protocol features.
"""
import sys
import socket
import array
from struct import *
from .utils import socket_closer

class rmpNetwork():
    def __init__(self, this_ip, fpga_ip, udp_port, timeout):
        """!@brief Initialize the network

        It Opens the sockets and sets specific options as socket receive time-out and buffer size.

        @param this_ip  -- str -- Host machine IP address
        @param fpga_ip  -- str -- Network attached device IP address
        @param udp_port -- int -- UDP port
        @param timeout  -- int -- Receive Socket time-out in seconds

        Returns -- int -- socket handle
        """
        self.sock = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)  # Internet # UDP

        self.sock.settimeout(1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
        if fpga_ip == "255.255.255.255":
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind((this_ip, 0))
        self.fpga_ip = fpga_ip
        self.this_ip = this_ip
        self.remote_udp_port = udp_port
        self.timeout = timeout
        self.psn = 0
        self.reliable = 0

    def __del__(self):
        socket_closer("class rmpNetwork __del__", self.sock)

    def CloseNetwork(self):
        """!@brief Close previously opened socket.
        """
        socket_closer("class rmpNetwork CloseNetwork", self.sock)
        return

    def recvfrom_to(self, buff):
        attempt = 0
        while (attempt < self.timeout or self.timeout == 0):
            try:
                return self.sock.recvfrom(10240)
            except:
                attempt += 1
        raise NameError("UDP timeout. No answer from remote end!")

    def wr32_bulk(self, add, dat):
        """!@brief Write remote register at address add with dat.

        It transmits a write request to the remote device.

        @param add -- int -- 32 bits remote address
        @param dat -- int -- 32 bits write data
        """

        self.psn += 1
        exp_psn = self.psn

        num_pkt = len(dat)/256
        num_wr = num_pkt
        num_ack = 0
        idx = 0

        # if num_wr > 0:
        #     print "sending 1"
        #     pkt = array.array('I')
        #     pkt.append(self.psn)  # psn
        #     pkt.append(2)  # opcode
        #     pkt.append(256)  # noo
        #     pkt.append(1)  # noo
        #     pkt.append(add)  # sa
        #     for d in dat[256*idx:256*(idx+1)]:  # dat
        #         pkt.append(d)
        #     self.sock.sendto(bytes(pkt.tostring()), (self.fpga_ip, self.remote_udp_port))
        #
        #     num_wr -= 1
        #     idx += 1
        #     self.psn += 1

        if num_wr > 0:
            print("sending 1")
            pkt = array.array('I')
            pkt.append(self.psn)  # psn
            pkt.append(2)  # opcode
            pkt.append(256)  # noo
            pkt.append(add)  # sa
            for d in dat[256*idx:256*(idx+1)]:  # dat
                pkt.append(d)
            self.sock.sendto(bytes(pkt.tostring()), (self.fpga_ip, self.remote_udp_port))

            num_wr -= 1
            idx += 1
            self.psn += 1

        while num_ack != num_pkt:
            if num_wr > 0:
                pkt = array.array('I')
                pkt.append(self.psn)  # psn
                pkt.append(2)  # opcode
                pkt.append(256)  # noo
                pkt.append(add)  # sa
                for d in dat[256*idx:256*(idx+1)]:  # dat
                    pkt.append(d)
                self.sock.sendto(bytes(pkt.tostring()), (self.fpga_ip, self.remote_udp_port))
                num_wr -= 1
                idx += 1
                self.psn += 1

            data, addr = self.recvfrom_to(10240)
            data = bytes(data)
            psn = unpack('I', data[0:4])[0]
            add = unpack('I', data[4:8])[0]
            if psn != exp_psn or add != add:
               print("Bulk write PSN error!")
               return
            num_ack += 1
            exp_psn += 1

        if dat[(len(dat)/256)*256:] != []:
            self.wr32(add, dat[(len(dat)/256)*256:])

    def wr32(self, add, dat, infinite_loop = False):
        """!@brief Write remote register at address add with dat.

        It transmits a write request to the remote device.

        @param add -- int -- 32 bits remote address
        @param dat -- int -- 32 bits write data
        """
        req_add = add
        for i in range(3):
            try:

                self.psn += 1

                pkt = array.array('I')
                pkt.append(self.psn)  # psn
                pkt.append(2)  # opcode
                if type(dat) == list:
                    pkt.append(len(dat))  # noo
                else:
                    pkt.append(1)  # noo
                pkt.append(req_add)  # sa
                if type(dat) == list:
                    for d in dat:
                        pkt.append(d)
                else:
                    pkt.append(dat)  # dat

                self.sock.sendto(bytes(pkt.tostring()), (self.fpga_ip, self.remote_udp_port))
                while infinite_loop:
                    self.sock.sendto(bytes(pkt.tostring()), (self.fpga_ip, self.remote_udp_port))
                data, addr = self.recvfrom_to(10240)

                data = bytes(data)

                psn = unpack('I', data[0:4])[0]
                add = unpack('I', data[4:8])[0]

                if psn == self.psn and add == req_add:
                    return
                elif psn != self.psn:
                    print()
                    print("Failed UCP write, received wrong PSN ...")
                    print("Received: " + str(psn))
                    print("Expected: " + str(self.psn))
                    print("Retrying...")
                elif add != req_add:
                    print()
                    print("Failed UCP write, error received ...")
                    print("Requested Add: " + hex(req_add))
                    print("Received Add: " + hex(add))
                    print("Retrying...")
                    self.socket_flush()
            except:
                if self.reliable == 1:
                    print()
                    print("Failed UCP write:")
                    #print "Received: " + str(psn)
                    #print "Expected: " + str(self.psn)
                    print("Requested Add: " + hex(req_add))
                    #print "Received Add: " + hex(add)
                    print("Retrying...")
                    pass
                else:
                    print("Failed UCP write. Exiting ...")
                    sys.exit(-1)


            print("Getting Last Executed PSN...")
            last_psn = self.rd32(0x30000004)
            print("Getting Last Executed PSN...", last_psn)
            if last_psn == self.psn:
                return
            else:
                pass

        print()
        print("UCP write error")
        print("Requested Add: " + hex(req_add))
        print("Received Add: " + hex(add))
        exit(-1)


    def rd32(self, add, n=1):
        """!@brief Read remote register at address add.

        It transmits a read request and waits for a read response from the remote device.
        Once the response is received it extracts relevant data from a specific offset within the
        UDP payload and returns it. In case no response is received from the remote device
        a socket time-out occurs.

        @param add -- int -- 32 bits remote address

        Returns -- int -- read data
        """
        req_add = add
        for i in range(3):

            self.psn += 1

            try:
                pkt = array.array('I')
                pkt.append(self.psn)    # psn
                pkt.append(1)           # opcode
                pkt.append(n)           # noo
                pkt.append(req_add)     # sa

                self.sock.sendto(bytes(pkt.tostring()), (self.fpga_ip, self.remote_udp_port))

                data, addr = self.recvfrom_to(10240)

                data = bytes(data)

                psn = unpack('I', data[0:4])[0]
                add = unpack('I', data[4:8])[0]

                if psn == self.psn and add == req_add:
                    dat = unpack('I' * n, data[8:])
                    dat_list = []
                    for k in range(n):
                        dat_list.append(dat[k])
                    if n == 1:
                        return dat_list[0]
                    else:
                        return dat_list
                else:
                    print()
                    print("Failed UCP read, received wrong PSN or error detected ...")
                    print("Received: " + str(psn))
                    print("Expected: " + str(self.psn))
                    print("Requested Add: " + hex(req_add))
                    print("Received Add: " + hex(add))
                    print("Retrying...")
                    self.socket_flush()

            except:
                if self.reliable == 1:
                    print("Failed UCP read, retrying ...")
                else:
                    print("Failed UCP read, exiting ...")
                    sys.exit(-1)

        print()
        print("UCP read error")
        print("Requested Add: " + hex(req_add))
        #print "Received Add: " + hex(add)
        exit(-1)

    def socket_flush(self):
        print("Flushing UCP socket...")
        socket_closer("class rmpNetwork socket_flush", self.sock)

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # Internet # UDP

        self.sock.settimeout(1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
        if self.fpga_ip == "255.255.255.255":
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind((self.this_ip, 0))
