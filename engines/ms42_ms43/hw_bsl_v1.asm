; HW BSL revision 1. Cold C167 entry; never calls or executes flash.
; F600..F9FF loader, FC00 register bank, FB00..FBFF stack, E000 bulk agent.
; Verify this complete loader before EBC setup/JEDEC; trailer is CRC16LE.
base 0xF600
entry:
        diswdt
        mov PSW,#0
        mov CP,#0xfc00
        nop
        mov DPP0,#0
        mov DPP1,#1
        mov DPP2,#2
        mov DPP3,#3
        mov SP,#0xfc00
        mov STKOV,#0xfb00
        mov STKUN,#0xfc00
        mov r14,#0xf600
        mov r13,#0x3fe
        mov r10,#0xffff
self_crc:
        mov r4,#0
        movb RL4,[r14]
        calla cc_UC,crc_byte
        add r14,#1
        sub r13,#1
        jmpr cc_NE,self_crc
        cmp r10,0xf9fe
        jmpa cc_NE,reject_early
        mov 0xff12,#0xe206
@BUS_SETUP@
        ; Native resident T6 rollover clocks T1 once per inferred millisecond.
        mov 0xff48,#0
        mov 0xff50,#0
        mov 0xfe48,#@TIMER_RELOAD@
        mov 0xfe4a,#@TIMER_RELOAD@
        mov 0xfe52,#0
        mov 0xfe56,#0
        mov 0xff48,#0x80c0
        mov 0xff50,#0x4800
        bset 0xffc4.10
        bset 0xffc6.10
        bclr 0xffc6.11
        bclr S0CON.8
        bclr S0CON.9
        bclr S0CON.10
        einit
        ; Only autoselect/reset commands precede host write admission.
        mov r4,#0xf0
        calla cc_UC,flash_single
        mov r4,#0x90
        calla cc_UC,flash_command
        exts 0x08,#2
        mov r8,0
        mov r9,2
        mov r4,#0xf0
        calla cc_UC,flash_single
        mov r10,#0xffff
        mov r4,#0x48
        calla cc_UC,tx_crc
        mov r4,#0x42
        calla cc_UC,tx_crc
        mov r4,#0x34
        calla cc_UC,tx_crc
        mov r4,#@FAMILY_BYTE@
        calla cc_UC,tx_crc
        mov r4,#1
        calla cc_UC,tx_crc
        mov r4,r8
        and r4,#0xff
        calla cc_UC,tx_crc
        mov r4,r8
        shr r4,#8
        calla cc_UC,tx_crc
        mov r4,r9
        and r4,#0xff
        calla cc_UC,tx_crc
        mov r4,r9
        shr r4,#8
        calla cc_UC,tx_crc
        mov r4,r10
        shr r4,#8
        calla cc_UC,tx
        mov r4,r10
        and r4,#0xff
        calla cc_UC,tx
        mov r10,#0xffff
        mov r14,#0xe000
        mov r13,#@AGENT_LENGTH@
body_rx:
        calla cc_UC,rx
        movb [r14],RL4
        calla cc_UC,crc_byte
        add r14,#1
        sub r13,#1
        jmpr cc_NE,body_rx
        calla cc_UC,rx
        mov r15,r4
        shl r15,#8
        calla cc_UC,rx
        or r15,r4
        cmp r15,r10
        jmpa cc_NE,reject
        cmp r10,#@AGENT_CRC@
        jmpa cc_NE,reject
        mov r4,#0x4b
        calla cc_UC,tx
        calla cc_UC,rx
        cmp r4,#0x47
        jmpa cc_NE,reject
        bclr S0CON.4
        mov r5,0xfe52
baud_wait:
        mov r4,0xfe52
        sub r4,r5
        cmp r4,#50
        jmpr cc_C,baud_wait
        bclr S0CON.15
        mov 0xfeb4,#@BAUD_RELOAD@
        mov S0CON,#0x8011
        bclr S0RIC.7
        jmps 0x00e000
crc_byte:
        xor r10,r4
        mov r11,#8
crc_bit:
        shr r10,#1
        jmpr cc_NC,crc_next
        xor r10,#0xa001
crc_next:
        sub r11,#1
        jmpr cc_NE,crc_bit
        ret
tx_crc:
        calla cc_UC,crc_byte
        calla cc_UC,tx
        ret
tx:
        bclr S0CON.4
        bclr S0TIC.7
        movb S0TBUF,RL4
        mov r5,0xfe52
tx_wait:
        jb S0TIC.7,tx_done
        mov r4,0xfe52
        sub r4,r5
        cmp r4,#500
        jmpr cc_C,tx_wait
        jmpa cc_UC,failed
tx_done:
        ret
rx:
        bset S0CON.4
        mov r5,0xfe52
rx_wait:
        mov r4,S0CON
        and r4,#0x0700
        jmpa cc_NE,reject
        jb S0RIC.7,rx_ready
        mov r4,0xfe52
        sub r4,r5
        cmp r4,#5000
        jmpr cc_C,rx_wait
        jmpa cc_UC,reject
rx_ready:
        movbz r4,S0RBUF
        bclr S0RIC.7
        ret
flash_command:
        bset 0xff14.10
        mov r5,#0xaa
        exts 0x10,#1
        mov 0xaaaa,r5
        mov r5,#0x55
        exts 0x10,#1
        mov 0x5554,r5
        exts 0x10,#1
        mov 0xaaaa,r4
        bclr 0xff14.10
        ret
flash_single:
        bset 0xff14.10
        exts 0x10,#1
        mov 0xaaaa,r4
        bclr 0xff14.10
        ret
reject:
        mov SP,#0xfc00
        mov r4,#0x5a
        calla cc_UC,tx
        jmpa cc_UC,failed
reject_early:
        ; Timer is deliberately not configured before the loader CRC passes.
        bclr S0CON.4
        bclr S0TIC.7
        mov S0TBUF,#0x5a
failed:
        bclr S0CON.4
        jmpr cc_UC,failed
