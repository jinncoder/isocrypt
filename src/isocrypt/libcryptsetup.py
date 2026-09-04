import ctypes
from ctypes.util import find_library

# https://gitlab.com/cryptsetup/cryptsetup/-/tree/master
# https://mbroz.fedorapeople.org/libcryptsetup_API/index.html#cexamples


class Cryptsetup:
    def __init__(self, device: str):
        self.failed = False
        self.device = device

        try:
            self.libcryptsetup = ctypes.CDLL(find_library("cryptsetup"), use_errno=True)
        except OSError:
            print("Are you sure libcryptsetup is installed?")

        self.libcryptsetup.crypt_format.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_void_p),
        ]

        self.cd = ctypes.c_void_p()

        r = self.libcryptsetup.crypt_init(ctypes.byref(self.cd), ctypes.c_char_p(bytes(self.device, "utf8")))

        if r < 0:
            print(f"crypt_init() failed for {self.device}")

    def format(self, password: bytes) -> bool:

        if self.failed:
            print("Something went wrong - create a new instance")
            return False

        r = self.libcryptsetup.crypt_format(
            self.cd,
            # CRYPT_LUKS2 / LUKS2 is a new LUKS format; use CRYPT_LUKS1 for
            # LUKS1
            ctypes.c_char_p(b"LUKS2"),
            ctypes.c_char_p(b"aes"),  # used cipher
            ctypes.c_char_p(b"xts-plain64"),  # used block mode and IV
            None,  # generate UUID
            None,  # generate volume key from RNG
            # 512bit key - here AES-256 in XTS mode, size is in bytes
            ctypes.c_size_t(int(512 / 8)),
            None,  # default parameters
        )

        if r < 0:
            print("crypt_format() failed on device %s\n", "/dev/loop10")
            self.libcryptsetup.crypt_free(self.cd)
            return False

        CRYPT_ANY_SLOT = (
            -1
        )  # https://mbroz.fedorapeople.org/libcryptsetup_API/group__crypt-keyslot.html#ga2efb82bfa83f67465f1a48b5088b1560

        r = self.libcryptsetup.crypt_keyslot_add_by_volume_key(
            self.cd,  # crypt context
            CRYPT_ANY_SLOT,  # just use first free slot
            None,  # use internal volume key
            0,  # unused (size of volume key)
            ctypes.c_char_p(password),  # passphrase - NULL means query
            len(password),  # size of passphrase
        )

        if r < 0:
            print("Adding keyslot failed.\n")
            self.libcryptsetup.crypt_free(self.cd)
            return False

        return True

    def __del__(self) -> None:
        self.libcryptsetup.crypt_free(self.cd)
