# coding=utf-8
# Distributed under the MIT software license, see the accompanying
# file LICENSE or http://www.opensource.org/licenses/mit-license.php.
import struct
from typing import Callable

from pyqrllib.pyqrllib import bin2hstr  # noqa
from twisted.internet.protocol import Protocol, connectionDone

from qrl.core.misc import logger
from qrl.core.p2pObservable import P2PObservable
from qrl.generated import qrllegacy_pb2


# Rename to p2p channel
class P2PProtocol(Protocol):
    def __init__(self):
        self._buffer = bytes()

        # Need to use composition instead of inheritance here
        self._observable = P2PObservable(self)
        self.peer_manager = None
        self.p2pchain_manager = None
        self.tx_manager = None

    @property
    def peer_ip(self):
        return self.transport.getPeer().host

    @property
    def peer_port(self):
        return self.transport.getPeer().port

    @property
    def host_ip(self):
        return self.transport.getHost().host

    @property
    def connection_id(self):
        return "{}:{}".format(self.peer_ip, self.peer_port)

    def register(self, message_type, func: Callable):
        self._observable.register(message_type, func)

    def connectionMade(self):
        if self.factory.add_connection(self):

            self.peer_manager = self.factory._qrl_node.peer_manager
            self.p2pchain_manager = self.factory._qrl_node.p2pchain_manager
            self.tx_manager = self.factory._qrl_node.tx_manager

            # Inform about new channel
            self.peer_manager.new_channel(self)
            self.p2pchain_manager.new_channel(self)
            self.tx_manager.new_channel(self)

            self.send_peer_list()
            self.send_version_request()

    def connectionLost(self, reason=connectionDone):
        logger.debug('%s disconnected. remainder connected: %d',
                     self.peer_ip,
                     self.factory.connections)

        self.factory.remove_connection(self)
        if self.peer_manager:
            self.peer_manager.remove_channel(self)

    def dataReceived(self, data: bytes) -> None:
        self._buffer += data
        for msg in self._parse_buffer():
            self._observable.notify(msg)

    def send(self, message: qrllegacy_pb2.LegacyMessage):
        self.transport.write(self._wrap_message(message))

    def loseConnection(self):
        self.transport.loseConnection()

    ###################################################
    ###################################################
    ###################################################
    ###################################################
    # Low-level serialization/connections/etc
    # FIXME: This is a temporary refactoring, it will be completely replaced before release

    @staticmethod
    def _wrap_message(protobuf_obj) -> bytes:
        """
        Receives a protobuf object and encodes it as (length)(data)
        :return: the encoded message
        :rtype: bytes
        >>> veData = qrllegacy_pb2.VEData(version="version", genesis_prev_hash=b'genesis_hash')
        >>> msg = qrllegacy_pb2.LegacyMessage(func_name=qrllegacy_pb2.LegacyMessage.VE, veData=veData)
        >>> bin2hstr(P2PProtocol._wrap_message(msg))
        '000000191a170a0776657273696f6e120c67656e657369735f68617368'
        """
        # FIXME: This is not the final implementation, it is just a workaround for refactoring
        # FIXME: struct.pack may result in endianness problems
        # NOTE: This temporary approach does not allow for alignment. Once the stream is off, it will need to clear
        data = protobuf_obj.SerializeToString()
        str_data_len = struct.pack('>L', len(data))
        return str_data_len + data

    def _parse_buffer(self):
        # FIXME: This parsing/wire protocol needs to be replaced
        """
        >>> from pyqrllib.pyqrllib import hstr2bin
        >>> p=P2PProtocol()
        >>> p._buffer = bytes(hstr2bin('000000191a170a0776657273696f6e120c67656e657369735f68617368'+ \
                                       '000000191a170a0776657273696f6e120c67656e657369735f68617368'))
        >>> messages = p._parse_buffer()
        >>> len(list(messages))
        2
        """
        while self._buffer:
            # FIXME: This is not the final implementation, it is just a minimal implementation for refactoring
            if len(self._buffer) < 4:
                # Buffer is still incomplete as it doesn't have message size
                return

            chunk_size_raw = self._buffer[:4]
            chunk_size = struct.unpack('>L', chunk_size_raw)[0]  # is m length encoded correctly?

            # FIXME: There is no limitation on the buffer size or timeout
            if len(self._buffer) < chunk_size:
                # Buffer is still incomplete as it doesn't have message
                return

            try:
                message_raw = self._buffer[4:4 + chunk_size]
                message = qrllegacy_pb2.LegacyMessage()
                message.ParseFromString(message_raw)
                yield message
            except Exception as e:
                logger.warning("Problem parsing message. Skipping")
            finally:
                self._buffer = self._buffer[4 + chunk_size:]

    ###################################################
    ###################################################
    ###################################################
    ###################################################

    # FIXME: Take this out define the peer or leave as part of the channel object?

    def send_version_request(self):
        msg = qrllegacy_pb2.LegacyMessage(func_name=qrllegacy_pb2.LegacyMessage.VE)
        self.send(msg)

    def send_peer_list(self):
        """
        Get Peers
        Sends the peers list.
        :return:
        """
        peer_ips = self.factory.get_connected_peer_ips()

        logger.debug('<<< Sending connected peers to %s [%s]', self.peer_ip, peer_ips)

        msg = qrllegacy_pb2.LegacyMessage(func_name=qrllegacy_pb2.LegacyMessage.PL,
                                          plData=qrllegacy_pb2.PLData(peer_ips=peer_ips))

        self.send(msg)

    def send_sync(self, synced=False):
        state_str = ''
        if synced:
            state_str = 'Synced'

        msg = qrllegacy_pb2.LegacyMessage(func_name=qrllegacy_pb2.LegacyMessage.SYNC,
                                          syncData=qrllegacy_pb2.SYNCData(state=state_str))
        self.send(msg)

    def send_fetch_block(self, block_idx):
        """
        Fetch Block n
        Sends request for the block number n.
        :return:
        """
        logger.info('<<<Fetching block: %s from %s', block_idx, self.connection_id)
        msg = qrllegacy_pb2.LegacyMessage(func_name=qrllegacy_pb2.LegacyMessage.FB,
                                          fbData=qrllegacy_pb2.FBData(index=block_idx))
        self.send(msg)

    def get_headerhash_list(self):
        msg = qrllegacy_pb2.LegacyMessage(func_name=qrllegacy_pb2.LegacyMessage.HEADERHASHES,
                                          nodeHeaderHash=None)
        self.send(msg)
