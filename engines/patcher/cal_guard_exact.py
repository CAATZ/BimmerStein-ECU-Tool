"""Emit the CalGuard cave that validates exact Firmware Compatibility IDs."""

CAVE_CPU = 0x1E10
CAVE_SIZE = 0x5FC4 - 0x5E10
BOOT_EXIT = 0x0942
RECOVER_EXIT = 0x094A
POLL_COUNT = 0x3000
RECOVERY_TOKEN = (0x5A, 0x9C, 0x9C)

CC_UC, CC_EQ, CC_NE = 0, 2, 3


class _Assembler:
    """Tiny two-pass emitter for the C166 forms used by this cave."""

    def __init__(self):
        self.code = bytearray()
        self.labels = {}
        self.fixups = []
        self.bit_fixups = []

    @property
    def pc(self):
        return CAVE_CPU + len(self.code)

    def emit(self, *values):
        self.code.extend(value & 0xFF for value in values)

    def label(self, name):
        self.labels[name] = self.pc

    def mov_mem(self, reg, address):
        if address & 1:
            raise ValueError(
                f"C166 word access requires an even address, got 0x{address:04X}")
        self.emit(0xF2, 0xF0 + reg, address, address >> 8)

    def movb_mem(self, byte_reg, address):
        self.emit(0xF3, 0xF0 + byte_reg, address, address >> 8)

    def mov_mem_reg(self, address, reg):
        self.emit(0xF6, 0xF0 + reg, address, address >> 8)

    def mov_reg_imm(self, reg, value):
        self.emit(0xE6, 0xF0 + reg, value, value >> 8)

    def movb_reg_imm(self, byte_reg, value):
        if not 0 <= value <= 0xF:
            raise ValueError("compact MOVB immediate must fit four bits")
        self.emit(0xE1, (value << 4) | byte_reg)

    def mov_sfr_imm(self, address, value):
        if address & 1 or not 0xFE00 <= address <= 0xFFFE:
            raise ValueError(f"not a word-addressable SFR: 0x{address:04X}")
        self.emit(0xE6, (address - 0xFE00) // 2, value, value >> 8)

    def mov_dpp(self, dpp, value):
        self.emit(0xE6, dpp, value, value >> 8)

    def cmp(self, reg, value):
        self.emit(0x46, 0xF0 + reg, value, value >> 8)

    def cmp_rr(self, left, right):
        self.emit(0x40, (left << 4) | right)

    def cmpb(self, byte_reg, value):
        self.emit(0x47, 0xF0 + byte_reg, value, 0)

    def cmpb_rr(self, left, right):
        self.emit(0x41, (left << 4) | right)

    def jmpr(self, cc, label):
        self.emit((cc << 4) | 0x0D, 0)
        self.fixups.append((len(self.code) - 1, label))

    def jb(self, address, bit, label):
        if address & 1 or not 0xFE00 <= address <= 0xFFFE:
            raise ValueError(f"not a bit-addressable SFR: 0x{address:04X}")
        start = len(self.code)
        self.emit(0x8A, (address - 0xFE00) // 2, 0, bit << 4)
        self.bit_fixups.append((start, label))

    def bclr(self, address, bit):
        if address & 1 or not 0xFE00 <= address <= 0xFFFE:
            raise ValueError(f"not a bit-addressable SFR: 0x{address:04X}")
        self.emit((bit << 4) | 0x0E, (address - 0xFE00) // 2)

    def sub_small(self, reg, value):
        if not 0 <= value <= 7:
            raise ValueError("compact SUB immediate must fit three bits")
        self.emit(0x28, (reg << 4) | value)

    def calls(self, address):
        self.emit(0xDA, address >> 16, address, address >> 8)

    def srvwdt(self):
        self.emit(0xA7, 0x58, 0xA7, 0xA7)

    def jmpa(self, cc, address):
        self.emit(0xEA, cc << 4, address, address >> 8)

    def finish(self):
        for position, label in self.fixups:
            target = self.labels[label]
            instruction = CAVE_CPU + position - 1
            delta = target - (instruction + 2)
            if delta & 1 or not -256 <= delta <= 254:
                raise ValueError(f"relative branch out of range: {label}")
            self.code[position] = (delta // 2) & 0xFF
        for start, label in self.bit_fixups:
            target = self.labels[label]
            delta = target - (CAVE_CPU + start + 4)
            if delta & 1 or not -256 <= delta <= 254:
                raise ValueError(f"relative bit branch out of range: {label}")
            self.code[start + 2] = (delta // 2) & 0xFF
        if len(self.code) > CAVE_SIZE:
            raise ValueError(f"CalGuard is {len(self.code)} B, cave holds {CAVE_SIZE} B")
        return bytes(self.code).ljust(CAVE_SIZE, b"\xFF")


def assemble():
    """Return the 436-byte production cave, including deliberate FF padding."""
    a = _Assembler()
    rl5 = 10

    # Preserve stock E740=1 flash-listener behavior before any identification.
    a.movb_mem(rl5, 0xE740)
    a.cmpb(rl5, 1)
    a.jmpa(CC_EQ, RECOVER_EXIT)
    a.mov_dpp(0, 4)

    # A genuine SS1v2 calibration must pair with the genuine SS1v2 program.
    for address, value in ((0x33BB, 0x53), (0x33BC, 0x53), (0x33BD, 0x31),
                           (0x33BE, 0x76), (0x33BF, 0x32)):
        a.movb_mem(rl5, address)
        a.cmpb(rl5, value)
        a.jmpr(CC_NE, "not_ss1v2_cal")
    a.mov_dpp(2, 15)
    a.mov_mem(4, 0x9A9A)
    a.mov_mem(5, 0x9A9C)
    a.mov_dpp(2, 0)
    a.cmp(4, 0x119A)
    a.jmpa(CC_NE, RECOVER_EXIT)
    a.cmp(5, 0x9063)
    a.jmpa(CC_NE, RECOVER_EXIT)
    a.jmpr(CC_UC, "compare_compatibility")

    # Without the strict calibration marker the program must not be SS1v2, and
    # the compatibility suffix must be one of the supported .0/.1/.2 families.
    a.label("not_ss1v2_cal")
    a.mov_dpp(2, 15)
    a.mov_mem(4, 0x9A9A)
    a.mov_mem(5, 0x9A9C)
    a.mov_dpp(2, 0)
    a.cmp(4, 0x119A)
    a.jmpr(CC_NE, "check_legacy_suffix")
    a.cmp(5, 0x9063)
    a.jmpa(CC_EQ, RECOVER_EXIT)

    a.label("check_legacy_suffix")
    a.mov_mem(4, 0x000E)
    for suffix in (0x3231, 0x3036, 0x3134, 0x3234, 0x3935, 0x3538):
        a.cmp(4, suffix)
        a.jmpr(CC_EQ, "compare_compatibility")
    a.jmpa(CC_UC, RECOVER_EXIT)

    # DPP0=4 exposes cal 0x1400C at 0x000C; DPP2=0 exposes program
    # 0x06007 at 0xA007. The program ID starts at an odd address, so compare
    # bytes: a C166 word access at 0xA007/0xA009 raises the ILLOPA trap.
    a.label("compare_compatibility")
    rl4, rl5 = 8, 10
    for cal_address, program_address in zip(
            range(0x000C, 0x0010), range(0xA007, 0xA00B)):
        a.movb_mem(rl4, cal_address)
        a.movb_mem(rl5, program_address)
        a.cmpb_rr(rl4, rl5)
        a.jmpr(CC_NE, "compatibility_fail")
    a.jmpr(CC_UC, "poll_recovery")

    a.label("compatibility_fail")
    a.jmpa(CC_UC, RECOVER_EXIT)

    # The exact IDs matched, but corruption elsewhere could still stop the
    # application. Briefly open ASC0 and accept the staged-loader magic before
    # continuing normal boot. Restore every touched UART register on timeout.
    a.label("poll_recovery")
    a.mov_mem(7, 0xFEB4)       # S0BG
    a.mov_mem(8, 0xFFB0)       # S0CON
    a.mov_mem(9, 0xFF6E)       # S0RIC
    a.mov_sfr_imm(0xFEB4, 0x0026)
    a.mov_sfr_imm(0xFFB0, 0x80F7)
    a.bclr(0xFF6E, 7)
    a.movb_reg_imm(12, 0)
    a.mov_reg_imm(12, POLL_COUNT)

    a.label("poll_loop")
    a.srvwdt()
    a.sub_small(12, 1)
    a.jmpr(CC_EQ, "poll_timeout")
    a.jb(0xFF6E, 7, "poll_byte")
    a.jmpr(CC_UC, "poll_loop")

    a.label("poll_timeout")
    a.mov_mem_reg(0xFFB0, 8)
    a.mov_mem_reg(0xFEB4, 7)
    a.mov_mem_reg(0xFF6E, 9)
    a.jmpa(CC_UC, BOOT_EXIT)

    a.label("poll_byte")
    a.movb_mem(8, 0xFEB2)      # RL4 = S0RBUF
    a.bclr(0xFF6E, 7)
    a.cmpb(12, 0)
    a.jmpr(CC_EQ, "want_5a")
    a.cmpb(12, 1)
    a.jmpr(CC_EQ, "want_9c_1")
    a.cmpb(8, RECOVERY_TOKEN[2])
    a.jmpr(CC_EQ, "poll_match")
    a.jmpr(CC_UC, "poll_reset")

    a.label("want_5a")
    a.cmpb(8, RECOVERY_TOKEN[0])
    a.jmpr(CC_NE, "poll_loop")
    a.movb_reg_imm(12, 1)
    a.jmpr(CC_UC, "poll_loop")

    a.label("want_9c_1")
    a.cmpb(8, RECOVERY_TOKEN[1])
    a.jmpr(CC_NE, "poll_reset")
    a.movb_reg_imm(12, 2)
    a.jmpr(CC_UC, "poll_loop")

    a.label("poll_reset")
    a.movb_reg_imm(12, 0)
    a.jmpr(CC_UC, "poll_loop")

    a.label("poll_match")
    a.movb_reg_imm(8, 6)
    a.calls(0x1FD8)            # Soft-BSL polled TX helper: ACK the pre-arm token.
    a.jmpa(CC_UC, RECOVER_EXIT)
    return a.finish()


if __name__ == "__main__":
    print(assemble().hex())
