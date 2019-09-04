import binascii
import sys
import logging
from threading import Thread
import time
import struct
import pigpio
from enum import Enum

class MDBState(Enum):
    RESET = 1
    DISABLED = 2
    ENABLED = 3
    SESSION_IDLE = 4
    VEND = 5

class MDBCommand():
    RESET = b'\x00'
    SETUP = b'\x01'
    POLL = b'\x02'
    VEND = b'\x03'
    READER = b'\x04'
    EXPANSION = b'\x07'

class MDBSubcommand():
    SETUP_CONFIG_DATA = b'\x00'
    SETUP_MAX_MIN_PRICES = b'\x01'
    VEND_REQUEST = b'\x00'
    VEND_CANCEL = b'\x01'
    VEND_SUCCESS = b'\x02'
    VEND_FAILURE = b'\x03'
    VEND_SESSION_COMPLETE = b'\x04'
    VEND_CASH_SALE = b'\x05'
    READER_DISABLE = b'\x00'
    READER_ENABLE = b'\x01'
    READER_CANCEL = b'\x02'
    EXPANSION_REQUEST_ID = b'\x00'


CommandToFrameLengthMapping = {
    MDBCommand.SETUP: 7,
    MDBCommand.VEND: {
        MDBSubcommand.VEND_REQUEST: 7,
        MDBSubcommand.VEND_CANCEL: 3,
        MDBSubcommand.VEND_SUCCESS: 5,
        MDBSubcommand.VEND_FAILURE: 3,
        MDBSubcommand.VEND_SESSION_COMPLETE: 3,
        MDBSubcommand.VEND_CASH_SALE: 7
    },
    MDBCommand.READER: 3,
    MDBCommand.EXPANSION: 32
}


