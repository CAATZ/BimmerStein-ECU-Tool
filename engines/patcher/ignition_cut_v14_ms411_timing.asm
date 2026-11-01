; Ignition V14 shared absolute timing, both native final-event paths.
; UPDATE yields the selected raw angle or FF; cut request state is independent.
base 0x3F7C0
        push r4
        calls 0x3FE40
        cmpb RL4,#63
        jmpr cc_UGT,stock_split
        mov r2,#255
        subb RL2,RL4
        movb RL4,#31
        orb 0xE848,RL4
stock_split:
        pop r4
        mov r0,r2
        andb RL0,#0x0F
        rets
