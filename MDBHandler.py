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
    RESET =     0x00
    SETUP =     0x01
    POLL =      0x02
    VEND =      0x03
    READER =    0x04
    EXPANSION = 0x07

class MDBSubcommand():
    SETUP_CONFIG_DATA =     0x00
    SETUP_MAX_MIN_PRICES =  0x01
    VEND_REQUEST =          0x00
    VEND_CANCEL =           0x01
    VEND_SUCCESS =          0x02
    VEND_FAILURE =          0x03
    VEND_SESSION_COMPLETE = 0x04
    VEND_CASH_SALE =        0x05
    READER_DISABLE =        0x00
    READER_ENABLE =         0x01
    READER_CANCEL =         0x02
    EXPANSION_REQUEST_ID =  0x00


CommandToFrameLengthMapping = {
    MDBCommand.RESET: 2,
    MDBCommand.SETUP: 7,
    MDBCommand.POLL: 2,
    MDBCommand.READER: 3,
    MDBCommand.EXPANSION: 32
}

SubcommandToFrameLengthMapping = {
    MDBCommand.VEND: {
        MDBSubcommand.VEND_REQUEST: 7,
        MDBSubcommand.VEND_CANCEL: 3,
        MDBSubcommand.VEND_SUCCESS: 5,
        MDBSubcommand.VEND_FAILURE: 3,
        MDBSubcommand.VEND_SESSION_COMPLETE: 3,
        MDBSubcommand.VEND_CASH_SALE: 7
    },
}


class MDBHandler():
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
        self.send_buffer.append([0x03, 0x05, 0x39])

    def session_display_request(self, content):
        # will show the text for 6 seconds
        self.send_buffer.append([0x02, 0x3C] + content)

    def session_cancel(self):
        self.send_buffer.append([0x04])

    def session_close(self):
        self.send_buffer.append([0x07])

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
        self.send([0x00])

    def send_nack(self):
        self.send([0xff])

    def send_data(self, data):
        self.pi.wave_clear()
        frame = []
        checksum = 0
        for i in range(0, len(data)):
            frame.append(data[i])
            frame.append(0) # set parity bit to zero
            checksum = (checksum + data[i]) % 256
        # add checksum with parity bit set
        frame.append(checksum)
        frame.append(1) # set parity bit to one
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
                if data[pos+1] == 1:
                    # new address byte received. Start new frame
                    self.frame_buffer.clear()
                    self.has_pending_frame = True
                    self.frame_checksum = 0
                    self.frame_expected_length = 2

                # handle all received bytes
                if self.has_pending_frame and len(self.frame_buffer) < self.frame_expected_length:
                    self.frame_buffer.append(data[pos])
                    frame_buffer_length = len(self.frame_buffer)
                    if frame_buffer_length == 2:
                        command = self.frame_buffer[0] & 7
                        if command in CommandToFrameLengthMapping:
                            self.frame_expected_length = CommandToFrameLengthMapping[command]
                        elif command in SubcommandToFrameLengthMapping:
                            subcommandMapping = SubcommandToFrameLengthMapping[command]
                            if self.frame_buffer[1] in subcommandMapping:
                                self.frame_expected_length = subcommandMapping[self.frame_buffer[1]]
                            else:
                                # Invalid command received! Ignore packet.
                                print('Invalid (sub-)command received! Ignoring.')
                                self.frame_buffer.clear()
                                self.has_pending_frame = False
                        else:
                            print('Unknown command ' + hex(command) + ' received!')
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

    def print_frame(self, frame):
        print('New frame received! | ', end='')
        print('Address: ', end='')
        print(hex(frame[0] & 0xf8), end=', ')
        print('Length: ', end='')
        print(len(frame), end=', ')
        print('Checksum: ', end='')
        print(hex(frame[len(frame)-1]), end=', ')
        print('Command: ', end='')
        print(hex(frame[0] & 0x07), end=', ')
        if len(frame) > 2:
            print('Data: ', end='')
            for i in range(1, len(frame)):
                print(hex(frame[i]), end='')
        print('')

    def handle_frame(self, frame):
        address = frame[0] & 0xf8
        length = len(frame)
        command = frame[0] & 0x07

        # Only handle frames addressed to this device! Sniffing comes with revision 2!
        if address != 0x10: # Address: 0x10
            return

        if command == MDBCommand.POLL:
            if self.state == MDBState.RESET:
                self.send_data([0x00]) # send JUST_RESET
                self.state == MDBState.DISABLED
                return
            elif self.state != MDBState.DISABLED and len(self.send_buffer) > 0:
                # send enqueued messages
                data = self.send_buffer.pop(0)
                self.send_data(data)
                if data[0] == 0x03:
                    self.state = MDBState.SESSION_IDLE
                elif data[0] == 0x04 or data[0] == 0x07:
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
                    self.send_data([0x08])
                    # TODO: cancel current session!
                    self.state = MDBState.ENABLED
        elif command == MDBCommand.SETUP:
            if frame[1] == MDBSubcommand.SETUP_CONFIG_DATA:
                self.send_data([0x01,0x01,0x02,0xF4,0x01,0x02,0x02,0x02])
                return
            elif frame[1] == MDBSubcommand.SETUP_MAX_MIN_PRICES:
                self.send_ack()
                return
        elif command == MDBCommand.EXPANSION:
            if frame[1] == MDBSubcommand.EXPANSION_REQUEST_ID:
                self.send_data([0x09,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00])
                return
        elif command == MDBCommand.RESET:
            self.send_ack()
            self.reset()
            return
