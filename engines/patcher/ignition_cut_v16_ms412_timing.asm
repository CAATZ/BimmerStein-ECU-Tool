; ignition_cut V16 ms412: runtime RAM D801-D804.
; Derived from ignition_cut_v14_ms412_timing.asm; only state operands are relocated.
; Ignition V14 shared absolute timing, both native final-event paths.
; UPDATE yields the selected raw angle or FF; cut request state is independent.
base 0x3E700
        push r4
        calls 0x3E800
        cmpb RL4,#63
        jmpr cc_UGT,stock_split
        mov r2,#255
        subb RL2,RL4
        movb RL4,#31
        orb 0xD802,RL4
stock_split:
        pop r4
        mov r0,r2
        andb RL0,#0x0F
        rets
