from typing import Sequence, Any, Callable, Union, Tuple, Condition
from threading import Lock
from enum import Enum
import struct

from .MDBCommunicator import MDBCommunicator, EnqueuedMessage
from .MDBCommands import MDBCommand, MDBSubcommand, MDBMessageCreator


class MDBState(Enum):
    RESET        = 1
    DISABLED     = 2
    ENABLED      = 3
    SESSION_IDLE = 4
    VEND         = 5


class MDBVendAction(Enum):
    DENY    = 2
    APPROVE = 3


class MDBVendRequest():
    _counter = 0

    def __init__(self, slot: int):
        self.id = self._counter
        self.slot = slot
        self._counter += 1

class MDBHandler():

    def __init__(self, pi: Any, rx_gpio: int, tx_gpio: int, state_changed_condition: Condition, address: int = 0x10):
        self.communicator = MDBCommunicator(pi, rx_gpio, tx_gpio, self._handle_frame, address)
        self.state = MDBState.RESET
        self.state_lock = Lock()
        self.vend_request = None


    def start(self) -> None:
        with self.state_lock:
            self.state = MDBState.RESET
        self.communicator.start()


    def exit(self) -> None:
        self.communicator.exit()


    def __del__(self):
        self.communicator.exit()


    def reset(self) -> None:
        with self.state_lock:
            self.state = MDBState.RESET


    def open_session(self, display_content: Union[Sequence[int], None] = None, time: int = 6000) -> None:
        with self.state_lock:
            if (self.state == MDBState.ENABLED):

                def callback(successful: bool) -> bool:
                    if successful:
                        self._set_state(MDBState.SESSION_IDLE)
                    return not successful

                self.communicator.enqueue_message(EnqueuedMessage(MDBMessageCreator.sessionStart(), self.communicator.enqueue_message(EnqueuedMessage(MDBMessageCreator.vendDeny(), callback))))
                if display_content is not None:
                    self.update_display(display_content, time)


    def update_display(self, content: Sequence[int], time: int = 6000) -> None:
        # time in milliseconds
        with self.state_lock:
            if self.state == MDBState.SESSION_IDLE or self.state == MDBState.VEND:
                self.communicator.enqueue_message(EnqueuedMessage(MDBMessageCreator.sessionDisplayRequest(time, content)))


    def cancel_session(self) -> None:
        with self.state_lock:
            if (self.state == MDBState.SESSION_IDLE or self.state == MDBState.VEND):
                self.communicator.enqueue_message(EnqueuedMessage(MDBMessageCreator.sessionCancel()))


    def close_session(self) -> None:
        with self.state_lock:
            if (self.state == MDBState.SESSION_IDLE or self.state == MDBState.VEND):

                def callback(successful: bool) -> bool:
                    if successful:
                        self._set_state(MDBState.ENABLED)
                    return not successful

                self.communicator.enqueue_message(EnqueuedMessage(MDBMessageCreator.sessionEnd(), callback))


    def get_vend_request(self) -> Union[MDBVendRequest, None]:
        return self.vend_request


    def get_state(self) -> MDBState:
        return self.state


    def _set_state(self, state: MDBState) -> None:
        with self.state_lock:
            self.state = state
            self.state_changed_condition.notifyAll()


    # ############################################################################### #
    # TODO: ......................................................................... #
    def _handle_frame(self, command: int, length: int, frame: Sequence[int]) -> None:
        if command == MDBCommand.POLL:
            if self.state == MDBState.RESET:
                if self.communicator.send_message(MDBMessageCreator.justReset()):
                    self._set_state(MDBState.DISABLED)
            elif self.state != MDBState.DISABLED and self.communicator.has_enqueued_messages():
                self.communicator.send_enqueued_messages()
            else:
                self.communicator.send_ack()
        elif command == MDBCommand.VEND:
            if frame[1] == MDBSubcommand.VEND_REQUEST:
                if self.state == MDBState.SESSION_IDLE:
                    slot = struct.unpack('>H', frame[4:6])[0]
                    self.vend_request = MDBVendRequest(slot)
                    self._set_state(MDBState.VEND)
                self.communicator.send_ack()
            elif frame[1] == MDBSubcommand.VEND_CANCEL:
                if self.state == MDBState.VEND:

                    def callback(successful: bool) -> bool:
                        if successful:
                            self._set_state(MDBState.SESSION_IDLE)
                        return not successful

                    self.vend_request = None
                    self.communicator.send_ack()
                    self.communicator.enqueue_message(EnqueuedMessage(MDBMessageCreator.vendDeny(), callback))
                else:
                    # TODO: out-of-sequence message!state_changed_condition
                    pass
            elif frame[1] == MDBSubcommand.VEND_SUCCESS:
                if self.state == MDBState.VEND:
                    self.communicator.send_ack()
                    self._set_state(MDBState.SESSION_IDLE)
                    # TODO: report dispensed product now!
                else:
                    # TODO: out-of-sequence message!
                    pass
            elif frame[1] == MDBSubcommand.VEND_FAILURE:
                if self.state == MDBState.VEND:
                    self.communicator.send_ack()
                    self._set_state(MDBState.SESSION_IDLE)
                else:
                    # TODO: out-of-sequence message!
            elif frame[1] == MDBSubcommand.VEND_SESSION_COMPLETE:
                if self.state == MDBState.SESSION_IDLE:

                    def callback(successful: bool) -> bool:
                        if successful:
                            self._set_state(MDBState.ENABLED)
                        return not successful

                    self.communicator.send_ack()
                    self.communicator.enqueue_message(EnqueuedMessage(MDBMessageCreator.sessionEnd(), callback))
                else:
                    #TODO: out-of-sequence message!
                    pass
        elif command == MDBCommand.READER:
            if frame[1] == MDBSubcommand.READER_ENABLE:
                if self.state == MDBState.DISABLED:
                    self.communicator.send_ack()
                    self._set_state(MDBState.ENABLED)
            elif frame[1] == MDBSubcommand.READER_DISABLE:
                if self.state != MDBState.DISABLED:
                    self.communicator.send_ack()
                    self._set_state(MDBState.DISABLED)
            elif frame[1] == MDBSubcommand.READER_CANCEL:
                if self.state != MDBState.DISABLED:
                    self.communicator.send_message([0x08])
                    # TODO: cancel current session!
                    self._set_state(MDBState.ENABLED)
        elif command == MDBCommand.SETUP:
            if frame[1] == MDBSubcommand.SETUP_CONFIG_DATA:
                self.communicator.send_message(MDBMessageCreator.setupConfigData())
            elif frame[1] == MDBSubcommand.SETUP_MAX_MIN_PRICES:
                self.communicator.send_ack()
        elif command == MDBCommand.EXPANSION:
            if frame[1] == MDBSubcommand.EXPANSION_REQUEST_ID:
                self.communicator.send_message(MDBMessageCreator.expansionRequestId())
        elif command == MDBCommand.RESET:
            self.communicator.send_ack()
            self.reset()