class MDBHandler():
    # Timeout in seconds
    TIMEOUT = 12
    
    # MDB2PC Constants
    MDB2PC_NAK = b'\x15'
    MDB2PC_ACK = b'\x06'
    MDB2PC_FRAME_START = b'\x02'
    MDB2PC_FRAME_BEGIN = b'\x02\x00'
    MDB2PC_FRAME_STOP = b'\x10\x03'

    # MDB Constants
    MDB_ACK = b''
    MDB_JUST_RESET = b'\x00'
    MDB_POLL = b'\x12'
    MDB_RESET = b'\x10\x10'
    MDB_READER_ENABLE = b'\x14\x01'
    MDB_OUT_OF_SEQUENCE = b'\x0B'
    MDB_READER_CONFIG_RESPONSE = b'\x01\x01\x02\xF4\x01\x02\x02\x00'
    MDB_EXT_FEATURES_RESPONSE = b'\x09\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
    MDB_VEND_REQUEST = b'\x13\x00'
    MDB_VEND_SUCCESFUL = b'\x13\x02'
    MDB_VEND_CANCEL = b'\x13\x01'
    MDB_SESSION_COMPLETE = b'\x13\x04'
    MDB_OPEN_SESSION = b'\x03\x05\x39'
    MDB_CANCEL_REQUEST = b'\x04'
    MDB_END_SESSION = b'\x07'
    MDB_VEND_DENIED = b'\x06'
    MDB_VEND_APPROVED = b'\x05\x00\x02'

    DISPLAY_WIDTH = 16

    def __init__(self, pi, rx_gpio):
        self.pi = pi
        self.rx_gpio = rx_gpio
        pigpio.exceptions = False # Ignore error if already set as bit bang read.
        self.pi.bb_serial_read_open(rx_gpio, 9600, 9) # Set baud rate and number of data bits here. Reading 9 data bits will read the parity bit.
        pigpio.exceptions = True
        self.state = MDBState.RESET
        self.has_pending_frame = False
        self.frame_buffer = []
        self.frame_checksum = 0
        self.frame_expected_length = 2

    def run(self):
        frame = self.collect_frame()
        if frame is not None:
            self.handle_frame(frame)

    def stop(self):
        self.pi.bb_serial_read_close(self.rx_gpio)

    def collect_frame(self):
        (count, data) = self.pi.bb_serial_read(self.rx_gpio)
        if count:
            for pos in range(0, count, 2):

                # handle new address byte / start new frame
                if data[pos+1] is b'\x01':
                    # new address byte received. Start new frame
                    self.frame_buffer.clear()
                    self.has_pending_frame = True
                    self.frame_expected_length = 2

                # handle all received bytes
                if self.has_pending_frame and len(self.frame_buffer) < self.frame_expected_length:
                    self.frame_buffer.append(data[pos])
                    frame_buffer_length = len(self.frame_buffer)
                    if frame_buffer_length == 2:
                        commandFrameLength = CommandToFrameLengthMapping[self.frame_buffer[0] & b'\x07']
                        if isinstance(commandFrameLength, int):
                            self.frame_expected_length = commandFrameLength
                        elif self.frame_buffer[1] in commandFrameLength:
                            self.frame_expected_length = commandFrameLength[self.frame_buffer[1]]
                        else:
                            # Invalid command received! Ignore packet.
                            print('Invalid command received! Ignoring.')
                            self.frame_buffer.clear()
                            self.has_pending_frame = False
                    if frame_buffer_length < self.frame_expected_length:
                        self.frame_checksum = (self.frame_checksum + data[pos]) % 256
            if self.has_pending_frame and len(self.frame_buffer) == self.frame_expected_length:
                # TODO: verify checksum and handle received frame
                self.has_pending_frame = False
                if self.frame_buffer[len(self.frame_buffer)-1] == self.frame_checksum:
                    # A valid frame was received!
                    print('Received a valid frame! YAY!')
                    return self.frame_buffer
                else:
                    # An invalid frame was received!
                    print('Received an invalid frame! Grr!')
                    pass
        return None

    def handle_frame(self, frame):
        print('New frame received! | ', end='')
        print('Address: ', end='')
        print((frame[0] & b'\xF8').hex(), end=', ')
        print('Length: ', end='')
        print(len(frame), end=', ')
        print('Checksum: ', end='')
        print(frame[len(frame)-1].hex(), end=', ')
        print('Command: ', end='')
        print((frame[0] & b'\x07').hex(), end=', ')
        if len(frame) > 2:
            print('Data: ', end='')
            for i in range(1, len(frame)):
                print(frame[i].hex(), end='')

    # def handle_data(self, data):
    #     if self.state == "RESET":
    #         logging.debug("STATE: RESET")
    #         if data == self.MDB_POLL:  # POLL
    #             logging.debug("MDB: [IN] Poll")
    #             self.send_data(self.MDB_JUST_RESET)
    #             self.state = "DISABLED"
    #         elif data == self.MDB_RESET:  # RESET
    #             logging.info("MDB: [IN] Reset")
    #             self.send_data(self.MDB_ACK)
    #         else:
    #             logging.warning("MDB: [IN] Unhandled Frame " + str(binascii.hexlify(data)))
    #             logging.warning("MDB: [IN] %s" % self.state)
    #             self.send_data(self.MDB_OUT_OF_SEQUENCE)
    #     elif self.state == "DISABLED":
    #         logging.debug("STATE: DISABLED")
    #         if data == self.MDB_POLL:  # POLL
    #             logging.debug("MDB: [IN] Poll")
    #             self.send_data(self.MDB_ACK)
    #         elif data == self.MDB_RESET:  # RESET
    #             logging.info("MDB: [IN] Reset")
    #             self.send_data(self.MDB_ACK)
    #             self.state = "RESET"
    #         elif data == b'\x11\x00\x03\x10\x10\x02\x01':  # SETUP CONFIG
    #             logging.debug("MDB: [IN] Setup Config")
    #             self.send_data(self.MDB_READER_CONFIG_RESPONSE)
    #         elif data == b'\x11\x01\x03\xe8\x00\x05':
    #             logging.debug("MDB: [IN] MinMax Prices")
    #             self.send_data(self.MDB_ACK)
    #         elif data == self.MDB_READER_ENABLE:
    #             logging.info("MDB: [IN] Reader Enable")
    #             self.send_data(self.MDB_ACK)
    #             self.state = "ENABLED"
    #             with self.condition:
    #                 self.condition.notify()
    #         elif data == b'\x17\x00SIE000':
    #             logging.debug("MDB: [IN] Extended Features")
    #             self.send_data(self.MDB_EXT_FEATURES_RESPONSE)
    #         else:
    #             logging.warning("MDB: [IN] Unhandled Frame " + str(binascii.hexlify(data)))
    #             logging.warning("MDB: [IN] %s" % self.state)
    #             self.send_data(self.MDB_OUT_OF_SEQUENCE)
    #     elif self.state == "ENABLED":
    #         logging.debug("STATE: ENABLED")
    #         if data == self.MDB_POLL:  # POLL
    #             logging.debug("MDB: [IN] Poll")
    #             if self.open_session == 1:
    #                 self.timer = time.time()
    #                 self.send_data(self.MDB_OPEN_SESSION)
    #                 self.state = "DISPLAY SESSION"
    #                 self.open_session = 0
    #                 self.last_amount = self.beer_available_callback(0)
    #                 #print("Amount "+ str(self.last_amount))
    #             else:
    #                 self.send_data(self.MDB_ACK)

    #         elif data == self.MDB_READER_ENABLE:
    #             logging.debug("MDB: [IN] Reader Enable")
    #             self.send_data(self.MDB_ACK)
    #         elif data == self.MDB_RESET:  # RESET
    #             logging.info("MDB: [IN] Reset")
    #             self.send_data(self.MDB_ACK)
    #             self.state = "RESET"
    #         else:
    #             logging.warning("MDB: [IN] Unhandled Frame " + str(binascii.hexlify(data)))
    #             logging.warning("MDB: [IN] %s" % self.state)
    #             #logging.debug(binascii.hexlify(data[0:2]))
    #             self.send_data(self.MDB_OUT_OF_SEQUENCE)

    #     elif self.state == "SESSION":
    #         logging.debug("STATE: SESSION")
    #         if data == self.MDB_POLL:  # POLL
    #             logging.debug("MDB: [IN] Poll")
    #             if time.time() - self.timer > self.TIMEOUT:
    #                 self.state = "SESSION END"
    #             else:
    #                 self.send_data(self.MDB_ACK)
    #         elif data[0:2] == self.MDB_VEND_REQUEST:
    #             logging.info("MDB: [IN] Vend Request")
    #             self.slot = struct.unpack('>H', data[4:6])[0]
    #             self.last_amount = self.beer_available_callback(self.slot)
    #             logging.info('self last amount %d', self.last_amount)
    #             if self.last_amount:
    #                 logging.info("MDB: [LOGIC] Request Approved, " + str(self.last_amount - 1) + " Beers left")
    #                 self.timer = time.time()
    #                 self.send_data(self.MDB_VEND_APPROVED)
    #             else:
    #                 logging.info("MDB: [LOGIC] Request Denied")
    #                 self.send_data(self.MDB_VEND_DENIED)

    #         elif data[0:2] == self.MDB_VEND_SUCCESFUL:
    #             logging.info("MDB: [IN] Vend Success")
    #             self.dispensed_callback(self.slot)
    #             self.send_data(self.MDB_CANCEL_REQUEST)
    #             self.state = "SESSION END"
    #         elif data[0:2] == self.MDB_VEND_CANCEL:
    #             # User put in coins
    #             logging.info("MDB: [IN] Vend Cancel")
    #             self.send_data(self.MDB_VEND_DENIED)
    #             self.state = "VEND CANCELED"
    #         elif data == self.MDB_RESET:  # RESET
    #             logging.info("MDB: [IN] Reset")
    #             self.send_data(self.MDB_ACK)
    #             self.state = "RESET"
    #         elif data == self.MDB_SESSION_COMPLETE:
    #             logging.info("MDB: [IN] Session Complete")
    #             self.send_data(self.MDB_ACK)
    #             self.state = "SESSION END"
    #         else:
    #             logging.warning("MDB: [IN] Unhandled Frame " + str(binascii.hexlify(data)))
    #             logging.warning("MDB: [IN] %s" % self.state)
    #             #logging.debug(binascii.hexlify(data[0:2]))
    #             self.send_data(self.MDB_OUT_OF_SEQUENCE)

    #     elif self.state == "SESSION END":
    #         logging.debug("STATE: SESSION END")
    #         if data == self.MDB_POLL:
    #             logging.debug("MDB: [IN] Poll")
    #             self.send_data(self.MDB_END_SESSION)
    #             self.state = "DISPLAY END SESSION"
    #         else:
    #             logging.warning("MDB: [IN] Unhandled Frame " + str(binascii.hexlify(data)))
    #             logging.warning("MDB: [IN] %s" % self.state)
    #             #logging.debug(binascii.hexlify(data[0:2]))
    #             self.send_data(self.MDB_OUT_OF_SEQUENCE)

    #     elif self.state == "DISPLAY SESSION":
    #         logging.debug("STATE: DISPLAY")
    #         if data == self.MDB_POLL:  # POLL
    #             logging.debug("MDB: [IN] Poll")
    #             self.send_data(b'\x02\x3C' +
    #                 b'AMIV'.center(self.DISPLAY_WIDTH) +
    #                 (str(self.last_amount).encode('ascii') + b' Freibier').center(self.DISPLAY_WIDTH))
    #             self.state = "SESSION"
    #         else:
    #             logging.warning("MDB: [IN] Unhandled Frame " + str(binascii.hexlify(data)))
    #             logging.warning("MDB: [IN] %s" % self.state)
    #             #logging.debug(binascii.hexlify(data[0:2]))
    #             self.send_data(self.MDB_OUT_OF_SEQUENCE)

    #     elif self.state == "DISPLAY END SESSION":
    #         logging.debug("STATE: DISPLAY")
    #         if data == self.MDB_POLL:  # POLL
    #             logging.debug("MDB: [IN] Poll")
    #             self.send_data(b'\x02\x0A' +
    #                 b'AMIV'.center(self.DISPLAY_WIDTH) +
    #                 b'Zum Wohl!'.center(self.DISPLAY_WIDTH))
    #             self.state = "ENABLED"
    #             if self.open_session != 1:
    #                 with self.condition:
    #                     self.condition.notify()
    #         else:
    #             logging.warning("MDB: [IN] Unhandled Frame " + str(binascii.hexlify(data)))
    #             logging.warning("MDB: [IN] %s" % self.state)
    #             #logging.debug(binascii.hexlify(data[0:2]))
    #             self.send_data(self.MDB_OUT_OF_SEQUENCE)

    #     elif self.state == "VEND CANCELED":
    #         logging.debug("STATE: VEND CANCELED")
    #         if data == self.MDB_POLL:
    #             logging.debug("MDB: [IN] Poll")
    #             self.send_data(b'\x06')
    #             self.state = "SESSION"
    #         else:
    #             logging.warning("MDB: [IN] Unhandled Frame " + str(binascii.hexlify(data)))
    #             logging.warning("MDB: [IN] %s" % self.state)
    #             #logging.debug(binascii.hexlify(data[0:2]))
    #             self.send_data(self.MDB_OUT_OF_SEQUENCE)
