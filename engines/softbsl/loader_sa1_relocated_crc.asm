; BrickGuard/Soft-BSL CRC-16 range core.
; File 0x5C52..0x5C7D, CPU 0x1C52..0x1C7D.
; The 16-entry table is file 0x5C32..0x5C51, immediately after the AMD driver.
base 0x1C52

crc_range:
        cmp r4,r5
        jmpr cc_EQ,crc_done
        movb RL3,[r4+]
        xorb RL6,RL3

        ; Two reflected four-bit folds through the aligned boot table.
        mov r3,r6
        shl r3,#12
        shr r3,#11
        mov r3,[r3+#0x1C32]
        shr r6,#4
        xor r6,r3
        mov r3,r6
        shl r3,#12
        shr r3,#11
        mov r3,[r3+#0x1C32]
        shr r6,#4
        xor r6,r3

        srvwdt
        jmpr cc_UC,crc_range
crc_done:
        ret
