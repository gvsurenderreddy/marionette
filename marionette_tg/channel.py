#!/usr/bin/env python
# coding: utf-8

import os
import sys
import threading

import twisted.internet.error
from twisted.internet import protocol
from twisted.internet import reactor
from twisted.python import log

sys.path.append('.')

import marionette_tg.conf

SERVER_IFACE = marionette_tg.conf.get("server.listen_iface")

class Channel(object):

    def __init__(self, protocol, transport_protocol):
        self.transport_protocol_ = transport_protocol
        self.protocol_ = protocol
        self.closed_ = False
        self.is_alive_ = True
        self.channel_id_ = os.urandom(4)
        self.buffer_lock_ = threading.RLock()
        self.buffer_ = ''
        self.last_buffer_ = ''
        self.model_ = None
        self.remote_host = None
        self.remote_port = None

    def appendToBuffer(self, chunk):
        with self.buffer_lock_:
            self.buffer_ += chunk

    def recv(self):
        with self.buffer_lock_:
            retval = self.buffer_
            self.last_buffer_ = self.buffer_
            self.buffer_ = ''
        return retval

    def peek(self):
        with self.buffer_lock_:
            return self.buffer_

    def send(self, data):
        if self.transport_protocol_ == 'tcp':
            self.protocol_.transport.write(data)
        else: #udp
            #TODO: Handle UDP Here
            #print "channel.send data =", repr(data)[:60]
            self.protocol_.transport.write(data, (self.remote_host, self.remote_port))
        return len(data)

    def sendall(self, data):
        return self.send(data)

    def rollback(self, n=0):
        with self.buffer_lock_:
            if n > 0:
                self.buffer_ = self.last_buffer_[-n:] + self.buffer_
            else:
                self.buffer_ = self.last_buffer_ + self.buffer_

    def is_alive(self):
        return self.is_alive_

    def is_closed(self):
        return self.closed_

    def get_channel_id(self):
        return self.channel_id_

    def close(self):
        self.closed_ = True
        self.is_alive_ = False
        if self.transport_protocol_ == 'tcp':
            self.protocol_.transport.loseConnection()
        else:
            #TODO: Should this only be done for the server?
            #TODO: This is a hack
            self.protocol_.connectionMade()


### Client async. classes

class MyUdpClient(protocol.DatagramProtocol):
    def __init__(self, host, port, callback):
        self.host = host
        self.port = port
        self.callback_ = callback

    def connectionMade(self):
        log.msg("channel.Client.connectionMade")

    def startProtocol(self):
        self.channel = Channel(self, "udp")
        self.channel.remote_host = self.host
        self.channel.remote_port = self.port
        self.callback_(self.channel)
        #TODO: Do we want connected UDP?
        self.transport.connect(self.host, self.port)
        log.msg("channel.Client: UDP Connection established %s:%d" 
                % (self.host, self.port))

    def datagramReceived(self, chunk, (host, port)):
        log.msg("channel.Client: %d bytes received" % len(chunk))
        self.channel.appendToBuffer(chunk)

    def doStop(self):
        log.msg("channel.Client.doStop: Stopping UDP connection")

class MyClient(protocol.Protocol):
    def connectionMade(self):
        log.msg("channel.Client.connectionMade")

    def dataReceived(self, chunk):
        log.msg("channel.Client: %d bytes received" % len(chunk))
        self.channel.appendToBuffer(chunk)


class MyClientFactory(protocol.ClientFactory):

    def __init__(self, callback):
        self.callback_ = callback

    def buildProtocol(self, address):
        proto = protocol.ClientFactory.buildProtocol(self, address)
        channel = Channel(proto, "tcp")
        proto.channel = channel
        self.callback_(channel)
        return proto


def open_new_channel(transport_protocol, port, callback):
    reactor.callFromThread(start_connection, transport_protocol, port, callback)

    return True

def start_connection(transport_protocol, port, callback):
    if transport_protocol == 'tcp':
        factory = MyClientFactory(callback)
        factory.protocol = MyClient
        reactor.connectTCP(SERVER_IFACE, int(port), factory)
    else: #udp
        #TODO: Handle UDP here
        reactor.listenUDP(0, MyUdpClient(SERVER_IFACE, int(port), callback))

    return True


#### Server async. classes

incoming = {}
incoming_lock = threading.RLock()
listening_sockets_ = {}

class MyServer(protocol.Protocol):

    def __init__(self, transport_protocol='tcp'):
        self.transport_protocol = transport_protocol
        log.msg("channel.Server transport_protocol: %s" % self.transport_protocol)

    def connectionMade(self):
        log.msg("channel.Server.connectionMade")
        port = int(self.transport.getHost().port)
        with incoming_lock:
            if not incoming.get(port):
                incoming[port] = []
            incoming[port].append(self)
        self.channel = Channel(self, self.transport_protocol)

    def dataReceived(self, chunk):
        self.channel.appendToBuffer(chunk)

        log.msg("channel.Server[%s]: %d bytes received" % (self.channel, len(chunk)))

    def datagramReceived(self, chunk, (host, port)):
        log.msg("channel.Server[%s]: %d bytes received" % (self.channel, len(chunk)))
        self.channel.remote_host = host
        self.channel.remote_port = port
        self.channel.appendToBuffer(chunk)

    def doStop(self):
        log.msg("channel.Server.doStop: Stopping UDP connection")

def bind(port=0):
    with incoming_lock:
        retval = start_listener(port)

    return retval

def accept_new_channel(transport_protocol, port):
    channel = None
    with incoming_lock:
        start_listener(transport_protocol, port)
        if incoming.get(port) and len(incoming.get(port))>0:
            myprotocol = incoming[port].pop(0)
            channel = myprotocol.channel

    return channel

def start_listener(transport_protocol, port):
    retval = port

    if not port or not listening_sockets_.get(port):
        try:
            if transport_protocol == 'tcp':
                factory = protocol.Factory()
                factory.protocol = MyServer
                connector = reactor.listenTCP(int(port), factory, interface=SERVER_IFACE)
            else: #udp
                #TODO: Handle UDP
                connector = reactor.listenUDP(int(port), MyServer('udp'), interface=SERVER_IFACE)
            port = connector.getHost().port
            listening_sockets_[port] = connector
            retval = port

        except twisted.internet.error.CannotListenError as e:
            retval = False

    return retval


def stop_accepting_new_channels(transport_protocol, port):
    #TODO: Handle TCP/UDP differences
    with incoming_lock:
        if listening_sockets_.get(port):
            listening_sockets_[port].stopListening()
            del listening_sockets_[port]
        if incoming.get(port):
            for channel in incoming[port]:
                channel.close()
            del incoming[port]
