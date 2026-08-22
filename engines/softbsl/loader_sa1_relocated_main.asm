; Persistent Soft-BSL 0x5A loader: main dispatcher/streaming path.
;
; File placement is 0x5F8C..0x5FFB (CPU = file XOR 0x4000 -> 0x1F8C).
; This starts on the first even instruction address after the complete
; MS41.2 AIF array (file 0x5D07..0x5F8A) and ends before the bank marker.
;
; Helper entry points:
;   s_rx        CPU 0x0412 / file 0x4412
;   s_tx        CPU 0x1CA0 / file 0x5CA0
;   crc16_check CPU 0x1C32 / file 0x5C32
base 0x1F8C

ldr:    movb RL4,0xE653
        cmpb RL4,#0x5A
        jmpr cc_NE,passthru
        movb RL4,0xE423
        cmpb RL4,#0x9C
        jmpr cc_NE,passthru
        movb RL4,0xE424
        cmpb RL4,#0x9C
        jmpr cc_NE,passthru
        movbz r12,0xE425
        shl r12,#8
        movbz r4,0xE426
        or r12,r4
        ; OR already set Z from the combined 16-bit length.
        jmpr cc_EQ,sr_bad
        cmp r12,#0x800
        jmpr cc_UGT,sr_bad
        bclr PSW.11
        movb RL4,#0x06
        calls 0x1CA0
        mov r5,#0xD800
sr_lp:  calls 0x0412
        movb [r5],RL4
        add r5,#1
        sub r12,#1
        jmpr cc_NE,sr_lp
sr_crc: calls 0x1C32
        cmpb RL4,#0
        jmpr cc_NE,sr_bad
        movb RL4,#0x06
        calls 0x1CA0
        srvwdt
        calls 0xD800
sr_rst: jmpr cc_UC,sr_rst
sr_bad: movb RL4,#0x15
        calls 0x1CA0
        jmpr cc_UC,sr_rst
passthru:
        ; Tail-call the stock dispatcher body. Its RETS consumes our caller's
        ; frame exactly as CALLS 0x0A44 + RETS did.
        jmpa cc_UC,0x0A44
