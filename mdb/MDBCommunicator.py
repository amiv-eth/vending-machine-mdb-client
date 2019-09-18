from threading import Thread, Lock
from typing import Sequence, Any, Callable, Union, Tuple
from collections import deque
import time
import struct
import pigpio

from .MDBCommands import MDBCommand, MDBSubcommand, MDBMessageCreator

# Mapping to get the length of the frame depending on the command only
CommandToFrameLengthMapping = {
    MDBCommand.RESET: 2,
    MDBCommand.SETUP: 7,
    MDBCommand.POLL: 2,
    MDBCommand.READER: 3,
    MDBCommand.EXPANSION: 32
}

# Mapping to get the length of the frame depending on the command and subcommand
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


MAX_RESPONSE_LENGTH = 36
NACK_TIME_DELAY = 5000000 # in ns = 5ms 


class EnqueuedMessage():
    def __init__(self, frame: Sequence[int], callback: Union[Callable[[bool], bool], None] = None):
        # callback parameter:    True => success;       False => failure
        # callback return value: True => try to resend; False => discard messages
        # NOTE: the return value is only relevant when parameter is False.
        self.frame = frame
        self.callback = callback


class MDBCommunicator(Thread):

    def __init__(self, pi: Any, rx_gpio: int, tx_gpio: int, handle_frame_function: Callable[[int, int, Sequence[int]], None], address: int = 0x10):
        super().__init__()
        self.address = address
        self.pi = pi
        self.rx_gpio = rx_gpio
        self.tx_gpio = tx_gpio
        self.handle_frame_function = handle_frame_function
        self.is_running = False
        self.queued_messages = deque()
        self.queued_messages_lock = Lock()
        self.send_lock = Lock()
        self.read_lock = Lock()

        # Ignore error if already set as bit bang read.
        pigpio.exceptions = False
        self.pi.set_mode(tx_gpio, pigpio.OUTPUT)
        # Set baud rate and number of data bits here. Reading 9 data bits will read the parity bit.
        self.pi.bb_serial_read_open(rx_gpio, 9600, 9)
        pigpio.exceptions = True

        # Initialize all internal variables
        self.reset()


    def exit(self) -> None:
        self.is_running = False


    def run(self) -> None:
        self.is_running = True

        while self.is_running:
            frame = self._collect_frame()
            if frame is not None:
                # self._print_frame(frame)
                self._handle_frame(frame)

        self._terminate()


    def reset(self) -> None:
        with self.queued_messages_lock:
            self.has_pending_frame = False
            self.frame_buffer = []
            self.frame_checksum = 0
            self.frame_expected_length = 2
            self.queued_messages.clear()
            self.send_buffer = []


    def _terminate(self):
        with self.queued_messages_lock:
            self.queued_messages.clear()

        frame = self._collect_frame()
        while frame is None or frame[0] & 0xf8 != self.address:
            frame = self._collect_frame()

        self.send_message(MDBMessageCreator.justReset())
        self.pi.bb_serial_read_close(self.rx_gpio)


    def __del__(self):
        if self.is_running:
            self._terminate()


    def enqueue_message(self, frame: EnqueuedMessage) -> None:
        if (len(frame) > MAX_RESPONSE_LENGTH):
            raise Exception('Byte length of frame exceeds the max. size of a response!')

        with self.queued_messages_lock:
            self.queued_messages.append(frame)


    def enqueue_messages(self, frames: Sequence[EnqueuedMessage]) -> None:
        for frame in frames:
            self.enqueue_message(frame)


    def has_enqueued_messages(self) -> bool:
        with self.queued_messages_lock:
            return len(self.queued_messages) > 0


    def send_enqueued_messages(self) -> None:
        with self.queued_messages_lock:
            messages = deque()
            data = []
            while (len(data) + len(self.queued_messages[0].frame) < MAX_RESPONSE_LENGTH):
                message = self.queued_messages.popleft()
                messages.append(self.queued_messages.popleft())
                data += message.frame

            if self.send_message(data):
                while len(messages) > 0:
                    message = messages.popleft()
                    if message.callback != None:
                        message.callback(True)
            else:
                resendMessages = []
                # Prepend the data which were not ACKed by the VMC to try again with the next POLL
                while len(messages) > 0:
                    message = messages.popleft()

                    if message.callback == None or message.callback(False):
                        resendMessages.append(message)

                resendMessages.reverse()
                for resendMessage in resendMessages:
                    self.queued_messages.appendleft(resendMessage)


    def send_messages(self, messages: Sequence[Sequence[int]]) -> bool:
        totalLength = 0
        for message in messages:
            totalLength += len(message)

        if totalLength > MAX_RESPONSE_LENGTH:
            raise Exception('Total byte length exceeds the max. size of a response!')

        data = []
        for message in messages:
            data += message
        return self.send_message(data)


    def send_message(self, message: Sequence[int]) -> bool:
        frame = []
        checksum = 0
        for i in range(0, len(message)):
            print(hex(message[i]), end=' ')
            frame.append(message[i])
            frame.append(0) # set parity bit to zero
            checksum = (checksum + message[i]) % 256
        # add checksum with parity bit set
        frame.append(checksum)
        frame.append(1) # set parity bit to one
        return self._send(frame)


    def send_ack(self) -> None:
        self._send([0x00, 0x01], False)


    def send_nack(self) -> None:
        print('send NACK')
        self._send([0xff, 0x01], False)


    def _send(self, frame: Sequence[int], responseExpected: bool = True) -> bool:
        with self.send_lock:
            self.pi.wave_clear()
            self.pi.wave_add_serial(self.tx_gpio, 9600, frame, 0, 9)
            wid=self.pi.wave_create()
            self.pi.wave_send_once(wid)
            while self.pi.wave_tx_busy():
                pass
            self.pi.wave_delete(wid)

            start_time = time.time_ns()

            if not responseExpected:
                return True

            while time.time_ns() - start_time < NACK_TIME_DELAY:
                (count, data) = self.pi.bb_serial_read(self.rx_gpio)

                if count > 0:
                    retValue = False
                    if (data[0] == 0x00):
                       print('ACK received! Everything is ok.')
                       retValue = True
                    elif (data[0] == 0xAA):
                        print('RET received! Retransmitting...')
                        retValue = self._send(frame)
                    elif (data[0] == 0xFF):
                        print('NACK received!')
                        retValue = False

                    if count > 2:
                        # Process additional data received after an ACK/NACK
                        self._process_received_frame_data(count-2, data[2:])

                    return retValue

            print('NACK assumed after timeout.')
            return False


    def _collect_frame(self) -> Union[Sequence[int], None]:
        with self.read_lock:
            (count, data) = self.pi.bb_serial_read(self.rx_gpio)
            return self._process_received_frame_data(count, data)


    def _process_received_frame_data(self, count: int, data: Sequence[int]) -> Union[Sequence[int], None]:
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
                        command = self.frame_buffer[0] & 0x07
                        address = self.frame_buffer[0] & 0xF8
                        if command in CommandToFrameLengthMapping:
                            self.frame_expected_length = CommandToFrameLengthMapping[command]
                        elif command in SubcommandToFrameLengthMapping:
                            subcommandMapping = SubcommandToFrameLengthMapping[command]
                            if self.frame_buffer[1] in subcommandMapping:
                                self.frame_expected_length = subcommandMapping[self.frame_buffer[1]]
                            else:
                                # Invalid command received! Ignore packet.
                                print('Invalid (sub-)command received! Ignoring. (Address: ' + hex(address) + ' | Command: ' + hex(command) + ' | Subcommand: ' + hex(self.frame_buffer[1]) + ')')
                                self.frame_buffer.clear()
                                self.has_pending_frame = False
                        else:
                            pass
                            # print('Unknown command ' + hex(command) + ' received!')
                    if frame_buffer_length < self.frame_expected_length:
                        self.frame_checksum = (self.frame_checksum + data[pos]) % 256
            if self.has_pending_frame and len(self.frame_buffer) == self.frame_expected_length:
                self.has_pending_frame = False
                if self.frame_buffer[len(self.frame_buffer)-1] == self.frame_checksum:
                    # A valid frame was received!
                    # print('Received a valid frame! YAY! Address is ' + hex(self.frame_buffer[0] & 0xf8))
                    return self.frame_buffer
                else:
                    # An invalid frame was received!
                    print('Received an invalid frame! Grr!')
                    pass
        return None


    def _print_frame(self, frame: Sequence[int]) -> None:
        address = frame[0] & 0xf8

        if address != 0x10:
            return

        print('New frame received! | ', end='')
        print('Address: ', end='')
        print(hex(address), end=', ')
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


    def _parse_frame(self, frame: Sequence[int]) -> Tuple[int, int, int]:
        address = frame[0] & 0xf8
        length = len(frame)
        command = frame[0] & 0x07

        return (address, command, length)


    def _handle_frame(self, frame: Sequence[int]) -> None:
        (address, command, length) = self._parse_frame(frame)

        # Only handle frames addressed to this device! Sniffing is planned for revision 2!
        if address != self.address:
            return

        if self.is_running:
            if self.handle_frame_function is not None:
                self.handle_frame_function(command, length, frame)
            else:
                print('self.handle_frame_function is NONE!')
        else:
            # send JUST_RESET (ensures that the VMC has a correct state of the device)
            self.send_message(MDBMessageCreator.justReset())
