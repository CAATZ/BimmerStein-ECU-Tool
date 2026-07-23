; Persistent Soft-BSL 0x5A loader: main dispatcher/streaming path.
;
; File placement is 0x5D92..0x5DFF (CPU = file XOR 0x4000 -> 0x1D92).
; This deliberately starts after the MS41 per-unit descriptor, which may
; occupy file 0x5D36..0x5D90.  The helper routines live in two other proven-FF
; SA1 gaps so the existing calibration guard can remain at 0x5E10..0x5FC3.
;
; Helper entry points:
;   s_rx        CPU 0x1FC4 / file 0x5FC4
;   s_tx        CPU 0x1FD8 / file 0x5FD8
;   crc16_check CPU 0x1C32 / file 0x5C32
base 0x1D92

ldr:    movb RL4,0xE653
        cmpb RL4,#0x5A
        jmpr cc_NE,passthru
        movb RL4,0xE423
        cmpb RL4,#0x9C
        jmpr cc_NE,passthru
        movb RL4,0xE424
        cmpb RL4,#0x9C
        jmpr cc_NE,passthru
        bclr PSW.11
        movb RL4,#0x06
        calls 0x1FD8
        movbz r12,0xE425
        shl r12,#8
        movbz r4,0xE426
        or r12,r4
        mov r5,#0xD800
sr_lp:  cmp r12,#0
        jmpr cc_EQ,sr_crc
        calls 0x1FC4
        movb [r5],RL4
        add r5,#1
        sub r12,#1
        jmpr cc_UC,sr_lp
sr_crc: calls 0x1C32
        cmpb RL4,#0
        jmpr cc_NE,sr_bad
        movb RL4,#0x06
        calls 0x1FD8
        srvwdt
        calls 0xD800
sr_rst: jmpr cc_UC,sr_rst
sr_bad: movb RL4,#0x15
        calls 0x1FD8
        jmpr cc_UC,sr_rst
passthru:
        calls 0xA44
        rets
