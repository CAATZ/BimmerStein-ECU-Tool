"""Emit the AIF-safe CalGuard trampoline and exact-ID guard body."""

STUB_CPU = 0x1C76
STUB_PART2_CPU = 0x1C8C
STUB_FALLBACK_CPU = 0x0426
CAVE_FILE = 0x3BE00
CAVE_CPU = CAVE_FILE ^ 0x4000
CAVE_SIZE = 0x180
CAVE_MARKER_ADDRESS = 0xBF7E
CAVE_MARKER = 1
SOFTBSL_TX = 0x1CA0
BOOT_EXIT = 0x0942
BOOT_FALLBACK = STUB_FALLBACK_CPU
RECOVER_EXIT = 0x094A
POLL_COUNT = 0x3000
RECOVERY_TOKEN = (0x5A, 0x9C, 0x9C)

CC_UC, CC_EQ, CC_NE = 0, 2, 3


class _Assembler:
    """Tiny two-pass emitter for the C166 forms used by this cave."""

    def __init__(self, base=CAVE_CPU, limit=CAVE_SIZE):
        self.base = base
        self.limit = limit
        self.code = bytearray()
        self.labels = {}
        self.fixups = []
        self.bit_fixups = []

    @property
    def pc(self):
        return self.base + len(self.code)

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

    def cmp_small(self, reg, value):
        if not 0 <= value <= 7:
            raise ValueError("compact CMP immediate must fit three bits")
        self.emit(0x48, (reg << 4) | value)

    def cmp_rr(self, left, right):
        self.emit(0x40, (left << 4) | right)

    def cmpb(self, byte_reg, value):
        self.emit(0x47, 0xF0 + byte_reg, value, 0)

    def cmpb_rr(self, left, right):
        self.emit(0x41, (left << 4) | right)

    def jmpr(self, cc, label):
        self.emit((cc << 4) | 0x0D, 0)
        self.fixups.append((len(self.code) - 1, label))

    def jmpr_address(self, cc, address):
        delta = address - (self.pc + 2)
        if delta & 1 or not -256 <= delta <= 254:
            raise ValueError(f"relative branch out of range: 0x{address:05X}")
        self.emit((cc << 4) | 0x0D, (delta // 2) & 0xFF)

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

    def jmps(self, address):
        self.emit(0xFA, address >> 16, address, address >> 8)

    def mov_rr(self, destination, source):
        self.emit(0xE0, (source << 4) | destination)

    def rets(self):
        self.emit(0xDB, 0x00)

    def srvwdt(self):
        self.emit(0xA7, 0x58, 0xA7, 0xA7)

    def jmpa(self, cc, address):
        self.emit(0xEA, cc << 4, address, address >> 8)

    def finish(self):
        for position, label in self.fixups:
            target = self.labels[label]
            instruction = self.base + position - 1
            delta = target - (instruction + 2)
            if delta & 1 or not -256 <= delta <= 254:
                raise ValueError(f"relative branch out of range: {label}")
            self.code[position] = (delta // 2) & 0xFF
        for start, label in self.bit_fixups:
            target = self.labels[label]
            delta = target - (self.base + start + 4)
            if delta & 1 or not -256 <= delta <= 254:
                raise ValueError(f"relative bit branch out of range: {label}")
            self.code[start + 2] = (delta // 2) & 0xFF
        if len(self.code) > self.limit:
            raise ValueError(
                f"CalGuard fragment is {len(self.code)} B, cave holds {self.limit} B")
        return bytes(self.code)


def assemble_stub():
    """Return the three boot-local trampoline fragments keyed by file offset."""
    first = _Assembler(STUB_CPU, 10)
    first.mov_dpp(2, 15)
    first.mov_mem(4, CAVE_MARKER_ADDRESS)
    first.jmpr_address(CC_UC, STUB_PART2_CPU)

    second = _Assembler(STUB_PART2_CPU, 14)
    second.mov_dpp(2, 0)
    second.cmp_small(4, CAVE_MARKER)
    second.jmpa(CC_NE, STUB_FALLBACK_CPU)
    second.jmps(CAVE_CPU)

    fallback = _Assembler(STUB_FALLBACK_CPU, 8)
    fallback.mov_rr(12, 0)
    fallback.calls(0x0720)
    fallback.rets()
    return {
        STUB_CPU ^ 0x4000: first.finish(),
        STUB_PART2_CPU ^ 0x4000: second.finish(),
        STUB_FALLBACK_CPU ^ 0x4000: fallback.finish(),
    }


def assemble():
    """Return the program-resident guard with its end-of-write marker."""
    a = _Assembler()
    rl5 = 10

    # The untouched stock E740 branch and marker-check trampoline execute
    # before this body. DPP0=4 exposes the calibration window.
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
    a.jmpr(CC_NE, "recover")
    a.cmp(5, 0x9063)
    a.jmpr(CC_NE, "recover")
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
    a.jmpr(CC_EQ, "recover")

    a.label("check_legacy_suffix")
    a.mov_mem(4, 0x000E)
    for suffix in (0x3231, 0x3036, 0x3134, 0x3234, 0x3935, 0x3538):
        a.cmp(4, suffix)
        a.jmpr(CC_EQ, "compare_compatibility")

    a.label("recover")
    a.jmps(RECOVER_EXIT)

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
    a.jmps(RECOVER_EXIT)

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
    # Replay the six stock bytes through the segment-0 fallback trampoline.
    # Calling stock 0x0720 from CSP0 preserves its native call context.
    a.jmps(STUB_FALLBACK_CPU)

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
    a.calls(SOFTBSL_TX)        # Soft-BSL polled TX helper: ACK the pre-arm token.
    a.jmps(RECOVER_EXIT)
    code = a.finish()
    return code.ljust(CAVE_SIZE - 2, b"\xFF") + CAVE_MARKER.to_bytes(2, "little")


if __name__ == "__main__":
    print(assemble().hex())
