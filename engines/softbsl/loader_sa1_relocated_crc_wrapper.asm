; Soft-BSL upload CRC wrapper.
; File 0x4412..0x442D, CPU 0x0412..0x042D.
; r3 is scratch; the loader does not consume it after this call.
base 0x0412

softbsl_crc16_check:
        calla 0x1FEA
        mov DPP0,#4
        movb RH4,0xE427
        movb RL4,0xE428
        cmp r6,r4
        jmpr cc_NE,softbsl_crc_bad
        movb RL4,#0
        rets
softbsl_crc_bad:
        movb RL4,#1
        rets
