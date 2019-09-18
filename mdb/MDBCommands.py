from typing import Sequence

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


class MDBMessageCreator():

    @staticmethod
    def justReset() -> Sequence[int]:
        return [0x00]

    @staticmethod
    def setupConfigData() -> Sequence[int]:
        return [0x01,0x01,0x02,0xF4,0x01,0x02,0x02,0x02]

    @staticmethod
    def expansionRequestId() -> Sequence[int]:
        return [0x09,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00]

    @staticmethod
    def sessionStart() -> Sequence[int]:
        return [0x03, 0x05, 0x39] # Available balance: 13.37 CHF
        # return [0x03, 0xFF, 0xFF]  # Available balance: unknown

    @staticmethod
    def sessionCancel() -> Sequence[int]:
        return [0x04]

    @staticmethod
    def sessionEnd() -> Sequence[int]:
        return [0x07]

    @staticmethod
    def sessionDisplayRequest(time: int, content: Sequence[int]) -> Sequence[int]:
        # time must be in milliseconds!
        return [0x02, time / 100] + list(content)

    @staticmethod
    def vendApprove(amount: int = 0xFFFF) -> Sequence[int]:
        return [0x05, amount / 256, amount % 256]

    @staticmethod
    def vendDeny() -> Sequence[int]:
        return [0x06]
