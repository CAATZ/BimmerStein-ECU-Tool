; Persistent Soft-BSL 0x5A loader: CRC16 verifier.
; File 0x5C32..0x5C6F, CPU 0x1C32..0x1C6F.  AMD driver data ends at
; file 0x5C31, so this placement composes on both Intel and AMD images.
base 0x1C32

crc16_check:
        mov r7,r5
        mov r5,#0xD800
        mov r6,#0xFFFF
cc_byte: cmp r5,r7
        jmpr cc_EQ,cc_cmp
        movb RL4,[r5]
        movbz r4,RL4
        xor r6,r4
        mov r13,#8
cc_bit: shr r6,#1
        jmpr cc_NC,cc_nx
        xor r6,#0xA001
cc_nx:  sub r13,#1
        jmpr cc_NE,cc_bit
        add r5,#1
        jmpr cc_UC,cc_byte
cc_cmp: movbz r4,0xE427
        shl r4,#8
        movbz r5,0xE428
        or r4,r5
        cmp r6,r4
        jmpr cc_NE,cc_bad
        movb RL4,#0
        rets
cc_bad: movb RL4,#1
        rets
