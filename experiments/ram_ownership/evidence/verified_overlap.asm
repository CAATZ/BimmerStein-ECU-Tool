# The C166 opcode matrix defines 0xCC as a two-byte operand-free NOP. The
# firmware's live call target intentionally enters the second half of the
# linear-sweep MOV at file 0x036E98; 0xF5 is the unused NOP padding byte.
036e9a: ccf5           nop
