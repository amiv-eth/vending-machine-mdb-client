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

    # MDB Constants
    # MDB_JUST_RESET = b'\x00'
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

    def __init__(self, pi, rx_gpio, tx_gpio):
        self.pi = pi
        self.rx_gpio = rx_gpio
        self.tx_gpio = tx_gpio
        pigpio.exceptions = False # Ignore error if already set as bit bang read.
        self.pi.bb_serial_read_open(rx_gpio, 9600, 9) # Set baud rate and number of data bits here. Reading 9 data bits will read the parity bit.
        pigpio.exceptions = True
        self.state = MDBState.RESET
        self.has_pending_frame = False
        self.frame_buffer = []
        self.frame_checksum = 0
        self.frame_expected_length = 2
        self.send_buffer = []

    def run(self):
        frame = self.collect_frame()

        if frame is not None:
            self.print_frame(frame)
            self.handle_frame(frame)

    def session_open(self):
        self.send_buffer.append(b'\x03\x05\x39')

    def session_display_request(self, content):
        # will show the text for 6 seconds
        self.send_buffer.append(b'\x02\x3C' + content)

    def session_cancel(self):
        self.send_buffer.append(b'\x04')

    def session_close(self):
        self.send_buffer.append(b'\x07')

    def reset(self):
        self.state = MDBState.RESET
        self.has_pending_frame = False
        self.frame_buffer = []
        self.frame_checksum = 0
        self.frame_expected_length = 2
        self.send_buffer = []

    def stop(self):
        self.pi.bb_serial_read_close(self.rx_gpio)

    def send_ack(self):
        self.send(b'\x00')

    def send_nack(self):
        self.send(b'\xff')

    def send_data(self, data):
        self.pi.wave_clear()
        frame = []
        checksum = b'\x00'
        for i in range(0, len(data)):
            frame.append(bytes([data[i]]))
            frame.append(b'\x00') # set parity bit to zero
            checksum += bytes([data[i] % 256])
        # add checksum with parity bit set
        frame.append(checksum)
        frame.append(b'\x01')
        self.send(frame)
    
    def send(self, frame):
        self.pi.wave_add_serial(self.tx_gpio, 9600, frame, 0, 9)
        wid=self.pi.wave_create()
        self.pi.wave_send_once(wid)
        while self.pi.wave_tx_busy():
            pass
        self.pi.wave_delete(wid)

    def get_state(self):
        return self.state

    def collect_frame(self):
        (count, data) = self.pi.bb_serial_read(self.rx_gpio)
        if count:
            for pos in range(0, count, 2):
                # handle new address byte / start new frame
                if data[pos+1].to_bytes(1) is b'\x01':
                    # new address byte received. Start new frame
                    self.frame_buffer.clear()
                    self.has_pending_frame = True
                    self.frame_expected_length = 2

                # handle all received bytes
                if self.has_pending_frame and len(self.frame_buffer) < self.frame_expected_length:
                    self.frame_buffer.append(data[pos].to_bytes(1))
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
                        self.frame_checksum = (self.frame_checksum + data[pos].to_bytes(1)) % 256
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

    def print_frame(self, frame):
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

    def handle_frame(self, frame):
        address = frame[0] & b'\xF8'
        length = len(frame)
        command = frame[0] & b'\x07'

        # Only handle frames addressed to this device! Sniffing comes with revision 2!
        if address != b'\x010':
            return

        if command == self.MDB_POLL:
            if self.state == MDBState.RESET:
                self.send_data(b'\x00') # send JUST_RESET
                self.state == MDBState.DISABLED
                return
            elif self.state != MDBState.DISABLED and len(self.send_buffer) > 0:
                # send enqueued messages
                data = self.send_buffer.pop(0)
                self.send_data(data)
                if data[0] == b'\x03':
                    self.state = MDBState.SESSION_IDLE
                elif data[0] == b'\x04' or data[0] == b'\x07':
                    self.state = MDBState.ENABLED
            else:
                self.send_ack()
        elif command == MDBCommand.READER:
            if frame[1] == MDBSubcommand.READER_ENABLE:
                if self.state == MDBState.DISABLED:
                    self.send_ack()
                    return
            elif frame[1] == MDBSubcommand.READER_DISABLE:
                if self.state != MDBState.DISABLED:
                    self.send_ack()
                    return
            elif frame[1] == MDBSubcommand.READER_CANCEL:
                if self.state != MDBState.DISABLED:
                    self.send_data(b'\x08')
                    # TODO: cancel current session!
                    self.state = MDBState.ENABLED
        elif command == MDBCommand.SETUP:
            if frame[1] == MDBSubcommand.SETUP_CONFIG_DATA:
                self.send_data(b'\x01\x01\x02\xF4\x01\x02\x02\x02')
                return
            elif frame[1] == MDBSubcommand.SETUP_MAX_MIN_PRICES:
                self.send_ack()
                return
        elif command == MDBCommand.EXPANSION:
            if frame[1] == MDBSubcommand.EXPANSION_REQUEST_ID:
                self.send_data(b'\x09\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00')
                return
        elif command == MDBCommand.RESET:
            self.send_ack()
            self.reset()
            return
